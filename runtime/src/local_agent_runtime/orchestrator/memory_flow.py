from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MemoryFlowMixin:
    def _promote_scratchpad_to_memory(self, session_id: str) -> None:
        """Promote scratchpad entries to session memory after task completion."""
        if self._memory_manager is None or self._scratchpad is None:
            return
        from ..memory.types import MemoryKind, MemoryCategory, MemoryScope, MemorySource
        entries = self._scratchpad.list_entries(session_id)
        for entry in entries:
            self._memory_manager.remember(
                content=f"[{entry.key}] {entry.value}",
                session_id=session_id,
                kind=MemoryKind.SESSION,
                metadata={
                    "category": MemoryCategory.IMPLEMENTATION_NOTE.value,
                    "scope": MemoryScope.SESSION.value,
                    "confidence": 0.7,
                    "source": MemorySource.ASSISTANT_SUMMARY.value,
                },
            )
        if entries:
            self._scratchpad.clear(session_id)

    def _consolidate_working_memories(self, session_id: str) -> None:
        """Promote WORKING memories to SESSION after task completion."""
        if self._memory_manager is None:
            return
        span = self._tracer.start_span(
            "memory_consolidate",
            trace_id=getattr(self, "_active_trace_id", None),
            attributes={"sessionId": session_id},
        )
        try:
            count = self._memory_manager.consolidate(session_id)
            if count > 0:
                logger.info("Consolidated %d working memories for session %s", count, session_id)
            self._tracer.end_span(span.span_id, status="ok", attributes={"count": count})
        except Exception:  # noqa: BLE001
            logger.debug("Memory consolidation failed for session %s", session_id, exc_info=True)
            self._tracer.end_span(span.span_id, status="error")

    def _remember_task_result(self, *, session_id: str, task: dict[str, Any]) -> None:
        if not hasattr(self._store, "update_session_summary"):
            return
        if task.get("status") not in {"completed", "failed", "cancelled"}:
            return

        current_session = self._store.require_session(session_id)
        current_summary = current_session.get("summary")
        entry = self._task_memory_entry(task)
        updated_summary = self._append_memory(current_summary, entry, marker="Task memory:")
        session = self._store.update_session_summary(session_id, updated_summary)
        self._publish(
            session_id=session_id,
            task=task,
            event_type="session.updated",
            payload={
                "summary": session.get("summary"),
                "title": session.get("title"),
                "status": session.get("status"),
            },
        )
        if hasattr(self._store, "require_workspace") and hasattr(self._store, "update_workspace_summary"):
            workspace = self._store.require_workspace(current_session["workspaceId"])
            updated_workspace_summary = self._append_memory(
                workspace.get("summary"),
                entry,
                marker="Project memory:",
                max_chars=6000,
            )
            self._store.update_workspace_summary(workspace["id"], updated_workspace_summary)

        # Store in MemoryManager for structured recall
        if self._memory_manager is not None:
            memory_content = self._task_memory_entry(task)
            workspace_id = current_session.get("workspaceId")
            from ..memory.types import MemoryKind, MemoryCategory, MemoryScope, MemorySource

            task_status = task.get("status", "completed")
            if task_status == "completed":
                category = MemoryCategory.TASK_LEARNING
                confidence = 0.8
            elif task_status == "failed":
                category = MemoryCategory.OPEN_ISSUE
                confidence = 0.5
            else:
                category = MemoryCategory.TASK_LEARNING
                confidence = 0.4

            # Collect source message ids from the task
            source_message_ids: list[str] = []
            active_assistant_id = task.get("activeAssistantMessageId")
            if active_assistant_id:
                source_message_ids.append(active_assistant_id)
            try:
                task_messages = self._store.list_messages_by_task(task.get("id", ""))
                for msg in task_messages:
                    if msg.get("role") == "user" and msg.get("id"):
                        source_message_ids.append(msg["id"])
            except Exception:  # noqa: BLE001
                pass

            self._memory_manager.remember(
                session_id=session_id,
                workspace_id=workspace_id,
                content=memory_content,
                kind=MemoryKind.WORKING,
                metadata={
                    "category": category.value,
                    "scope": MemoryScope.WORKSPACE.value if workspace_id else MemoryScope.SESSION.value,
                    "confidence": confidence,
                    "source": MemorySource.TASK_RESULT.value,
                    "sourceTaskIds": [task.get("id", "")],
                    "sourceMessageIds": source_message_ids,
                },
                dedup=(task_status == "completed"),
            )

            # Detect explicit user preferences from user messages
            if task_status == "completed" and source_message_ids:
                user_contents = []
                try:
                    task_messages = self._store.list_messages_by_task(task.get("id", ""))
                    for msg in task_messages:
                        if msg.get("role") == "user" and msg.get("content"):
                            user_contents.append(msg["content"])
                except Exception:  # noqa: BLE001
                    pass
                for uc in user_contents:
                    lower_uc = uc.lower()
                    if any(p in lower_uc for p in self._SUPPLEMENT_MEMORY_PATTERNS):
                        self._memory_manager.remember(
                            session_id=session_id,
                            workspace_id=workspace_id,
                            content=f"[User preference] {uc[:200]}",
                            kind=MemoryKind.WORKING,
                            metadata={
                                "category": MemoryCategory.USER_PREFERENCE.value,
                                "scope": MemoryScope.WORKSPACE.value if workspace_id else MemoryScope.SESSION.value,
                                "confidence": 0.6,
                                "source": MemorySource.USER_MESSAGE.value,
                                "sourceTaskIds": [task.get("id", "")],
                                "sourceMessageIds": source_message_ids,
                            },
                            dedup=True,
                        )

    _SUPPLEMENT_MEMORY_PATTERNS: list[str] = [
        "prefer", "always", "never", "use ", "don't use", "avoid",
        "make sure", "remember to", "by default", "we use", "we should",
        "convention", "style", "format", "pattern",
    ]

    def _remember_supplement_candidates(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        supplements: list[dict[str, Any]],
    ) -> None:
        """Write memory candidates from consumed supplements that look like preferences/conventions."""
        if self._memory_manager is None:
            return
        from ..memory.types import MemoryKind, MemoryCategory, MemoryScope, MemorySource

        workspace_id = None
        try:
            session = self._store.require_session(session_id)
            workspace_id = session.get("workspaceId")
        except Exception:  # noqa: BLE001
            pass

        for entry in supplements:
            content = entry.get("content", "")
            if not content or len(content) < 10:
                continue

            # Check if supplement content matches preference/convention patterns
            lower_content = content.lower()
            is_candidate = any(pattern in lower_content for pattern in self._SUPPLEMENT_MEMORY_PATTERNS)
            if not is_candidate:
                continue

            self._memory_manager.remember(
                session_id=session_id,
                workspace_id=workspace_id,
                content=f"[Supplement] {content}",
                kind=MemoryKind.WORKING,
                metadata={
                    "category": MemoryCategory.USER_PREFERENCE.value,
                    "scope": MemoryScope.SESSION.value,
                    "confidence": 0.5,
                    "source": MemorySource.SUPPLEMENT.value,
                    "sourceTaskIds": [task.get("id", "")],
                    "sourceMessageIds": [entry.get("message_id", "")],
                },
                dedup=True,
            )

    def _task_memory_entry(self, task: dict[str, Any]) -> str:
        status = task.get("status") or "completed"
        goal = self._single_line(task.get("goal") or "")
        lines = [f"- {status}: {goal}"]
        summary = self._single_line(task.get("summary") or task.get("resultSummary") or "")
        if summary:
            lines.append(f"  result: {summary}")

        changed_files = [
            item
            for item in task.get("changedFiles") or []
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ]
        if changed_files:
            lines.append(
                "  files: "
                + ", ".join(
                    self._single_line(
                        f"{item.get('path')} ({item.get('status') or 'changed'})"
                    )
                    for item in changed_files[:6]
                )
            )

        commands = [
            item
            for item in task.get("commands") or []
            if isinstance(item, dict) and isinstance(item.get("command"), str)
        ]
        if commands:
            lines.append(
                "  commands: "
                + "; ".join(
                    self._single_line(
                        f"{item.get('command')} -> {item.get('status') or 'recorded'}"
                        + (
                            f" exit {item.get('exitCode')}"
                            if item.get("exitCode") is not None
                            else ""
                        )
                    )
                    for item in commands[:4]
                )
            )

        verification = [
            item
            for item in task.get("verification") or []
            if isinstance(item, dict)
        ]
        if verification:
            lines.append(
                "  verification: "
                + "; ".join(
                    self._single_line(
                        f"{item.get('status') or 'recorded'}"
                        + (f" - {item.get('summary')}" if item.get("summary") else "")
                    )
                    for item in verification[:4]
                )
            )
        return "\n".join(lines)

    def _append_memory(
        self,
        current_summary: Any,
        entry: str,
        *,
        marker: str,
        max_chars: int = 4000,
    ) -> str:
        current = str(current_summary or "").strip()
        if self._memory_contains_entry(current, entry):
            return current or f"{marker}\n{entry}"
        if not current:
            combined = f"{marker}\n{entry}"
        elif marker in current:
            combined = f"{current}\n{entry}"
        else:
            combined = f"{current}\n\n{marker}\n{entry}"

        return self._trim_memory_blocks(combined, marker=marker, max_chars=max_chars)

    def _trim_memory_blocks(self, combined: str, *, marker: str, max_chars: int) -> str:
        marker_index = combined.find(marker)
        if marker_index < 0:
            return combined[-max_chars:].lstrip()
        prefix = combined[: marker_index + len(marker)].rstrip()
        memory_body = combined[marker_index + len(marker) :].strip()
        blocks = self._dedupe_memory_blocks(self._memory_blocks(memory_body))
        candidate = f"{prefix}\n" + "\n".join(blocks) if blocks else prefix
        if len(candidate) <= max_chars:
            return candidate

        kept_blocks: list[str] = []
        total = len(prefix) + 1
        for block in reversed(blocks):
            block_length = len(block) + 1
            if total + block_length > max_chars:
                if kept_blocks:
                    break
                continue
            kept_blocks.append(block)
            total += block_length
        kept_blocks.reverse()
        return f"{prefix}\n" + "\n".join(kept_blocks) if kept_blocks else prefix

    def _memory_blocks(self, body: str) -> list[str]:
        blocks: list[list[str]] = []
        current: list[str] = []
        for line in body.splitlines():
            if line.startswith("- ") and current:
                blocks.append(current)
                current = [line]
            elif line.strip():
                current.append(line.rstrip())
        if current:
            blocks.append(current)
        return ["\n".join(block) for block in blocks]

    def _dedupe_memory_blocks(self, blocks: list[str]) -> list[str]:
        seen: set[str] = set()
        kept: list[str] = []
        for block in reversed(blocks):
            key = " ".join(block.split()).lower()
            if key in seen:
                continue
            seen.add(key)
            kept.append(block)
        kept.reverse()
        return kept

    def _memory_contains_entry(self, current: str, entry: str) -> bool:
        if not current:
            return False
        normalized_current = "\n".join(line.rstrip() for line in current.splitlines())
        normalized_entry = "\n".join(line.rstrip() for line in entry.strip().splitlines())
        return normalized_entry in normalized_current

    def _single_line(self, value: Any, *, max_chars: int = 220) -> str:
        text = " ".join(str(value).split())
        if len(text) <= max_chars:
            return text
        return f"{text[: max_chars - 15].rstrip()} [truncated]"

    def _merge_completion_summary(self, *, summary: str, validation: dict[str, Any] | None) -> str:
        base = (summary or "").strip()
        if not validation:
            return base
        validation_summary = (validation.get("summary") or "").strip()
        if not validation_summary:
            return base
        if not base:
            return validation_summary
        return f"{base} {validation_summary}"

