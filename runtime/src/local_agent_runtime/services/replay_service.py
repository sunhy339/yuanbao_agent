"""Replay service for audit and dry-run replay of task execution.

Audit replay reconstructs a task timeline from stored trace events,
proposal records, turns, and other data — without calling the LLM.

Dry-run replay goes further by re-evaluating policy gates and validators
under changed configuration, showing which decisions would differ.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..store.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


def _parse_json(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


class ReplayService:
    """Build audit timelines and dry-run gate evaluations from stored data."""

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def audit_replay(self, params: dict[str, Any]) -> dict[str, Any]:
        """Reconstruct a task execution timeline from stored data.

        Params: {taskId: str}
        Returns: {replaySession: {...}, timeline: [...], warnings: [...]}
        """
        task_id = params.get("taskId") or params.get("sourceTaskId") or ""
        if not task_id:
            raise ValueError("taskId is required")

        now_ms = int(time.time() * 1000)
        timeline = self._build_timeline(task_id)
        warnings = self._detect_unavailable_side_effects(timeline)
        summary = self._summarize_timeline(timeline, warnings)

        session = self._store.create_replay_session({
            "sourceTaskId": task_id,
            "mode": "audit",
            "status": "completed",
            "timeline": timeline,
            "warnings": warnings,
            "summary": summary,
            "startedAt": now_ms,
            "completedAt": now_ms,
        })

        return {
            "replaySession": session["replaySession"],
            "timeline": timeline,
            "warnings": warnings,
            "summary": summary,
        }

    def dry_run_replay(self, params: dict[str, Any]) -> dict[str, Any]:
        """Re-evaluate policy gates and validators under new config.

        Params: {taskId: str, configOverrides?: dict}
        Returns: {replaySession: {...}, timeline: [...], gateEvaluations: [...], warnings: [...]}
        """
        task_id = params.get("taskId") or params.get("sourceTaskId") or ""
        if not task_id:
            raise ValueError("taskId is required")

        config_overrides = params.get("configOverrides") or {}
        now_ms = int(time.time() * 1000)

        timeline = self._build_timeline(task_id)
        warnings = self._detect_unavailable_side_effects(timeline)
        gate_evaluations = self._evaluate_gates(task_id, config_overrides)
        summary = self._summarize_dry_run(timeline, gate_evaluations, warnings)

        session = self._store.create_replay_session({
            "sourceTaskId": task_id,
            "mode": "dry_run",
            "configOverrides": config_overrides,
            "status": "completed",
            "timeline": timeline,
            "warnings": warnings,
            "gateEvaluations": gate_evaluations,
            "summary": summary,
            "startedAt": now_ms,
            "completedAt": now_ms,
        })

        return {
            "replaySession": session["replaySession"],
            "timeline": timeline,
            "gateEvaluations": gate_evaluations,
            "warnings": warnings,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_timeline(self, task_id: str) -> list[dict[str, Any]]:
        """Build a chronological timeline from all stored data for a task."""
        timeline: list[dict[str, Any]] = []

        # 1. Trace events (decisions + lifecycle)
        trace_result = self._store.list_trace_events({"taskId": task_id, "limit": 1000})
        for evt in trace_result.get("traceEvents", []):
            timeline.append({
                "step": "trace_event",
                "timestamp": evt.get("createdAt"),
                "type": evt.get("type"),
                "source": evt.get("source"),
                "payload": evt.get("payload"),
                "yuanbao": evt.get("yuanbao"),
                "hahaCc": evt.get("hahaCc"),
                "replayable": True,
            })

        # 2. Proposal records
        proposals = self._store._conn.execute(
            "SELECT * FROM proposal_records WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        for row in proposals:
            r = dict(row)
            timeline.append({
                "step": "proposal",
                "timestamp": r.get("created_at"),
                "kind": r.get("kind"),
                "status": r.get("status"),
                "inputSummary": r.get("input_summary"),
                "validationReasons": _parse_json(r.get("validation_reasons_json")),
                "replayable": True,
            })

        # 3. Provider turns
        turns = sorted(
            self._store.list_provider_turns(task_id),
            key=lambda item: item.get("created_at") or 0,
        )
        for r in turns:
            timeline.append({
                "step": "provider_turn",
                "timestamp": r.get("created_at"),
                "turnIndex": r.get("turn_index"),
                "model": r.get("model"),
                "status": r.get("status"),
                "turnDecision": r.get("turn_decision"),
                "thoughtSummary": r.get("thought_summary"),
                "toolPolicyDecision": r.get("toolPolicyDecision") or {},
                "roleSnapshot": r.get("roleSnapshot") or {},
                "toolPolicyExplanation": r.get("toolPolicyExplanation") or {},
                "replayable": False,  # LLM output not reproducible
                "unavailableReason": "LLM response content not stored",
            })

        # 4. Context snapshots
        snapshots = self._store._conn.execute(
            "SELECT * FROM context_snapshots WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        for row in snapshots:
            r = dict(row)
            timeline.append({
                "step": "context_snapshot",
                "timestamp": r.get("created_at"),
                "tokenEstimate": r.get("token_estimate"),
                "toolCount": r.get("tool_count"),
                "includedSections": _parse_json(r.get("included_sections_json")),
                "replayable": True,
            })

        # 5. Approvals
        approvals = self._store._conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        for row in approvals:
            r = dict(row)
            timeline.append({
                "step": "approval",
                "timestamp": r.get("created_at"),
                "status": r.get("status"),
                "kind": r.get("kind"),
                "replayable": True,
            })

        # 6. Patches
        patches = self._store._conn.execute(
            "SELECT * FROM patches WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        for row in patches:
            r = dict(row)
            timeline.append({
                "step": "patch",
                "timestamp": r.get("created_at"),
                "status": r.get("status"),
                "replayable": True,
            })

        # 7. Command logs
        commands = self._store._conn.execute(
            "SELECT * FROM command_logs WHERE task_id = ? ORDER BY started_at ASC",
            (task_id,),
        ).fetchall()
        for row in commands:
            r = dict(row)
            timeline.append({
                "step": "command",
                "timestamp": r.get("started_at"),
                "status": r.get("status"),
                "replayable": True,
            })

        # 8. Scope conflict checks
        scope_checks = self._store._conn.execute(
            "SELECT * FROM scope_conflict_checks WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        for row in scope_checks:
            r = dict(row)
            timeline.append({
                "step": "scope_conflict_check",
                "timestamp": r.get("created_at"),
                "checkType": r.get("check_type"),
                "resolution": r.get("resolution"),
                "safe": bool(r.get("safe")),
                "replayable": True,
            })

        # 9. Hook executions
        hook_execs = self._store._conn.execute(
            "SELECT * FROM hook_executions WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        for row in hook_execs:
            r = dict(row)
            timeline.append({
                "step": "hook_execution",
                "timestamp": r.get("created_at"),
                "event": r.get("event"),
                "status": r.get("status"),
                "policyOutcome": r.get("policy_outcome"),
                "replayable": True,
            })

        # Sort by timestamp
        timeline.sort(key=lambda e: e.get("timestamp") or 0)
        return timeline

    def _detect_unavailable_side_effects(
        self, timeline: list[dict[str, Any]],
    ) -> list[str]:
        """Identify steps that cannot be fully replayed."""
        warnings: list[str] = []
        for entry in timeline:
            if not entry.get("replayable", True):
                reason = entry.get("unavailableReason", "External side effect")
                step = entry.get("step", "unknown")
                warnings.append(f"{step}: {reason}")
        return warnings

    def _evaluate_gates(
        self,
        task_id: str,
        config_overrides: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Re-evaluate policy gates under new config for dry-run replay."""
        from ..policy.proposal_validator import validate_write_scopes

        evaluations: list[dict[str, Any]] = []

        # 1. Re-evaluate write scope validations for proposals
        proposals = self._store._conn.execute(
            "SELECT * FROM proposal_records WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        for row in proposals:
            r = dict(row)
            proposal_data = _parse_json(r.get("proposal_json"))
            kind = r.get("kind", "")

            # Only re-evaluate plan-related proposals that have write scopes
            if kind == "decomposition" and proposal_data:
                subtasks = proposal_data.get("subtasks", [])
                original_reasons = _parse_json(r.get("validation_reasons_json"))

                # Re-run scope validation with any overrides
                new_reasons: list[str] = []
                if config_overrides.get("strictWriteScopes"):
                    scope_subtasks = [
                        {"id": s.get("id"), "ownedScope": s.get("ownedScope") or s.get("writeScope") or []}
                        for s in subtasks if isinstance(s, dict)
                    ]
                    from ..policy.proposal_validator import validate_write_scope_overlap
                    new_reasons = validate_write_scope_overlap(scope_subtasks)

                changed = original_reasons != new_reasons
                evaluations.append({
                    "step": "proposal",
                    "proposalId": r.get("id"),
                    "kind": kind,
                    "originalValidationReasons": original_reasons,
                    "newValidationReasons": new_reasons,
                    "changed": changed,
                })

        # 2. Re-evaluate scope conflict checks
        scope_checks = self._store._conn.execute(
            "SELECT * FROM scope_conflict_checks WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        for row in scope_checks:
            r = dict(row)
            original_overlaps = _parse_json(r.get("overlaps_json"))
            original_safe = bool(r.get("safe"))

            evaluations.append({
                "step": "scope_conflict_check",
                "checkId": r.get("id"),
                "checkType": r.get("check_type"),
                "originalOverlaps": original_overlaps,
                "originalSafe": original_safe,
                "newOverlaps": original_overlaps,  # same data, no model call
                "newSafe": original_safe,
                "changed": False,
            })

        return evaluations

    def _summarize_timeline(
        self,
        timeline: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        """Generate a human-readable summary of the audit replay."""
        step_counts: dict[str, int] = {}
        for entry in timeline:
            step = entry.get("step", "unknown")
            step_counts[step] = step_counts.get(step, 0) + 1

        parts = [f"{count} {name}" for name, count in sorted(step_counts.items())]
        summary = f"Audit replay: {len(timeline)} steps ({', '.join(parts)})"
        if warnings:
            summary += f", {len(warnings)} unavailable"
        return summary

    def _summarize_dry_run(
        self,
        timeline: list[dict[str, Any]],
        gate_evaluations: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        """Generate a human-readable summary of the dry-run replay."""
        changed = sum(1 for e in gate_evaluations if e.get("changed"))
        total = len(gate_evaluations)
        summary = f"Dry-run replay: {len(timeline)} steps, {total} gate evaluations, {changed} changed"
        if warnings:
            summary += f", {len(warnings)} unavailable"
        return summary
