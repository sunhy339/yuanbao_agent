"""Three-segment context compaction for long conversations.

The compactor splits conversation history into three segments:

1. **Primers** – system prompts and tool definitions (always kept in full).
2. **Summary** – an LLM-generated summary of the middle history.
3. **Recents** – the last *N* turns, kept verbatim.

When the total token estimate exceeds the budget, the compactor generates a
summary of the older turns and retains only the recent tail verbatim.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..store.sqlite_store import SQLiteStore
from .token_budget import estimate_tokens
from ..tools.failure_analysis import build_tool_failure_record


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CompactionResult:
    """Outcome of a compaction pass."""

    kept_messages: list[dict[str, Any]]
    tokens_before: int
    tokens_after: int
    summary: str | None = None
    strategy: str = "primer_summary_recent"
    compaction_id: str | None = None
    handoff_summary: dict[str, Any] | None = None


@dataclass(slots=True)
class CompactionDecision:
    """Decision about whether a context should be compacted."""

    should_compact: bool
    reason: str
    tokens_before: int
    max_tokens: int
    source: str = "rule"
    force: bool = False


# ---------------------------------------------------------------------------
# Provider protocol (minimal interface for summary generation)
# ---------------------------------------------------------------------------

class _CanGenerate(Protocol):
    def generate(self, prompt: str, context: dict) -> dict: ...


# ---------------------------------------------------------------------------
# Compactor
# ---------------------------------------------------------------------------

_RECENT_TURN_DEFAULT = 6
_SUMMARY_MAX_CHARS = 4000

# -- Progressive compaction thresholds (tokens) --
# Tier 1 (< TIER1): keep as-is, only compact if forced
# Tier 2 (TIER1 - TIER2): truncate tool outputs, compress logs to error summaries
# Tier 3 (>= TIER2): full compact (primer/summary/recent)
_TIER1_THRESHOLD = 50_000
_TIER2_THRESHOLD = 220_000
_STABLE_CONTEXT_MARKER = "Stable context prefix:"


class ContextCompactor:
    """Compresses a message list to fit within a token budget."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        provider: _CanGenerate | None = None,
        recent_turns: int = _RECENT_TURN_DEFAULT,
        provider_context: dict[str, Any] | None = None,
    ) -> None:
        self._store = store
        self._provider = provider
        self._recent_turns = recent_turns
        self._provider_context = provider_context or {}

    # -- public API --

    def compact(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        *,
        message_ids: list[str] | None = None,
        task_id: str | None = None,
        force: bool = False,
    ) -> CompactionResult:
        """Compact *messages* to fit within *max_tokens*.

        Uses progressive compaction:
        - Tier 1 (<50K tokens): keep as-is unless forced.
        - Tier 2 (50K-220K): truncate tool outputs, compress logs.
        - Tier 3 (>=220K): full compact (primer/summary/recent).

        Returns a ``CompactionResult`` with the retained message list and
        bookkeeping metadata.

        *message_ids* maps to the input messages for traceability.
        *task_id* is the task that triggered this compaction.
        """
        # Compute per-message tokens once
        msg_tokens = [estimate_tokens(m.get("content", "")) for m in messages]
        tokens_before = sum(msg_tokens)

        # Tier 1: within budget and not forced — no-op
        if tokens_before <= max_tokens and not force:
            return CompactionResult(
                kept_messages=list(messages),
                tokens_before=tokens_before,
                tokens_after=tokens_before,
            )

        # -- Progressive compaction --
        # If we are between TIER1 and TIER2, try lightweight truncation first.
        if tokens_before < _TIER2_THRESHOLD and not force:
            truncated = self._truncate_tool_outputs(messages, msg_tokens)
            truncated_tokens = sum(estimate_tokens(m.get("content", "")) for m in truncated)
            if truncated_tokens <= max_tokens:
                return CompactionResult(
                    kept_messages=truncated,
                    tokens_before=tokens_before,
                    tokens_after=truncated_tokens,
                    strategy="tool_output_truncation",
                )
            # Truncation alone wasn't enough; fall through to full compact.

        # --- Split into segments ---
        primers, history, recents = self._split_segments(messages)
        history, recents = self._repair_recent_tool_call_pairs(history, recents)

        primer_tokens = sum(estimate_tokens(m.get("content", "")) for m in primers)
        recent_tokens = sum(estimate_tokens(m.get("content", "")) for m in recents)
        budget_for_summary = max_tokens - primer_tokens - recent_tokens

        # Generate summary of the history segment
        summary = self._generate_summary(history, budget_for_summary)
        handoff_summary = self._build_handoff_summary(
            session_id=session_id,
            task_id=task_id,
            history=history,
            recents=recents,
            summary=summary,
        )
        handoff_text = self._format_handoff_summary(handoff_summary)

        # Reassemble
        kept: list[dict[str, Any]] = list(primers)
        if summary or handoff_text:
            content_parts: list[str] = []
            if summary:
                content_parts.append(f"[Conversation summary]\n{summary}")
            if handoff_text:
                content_parts.append(f"[Structured handoff]\n{handoff_text}")
            kept.append({
                "role": "system",
                "content": "\n\n".join(content_parts),
            })
        kept.extend(recents)

        tokens_after = sum(estimate_tokens(m.get("content", "")) for m in kept)
        primer_hash = self._hash_primers(primers)

        # Persist record
        import json as _json
        compaction_id = self._store.new_id("cmp")
        now = self._store.now()

        # Determine which message IDs were covered (history segment)
        covered_ids: list[str] = []
        if message_ids:
            msg_count = len(messages)
            ids_count = len(message_ids)
            # History messages are the ones not in primers or recents
            primer_count = len(primers)
            body_count = msg_count - primer_count
            split_point = max(0, body_count - self._recent_turns)
            # History segment covers from primer_count to primer_count + split_point
            for idx in range(primer_count, primer_count + split_point):
                if idx < ids_count:
                    covered_ids.append(message_ids[idx])

        self._store._conn.execute(
            """
            INSERT INTO compaction_records
                (id, session_id, task_id, strategy, tokens_before, tokens_after,
                 summary, primer_hash, covered_message_ids, trimmed_sections,
                 handoff_summary_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                compaction_id,
                session_id,
                task_id,
                "primer_summary_recent",
                tokens_before,
                tokens_after,
                summary,
                primer_hash,
                _json.dumps(covered_ids, ensure_ascii=False),
                _json.dumps([], ensure_ascii=False),
                _json.dumps(handoff_summary, ensure_ascii=False),
                now,
            ),
        )
        self._store._conn.commit()

        return CompactionResult(
            kept_messages=kept,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            summary=summary,
            strategy="primer_summary_recent",
            compaction_id=compaction_id,
            handoff_summary=handoff_summary,
        )

    def should_compact(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        *,
        near_budget_ratio: float = 0.92,
        force_budget_ratio: float = 1.0,
    ) -> CompactionDecision:
        """Decide whether to compact now using progressive thresholds.

        Three tiers:
        - Tier 1 (<50K): no compaction unless forced.
        - Tier 2 (50K-220K): lightweight compaction (tool output truncation).
        - Tier 3 (>=220K or over budget): full compact.

        The ``force_budget_ratio`` still controls the hard ceiling — anything
        at or above ``max_tokens * force_budget_ratio`` always compacts.
        """
        tokens_before = sum(estimate_tokens(m.get("content", "")) for m in messages)
        if max_tokens <= 0:
            return CompactionDecision(
                should_compact=True,
                reason="invalid or exhausted token budget",
                tokens_before=tokens_before,
                max_tokens=max_tokens,
                source="rule",
                force=True,
            )

        # Tier 3: over hard budget → force full compact
        if tokens_before >= int(max_tokens * force_budget_ratio):
            return CompactionDecision(
                should_compact=True,
                reason="context exceeds token budget (tier 3: full compact)",
                tokens_before=tokens_before,
                max_tokens=max_tokens,
                source="rule",
            )

        # Tier 1: well below 50K → no compaction needed
        if tokens_before < _TIER1_THRESHOLD:
            return CompactionDecision(
                should_compact=False,
                reason="context is small (tier 1: no compaction)",
                tokens_before=tokens_before,
                max_tokens=max_tokens,
                source="rule",
            )

        # Tier 2 (50K to hard budget): lightweight compaction
        # Check if we have oversized tool outputs that can be truncated first.
        if tokens_before >= _TIER1_THRESHOLD:
            has_large_tools = self._has_oversized_tool_outputs(messages)
            if has_large_tools:
                return CompactionDecision(
                    should_compact=True,
                    reason="tier 2: truncate oversized tool outputs",
                    tokens_before=tokens_before,
                    max_tokens=max_tokens,
                    source="rule",
                    force=False,
                )

        # If near budget but no large tools, ask the LLM or fall back
        if tokens_before < int(max_tokens * near_budget_ratio):
            return CompactionDecision(
                should_compact=False,
                reason="context is comfortably within token budget",
                tokens_before=tokens_before,
                max_tokens=max_tokens,
                source="rule",
            )

        llm_decision = self._llm_compaction_decision(messages, tokens_before, max_tokens)
        if llm_decision is not None:
            return llm_decision

        return CompactionDecision(
            should_compact=tokens_before >= int(max_tokens * near_budget_ratio),
            reason="near token budget; provider unavailable or undecidable",
            tokens_before=tokens_before,
            max_tokens=max_tokens,
            source="rule_fallback",
            force=tokens_before < max_tokens,
        )

    # -- internals --

    def _split_segments(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Split into (primers, history, recents).

        *Primers* are leading system messages plus the stable context prefix.
        *Recents* are the last ``_recent_turns`` non-system messages.
        *History* is everything in between.
        """
        primers: list[dict] = []
        body_start = 0
        for i, msg in enumerate(messages):
            if self._is_primer_message(msg):
                primers.append(msg)
                body_start = i + 1
            else:
                break

        body = messages[body_start:]
        split_point = max(0, len(body) - self._recent_turns)
        history = body[:split_point]
        recents = body[split_point:]
        return primers, history, recents

    @staticmethod
    def _is_primer_message(message: dict[str, Any]) -> bool:
        if message.get("role") == "system":
            return True
        content = message.get("content")
        return message.get("role") == "user" and isinstance(content, str) and content.startswith(_STABLE_CONTEXT_MARKER)

    def _repair_recent_tool_call_pairs(
        self,
        history: list[dict[str, Any]],
        recents: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Keep strict tool-call request/response pairs together.

        Some provider APIs reject a tool/function output unless the matching
        assistant tool call is still present in the request history. Recency
        splitting can otherwise leave a tail like ``tool(call-1)`` while the
        preceding assistant message that created ``call-1`` was summarized.
        """
        if not history or not recents:
            return history, recents

        repaired_history = list(history)
        repaired_recents = list(recents)
        while True:
            recent_call_ids = self._assistant_tool_call_ids(repaired_recents)
            orphan_tool_ids = [
                tool_id
                for tool_id in (self._tool_result_id(message) for message in repaired_recents)
                if tool_id and tool_id not in recent_call_ids
            ]
            if not orphan_tool_ids:
                return repaired_history, repaired_recents

            orphan_set = set(orphan_tool_ids)
            boundary: int | None = None
            for index in range(len(repaired_history) - 1, -1, -1):
                if self._message_tool_call_ids(repaired_history[index]) & orphan_set:
                    boundary = index
                    break
            if boundary is None:
                repaired_recents = [
                    message
                    for message in repaired_recents
                    if self._tool_result_id(message) not in orphan_set
                ]
                if not repaired_recents:
                    return repaired_history, repaired_recents
                continue

            repaired_recents = repaired_history[boundary:] + repaired_recents
            repaired_history = repaired_history[:boundary]

    @staticmethod
    def _assistant_tool_call_ids(messages: list[dict[str, Any]]) -> set[str]:
        ids: set[str] = set()
        for message in messages:
            ids.update(ContextCompactor._message_tool_call_ids(message))
        return ids

    @staticmethod
    def _message_tool_call_ids(message: dict[str, Any]) -> set[str]:
        if message.get("role") != "assistant":
            return set()
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            return set()
        ids: set[str] = set()
        for item in tool_calls:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
                ids.add(item["id"])
        return ids

    @staticmethod
    def _tool_result_id(message: dict[str, Any]) -> str | None:
        if message.get("role") != "tool":
            return None
        tool_call_id = message.get("tool_call_id")
        if isinstance(tool_call_id, str) and tool_call_id:
            return tool_call_id
        return None

    def _generate_summary(self, history: list[dict], token_budget: int) -> str | None:
        """Ask the LLM to summarise *history*, or return a heuristic summary."""
        if not history:
            return None

        history_text = "\n".join(
            f"[{m.get('role', '?')}] {m.get('content', '')}" for m in history
        )

        # If we have a provider, use it; otherwise fall back to a heuristic truncation
        if self._provider is not None:
            try:
                prompt = (
                    "Summarize the following conversation history concisely. "
                    "Preserve key decisions, facts, and any code references.\n\n"
                    f"{history_text[:8000]}"
                )
                result = self._provider.generate(
                    prompt,
                    {
                        **self._provider_context,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                raw = result.get("message", "") or result.get("content", "")
                if raw:
                    max_chars = max(token_budget * 4, 500) if token_budget > 0 else _SUMMARY_MAX_CHARS
                    return raw[:max_chars]
            except Exception:
                pass  # fall through to heuristic

        # Heuristic: keep first and last portion of history
        max_chars = max(token_budget * 4, 500) if token_budget > 0 else _SUMMARY_MAX_CHARS
        if len(history_text) <= max_chars:
            return history_text
        head = history_text[: max_chars // 2]
        tail = history_text[len(history_text) - max_chars // 2 :]
        return f"{head}\n…[truncated]…\n{tail}"

    def _build_handoff_summary(
        self,
        *,
        session_id: str,
        task_id: str | None,
        history: list[dict[str, Any]],
        recents: list[dict[str, Any]],
        summary: str | None,
    ) -> dict[str, Any]:
        task = self._load_task_for_handoff(task_id)
        messages = history + recents
        commands = self._handoff_commands(task)
        verification = self._handoff_verification(task)
        failed_tools = self._handoff_failed_tools(messages)
        return {
            "version": 1,
            "sessionId": session_id,
            "taskId": task_id,
            "objective": self._handoff_objective(task, messages),
            "currentStep": task.get("currentStep") if task else None,
            "completedWork": self._handoff_completed_work(task, summary)[:8],
            "modifiedFiles": self._handoff_modified_files(task)[:20],
            "failedCommands": [item for item in commands if self._is_failed_status(item.get("status"))][:8],
            "failedTools": failed_tools[:8],
            "verificationStatus": self._handoff_verification_status(verification, commands, failed_tools),
            "verification": verification[:8],
            "pendingEvidence": self._handoff_pending_evidence(task)[:8],
            "decisions": self._handoff_decisions(task, summary)[:8],
            "risks": self._handoff_risks(task)[:8],
            "nextCommand": self._handoff_next_action(task, commands, failed_tools),
            "recentContext": self._handoff_recent_context(messages),
        }

    def _load_task_for_handoff(self, task_id: str | None) -> dict[str, Any] | None:
        if not task_id:
            return None
        try:
            return self._store.get_task({"taskId": task_id})["task"]
        except Exception:
            return None

    @staticmethod
    def _handoff_objective(task: dict[str, Any] | None, messages: list[dict[str, Any]]) -> str | None:
        if task and task.get("goal"):
            return str(task["goal"])[:500]
        for message in reversed(messages):
            if message.get("role") == "user" and message.get("content"):
                return str(message["content"])[:500]
        return None

    @staticmethod
    def _handoff_completed_work(task: dict[str, Any] | None, summary: str | None) -> list[str]:
        items: list[str] = []
        if task:
            for step in task.get("plan") or []:
                if isinstance(step, dict) and step.get("status") == "completed":
                    title = step.get("title") or step.get("id")
                    if title:
                        items.append(str(title)[:240])
            result_summary = task.get("resultSummary") or task.get("summary")
            if result_summary:
                items.append(str(result_summary)[:500])
        if summary:
            items.append(str(summary)[:500])
        return ContextCompactor._dedupe_text(items)

    @staticmethod
    def _handoff_modified_files(task: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not task:
            return []
        files: list[dict[str, Any]] = []
        for item in task.get("changedFiles") or []:
            if isinstance(item, dict):
                path = item.get("path") or item.get("file") or item.get("name")
                if path:
                    files.append({
                        "path": str(path),
                        "status": item.get("status"),
                        "summary": item.get("summary"),
                    })
            elif isinstance(item, str):
                files.append({"path": item})
        return ContextCompactor._dedupe_records(files, keys=("path", "status", "summary"))

    @staticmethod
    def _handoff_commands(task: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not task:
            return []
        commands: list[dict[str, Any]] = []
        for item in task.get("commands") or []:
            if not isinstance(item, dict):
                continue
            commands.append({
                "command": item.get("command") or item.get("name"),
                "status": item.get("status"),
                "exitCode": item.get("exitCode"),
                "summary": item.get("summary"),
            })
        return ContextCompactor._dedupe_records(commands, keys=("command", "status", "exitCode", "summary"))

    @staticmethod
    def _handoff_verification(task: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not task:
            return []
        verification: list[dict[str, Any]] = []
        for source_key in ("verification", "testsRun"):
            for item in task.get(source_key) or []:
                if not isinstance(item, dict):
                    continue
                verification.append({
                    "name": item.get("name") or item.get("command") or item.get("suite"),
                    "status": item.get("status"),
                    "summary": item.get("summary"),
                })
        return ContextCompactor._dedupe_records(verification, keys=("name", "status", "summary"))

    @staticmethod
    def _handoff_pending_evidence(task: dict[str, Any] | None) -> list[dict[str, Any]]:
        return []

    @staticmethod
    def _handoff_failed_tools(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for message in messages:
            if message.get("role") != "tool":
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            try:
                payload = json.loads(content)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(payload, dict):
                continue
            status = str(payload.get("status") or "").strip()
            if not ContextCompactor._is_failed_status(status) and payload.get("ok") is not False:
                continue
            tool_name = str(message.get("name") or payload.get("tool") or payload.get("name") or "unknown")
            summary = (
                payload.get("summary")
                or payload.get("error")
                or payload.get("message")
                or payload.get("content")
                or "Tool failed."
            )
            summary_text = str(summary)[:500]
            failure = build_tool_failure_record(
                tool_name=tool_name,
                payload=payload,
                status=status,
                summary=summary_text,
            )
            key = (tool_name, status, summary_text[:200])
            if key in seen:
                continue
            seen.add(key)
            if isinstance(payload.get("recoveryDecision"), dict):
                failure["recoveryDecision"] = payload["recoveryDecision"]
            failures.append(failure)
        return failures

    @staticmethod
    def _handoff_risks(task: dict[str, Any] | None) -> list[str]:
        if not task:
            return []
        risks: list[str] = []
        for item in task.get("risks") or []:
            text = item.get("summary") or item.get("risk") or item.get("message") if isinstance(item, dict) else item
            if text:
                risks.append(str(text)[:300])
        return ContextCompactor._dedupe_text(risks)

    @staticmethod
    def _handoff_decisions(task: dict[str, Any] | None, summary: str | None) -> list[str]:
        decisions: list[str] = []
        if summary:
            decisions.append(str(summary)[:300])
        return ContextCompactor._dedupe_text(decisions)

    @staticmethod
    def _handoff_verification_status(
        verification: list[dict[str, Any]],
        commands: list[dict[str, Any]],
        failed_tools: list[dict[str, Any]] | None = None,
    ) -> str:
        statuses = [str(item.get("status") or "").lower() for item in verification + commands]
        if failed_tools:
            return "failed"
        if any(ContextCompactor._is_failed_status(status) for status in statuses):
            return "failed"
        if any(status in {"passed", "completed", "success", "ok"} for status in statuses):
            return "passed"
        return "missing"

    @staticmethod
    def _handoff_next_action(
        task: dict[str, Any] | None,
        commands: list[dict[str, Any]],
        failed_tools: list[dict[str, Any]] | None = None,
    ) -> str | None:
        for command in commands:
            if ContextCompactor._is_failed_status(command.get("status")) and command.get("command"):
                return f"Fix or rerun failed command: {command['command']}"
        if failed_tools:
            first = failed_tools[0]
            return f"Recover failed tool: {first.get('name')}"
        if task:
            for step in task.get("plan") or []:
                if isinstance(step, dict) and step.get("status") in {"active", "pending"}:
                    title = step.get("title") or step.get("id")
                    if title:
                        return str(title)[:300]
            if task.get("currentStep"):
                return str(task["currentStep"])[:300]
        return None

    @staticmethod
    def _handoff_recent_context(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        recent: list[dict[str, str]] = []
        for message in messages[-6:]:
            content = str(message.get("content") or "").strip()
            if content:
                recent.append({"role": str(message.get("role") or "unknown"), "content": content[:300]})
        return recent

    @staticmethod
    def _format_handoff_summary(handoff: dict[str, Any]) -> str:
        lines: list[str] = []
        if handoff.get("objective"):
            lines.append(f"Objective: {handoff['objective']}")
        if handoff.get("currentStep"):
            lines.append(f"Current step: {handoff['currentStep']}")
        lines.append(f"Verification status: {handoff.get('verificationStatus') or 'missing'}")
        if handoff.get("completedWork"):
            lines.append("Completed work:")
            lines.extend(f"- {item}" for item in handoff["completedWork"][:5])
        if handoff.get("modifiedFiles"):
            lines.append("Modified files:")
            for item in handoff["modifiedFiles"][:8]:
                summary = f" - {item.get('summary')}" if item.get("summary") else ""
                lines.append(f"- {item.get('path')}{summary}")
        if handoff.get("failedCommands"):
            lines.append("Failed commands:")
            for item in handoff["failedCommands"][:5]:
                lines.append(f"- {item.get('command')} ({item.get('status')})")
        if handoff.get("failedTools"):
            lines.append("Failed tools:")
            for item in handoff["failedTools"][:5]:
                lines.append(f"- {item.get('name')} ({item.get('status')}): {item.get('summary')}")
                if item.get("recoveryHint"):
                    lines.append(f"  Recovery: {item['recoveryHint']}")
        if handoff.get("risks"):
            lines.append("Risks:")
            lines.extend(f"- {item}" for item in handoff["risks"][:5])
        if handoff.get("nextCommand"):
            lines.append(f"Next action: {handoff['nextCommand']}")
        return "\n".join(lines)

    @staticmethod
    def _is_failed_status(status: Any) -> bool:
        text = str(status or "").lower()
        return text in {"failed", "error", "timeout", "killed", "cancelled"} or text.startswith("fail")

    @staticmethod
    def _dedupe_text(items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    @staticmethod
    def _dedupe_records(items: list[dict[str, Any]], *, keys: tuple[str, ...]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for item in items:
            cleaned = {key: value for key, value in item.items() if value not in (None, "", [], {})}
            identity = tuple(str(cleaned.get(key) or "").strip().casefold() for key in keys)
            if identity in seen:
                continue
            seen.add(identity)
            if cleaned:
                result.append(cleaned)
        return result

    def _llm_compaction_decision(
        self,
        messages: list[dict[str, Any]],
        tokens_before: int,
        max_tokens: int,
    ) -> CompactionDecision | None:
        if self._provider is None:
            return None
        preview = "\n".join(
            f"[{m.get('role', '?')}] {str(m.get('content', ''))[:500]}"
            for m in messages[-8:]
        )
        prompt = (
            "Decide whether to compact this conversation context before the next model call.\n"
            "Return ONLY JSON: {\"shouldCompact\": true|false, \"reason\": \"short reason\"}.\n"
            "Prefer compaction when older details can be summarized safely; avoid compaction "
            "when exact recent tool outputs or code snippets are still critical.\n\n"
            f"Estimated tokens: {tokens_before}\n"
            f"Max tokens: {max_tokens}\n"
            f"Recent context preview:\n{preview}"
        )
        try:
            result = self._provider.generate(
                prompt,
                {
                    **self._provider_context,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            raw = result.get("message") or result.get("content") or ""
            match_start = raw.find("{")
            match_end = raw.rfind("}")
            if match_start < 0 or match_end <= match_start:
                return None
            payload = json.loads(raw[match_start:match_end + 1])
            should = bool(payload.get("shouldCompact", False))
            reason = str(payload.get("reason") or "provider compaction proposal").strip()
            return CompactionDecision(
                should_compact=should,
                reason=reason[:240],
                tokens_before=tokens_before,
                max_tokens=max_tokens,
                source="llm",
                force=should and tokens_before < max_tokens,
            )
        except Exception:
            return None

    @staticmethod
    def _hash_primers(primers: list[dict]) -> str:
        content = "|".join(m.get("content", "") for m in primers)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    # -- Progressive compaction helpers --

    # Tool output is often the largest single contributor to context bloat.
    # Keep the head and tail with a truncation marker in between.
    _TOOL_OUTPUT_HEAD_CHARS = 600
    _TOOL_OUTPUT_TAIL_CHARS = 400
    _TOOL_OUTPUT_MAX_CHARS = _TOOL_OUTPUT_HEAD_CHARS + _TOOL_OUTPUT_TAIL_CHARS

    @classmethod
    def _truncate_tool_outputs(
        cls,
        messages: list[dict[str, Any]],
        msg_tokens: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Return a shallow copy with oversized tool outputs truncated.

        Only tool-role messages longer than ``_TOOL_OUTPUT_MAX_CHARS`` are
        truncated; everything else is preserved as-is.
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "tool" and isinstance(content, str) and len(content) > cls._TOOL_OUTPUT_MAX_CHARS:
                truncated = (
                    content[: cls._TOOL_OUTPUT_HEAD_CHARS]
                    + f"\n…[truncated {len(content) - cls._TOOL_OUTPUT_MAX_CHARS} chars]…\n"
                    + content[len(content) - cls._TOOL_OUTPUT_TAIL_CHARS :]
                )
                new_msg = dict(msg)
                new_msg["content"] = truncated
                result.append(new_msg)
            else:
                result.append(msg)
        return result

    @classmethod
    def _has_oversized_tool_outputs(cls, messages: list[dict[str, Any]]) -> bool:
        """Return True if any tool-role message exceeds the truncation limit."""
        for msg in messages:
            content = msg.get("content")
            if (
                msg.get("role") == "tool"
                and isinstance(content, str)
                and len(content) > cls._TOOL_OUTPUT_MAX_CHARS
            ):
                return True
        return False
