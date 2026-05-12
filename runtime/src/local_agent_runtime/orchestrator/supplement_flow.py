"""Supplement Flow Mixin â extracted from Orchestrator.

Handles supplemental message routing: finding target tasks, attaching messages,
assessing impact, routing to children, and task focus context.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class SupplementFlowMixin:
    """Mixin providing supplement message routing and task focus helpers."""

    def _find_supplement_target_task(self, *, session_id: str, task_id: str | None, strict: bool) -> dict[str, Any] | None:
        if not task_id:
            return self._find_open_session_task(session_id)
        try:
            task = self._store.get_task({"taskId": task_id})["task"]
        except Exception:  # noqa: BLE001
            if strict:
                raise ValueError(f"Cannot supplement missing task: {task_id}")
            return None
        if task.get("sessionId") != session_id:
            if strict:
                raise ValueError(f"Cannot supplement task outside session: {task_id}")
            return None
        if task.get("status") not in {"running", "planning", "verifying", "waiting_approval", "queued", "paused"}:
            if strict:
                raise ValueError(f"Cannot supplement task that is not active: {task_id}")
            return None
        return task

    def _attach_supplemental_message(self, *, session_id: str, task: dict[str, Any], content: str) -> dict[str, Any]:
        # Create user message for chat history
        user_msg = self._store.create_message(
            session_id=session_id,
            task_id=task["id"],
            role="user",
            content=content,
            kind="supplement",
        )
        # Write to task inbox so the running loop can consume it
        inbox_entry = self._store.create_inbox_entry(
            task_id=task["id"],
            session_id=session_id,
            content=content,
            message_id=user_msg["id"],
        )
        updated_task = self._store.update_task(
            task_id=task["id"],
            status=task["status"],
            plan=task.get("plan") or [],
            current_step=task.get("currentStep"),
        )
        runtime_task = {**updated_task, "plan": updated_task.get("plan") or task.get("plan") or []}
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.updated",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task.get("plan") or [],
                "currentStep": runtime_task.get("currentStep"),
                "detail": "Supplemental user message attached to the active task.",
            },
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.supplement.received",
            payload={
                "inboxEntryId": inbox_entry["id"],
                "messageId": user_msg["id"],
                "content": content,
            },
        )
        acknowledgement = "\u5df2\u8865\u5145\u5230\u5f53\u524d\u672a\u5b8c\u6210\u4efb\u52a1\uff0c\u7ee7\u7eed\u6cbf\u7528\u539f\u4efb\u52a1\u8ba1\u5212\u3002"
        self._store.create_message(
            session_id=session_id,
            task_id=runtime_task["id"],
            role="assistant",
            content=acknowledgement,
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="assistant.message.completed",
            payload={"content": acknowledgement, "supplemental": True},
        )
        # Route supplement to child tasks if applicable
        routing_result = self._route_supplement_to_children(
            session_id=session_id,
            root_task=runtime_task,
            content=content,
            message_id=user_msg["id"],
        )
        result = {"task": runtime_task, "acceptedMode": "supplement"}
        if routing_result:
            result["supplementRouting"] = routing_result
        return result

    @staticmethod
    def _extract_file_paths(text: str) -> list[str]:
        """Extract plausible file paths from free-form text.

        Matches patterns like `src/foo.py`, `dir/bar.tsx`, `path/to/file.js`, etc.
        """
        pattern = r'(?:^|[\s`"\'(])([\w./\\-]+\.(?:py|ts|tsx|js|jsx|json|yaml|yml|toml|md|rs|go|java|c|cpp|h|hpp|cs|rb|php|sh|bash|sql|html|css|scss|vue|svelte|graphql|proto))(?=[\s`"\')\],;]|$)'
        matches = re.findall(pattern, text)
        return [m for m in matches if len(m) >= 3]

    @staticmethod
    def _assess_supplement_impact(
        content: str,
        children: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Assess which files, modules, and child tasks are affected by a supplement.

        Returns a structured impact scope:
        - affectedFiles: file paths extracted from the supplement
        - affectedChildTasks: list of {childTaskId, matchedFiles, matchType}
        - summary: brief human-readable summary
        """
        affected_files = SupplementFlowMixin._extract_file_paths(content)

        # Build a mapping: file path -> [child tasks that touch that file]
        file_to_children: dict[str, list[str]] = {}
        child_id_to_title: dict[str, str] = {}

        for child in children:
            child_id = child.get("id", "")
            child_id_to_title[child_id] = child.get("goal") or child.get("title") or child_id

            # Collect all file paths associated with this child
            child_files: set[str] = set()
            for cf in child.get("changedFiles") or []:
                if isinstance(cf, dict):
                    child_files.add(cf.get("path", ""))
                elif isinstance(cf, str):
                    child_files.add(cf)

            # Also check goal text for file references
            goal = child.get("goal") or ""
            child_files.update(SupplementFlowMixin._extract_file_paths(goal))

            for f in child_files:
                if f:
                    file_to_children.setdefault(f, []).append(child_id)

        # Match supplement files to child tasks
        affected_child_tasks: list[dict[str, Any]] = []
        matched_child_ids: set[str] = set()

        for sf in affected_files:
            # Direct match
            if sf in file_to_children:
                for cid in file_to_children[sf]:
                    if cid not in matched_child_ids:
                        matched_child_ids.add(cid)
                        affected_child_tasks.append({
                            "childTaskId": cid,
                            "matchedFiles": [sf],
                            "matchType": "direct",
                        })
                    else:
                        # Append to existing entry
                        for entry in affected_child_tasks:
                            if entry["childTaskId"] == cid and sf not in entry["matchedFiles"]:
                                entry["matchedFiles"].append(sf)

            # Directory prefix match (supplement file is under a child's scope dir)
            for child_file, cids in file_to_children.items():
                child_dir = "/".join(child_file.split("/")[:-1])
                if child_dir and sf.startswith(child_dir + "/") and sf != child_file:
                    for cid in cids:
                        if cid not in matched_child_ids:
                            matched_child_ids.add(cid)
                            affected_child_tasks.append({
                                "childTaskId": cid,
                                "matchedFiles": [sf],
                                "matchType": "directory_prefix",
                            })
                        else:
                            for entry in affected_child_tasks:
                                if entry["childTaskId"] == cid and sf not in entry["matchedFiles"]:
                                    entry["matchedFiles"].append(sf)

        summary_parts: list[str] = []
        if affected_files:
            summary_parts.append(f"Supplement references {len(affected_files)} file(s): {', '.join(affected_files[:5])}")
        if affected_child_tasks:
            summary_parts.append(f"Impacts {len(affected_child_tasks)} child task(s)")

        return {
            "affectedFiles": affected_files,
            "affectedChildTasks": affected_child_tasks,
            "summary": "; ".join(summary_parts) if summary_parts else "No specific file or task impact detected",
        }

    def _route_supplement_to_children(
        self,
        *,
        session_id: str,
        root_task: dict[str, Any],
        content: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        """Route supplement to child tasks based on goal/scope/file relevance.

        Returns routing info dict if any children were matched, None otherwise.
        """
        # Find child tasks under this root
        all_tasks = self._store.list_tasks({"sessionId": session_id}).get("tasks", [])
        root_id = root_task.get("id")
        children = [
            t for t in all_tasks
            if t.get("rootTaskId") == root_id and t.get("id") != root_id
        ]
        if not children:
            return None

        # Assess impact scope
        impact = self._assess_supplement_impact(content, children)

        # Build quick lookup: child_id -> match info from impact assessment
        impact_child_ids: set[str] = set()
        for entry in impact.get("affectedChildTasks", []):
            impact_child_ids.add(entry["childTaskId"])

        # Extract keywords from supplement content for matching
        content_lower = content.lower()
        content_words = set(content_lower.split())

        routed_to: list[dict[str, Any]] = []
        follow_ups: list[dict[str, Any]] = []

        for child in children:
            child_id = child.get("id", "")
            # Score relevance by matching keywords against child goal/scope
            goal = (child.get("goal") or "").lower()
            changed_files = child.get("changedFiles") or []
            scope_text = " ".join(str(f) for f in changed_files).lower()

            # Keyword overlap scoring
            goal_words = set(goal.split())
            scope_words = set(scope_text.split()) if scope_text else set()
            keyword_overlap = len(content_words & (goal_words | scope_words))

            # File-path-based match from impact assessment
            file_match = child_id in impact_child_ids

            # Must have at least one keyword overlap OR a file-path match to route
            if keyword_overlap == 0 and not file_match:
                continue

            # Determine match reason
            match_reasons: list[str] = []
            if file_match:
                match_reasons.append("file_path_match")
            if keyword_overlap > 0:
                match_reasons.append("keyword_overlap")

            child_status = child.get("status", "")
            is_running = child_status in {"running", "planning", "verifying", "waiting_approval", "paused"}
            is_completed = child_status in {"completed", "failed"}

            if is_running:
                # Forward supplement to child's inbox
                inbox_entry = self._store.create_inbox_entry(
                    task_id=child["id"],
                    session_id=session_id,
                    content=content,
                    message_id=message_id,
                )
                routed_to.append({
                    "childTaskId": child["id"],
                    "action": "forwarded",
                    "inboxEntryId": inbox_entry["id"],
                    "matchReasons": match_reasons,
                })
            elif is_completed:
                # Mark for potential follow-up
                follow_ups.append({
                    "childTaskId": child["id"],
                    "action": "follow_up_recommended",
                    "childStatus": child_status,
                    "matchReasons": match_reasons,
                })

        if not routed_to and not follow_ups:
            return None

        routing_info: dict[str, Any] = {
            "routedTo": routed_to,
            "followUps": follow_ups,
            "impactScope": impact,
        }

        # Publish routed event
        self._publish(
            session_id=session_id,
            task=root_task,
            event_type="task.supplement.routed",
            payload={
                "messageId": message_id,
                "content": content,
                "routedToCount": len(routed_to),
                "followUpCount": len(follow_ups),
                "routedTo": routed_to,
                "followUps": follow_ups,
                "impactScope": impact,
            },
        )

        return routing_info

    def _context_with_task_focus(self, context: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
        focused_context = {**context}
        task_focus = {
            "taskId": task.get("id"),
            "goal": task.get("goal"),
            "status": task.get("status"),
            "currentStep": task.get("currentStep"),
            "acceptanceCriteria": list(task.get("acceptanceCriteria") or []),
            "outOfScope": list(task.get("outOfScope") or []),
        }
        focused_context["task_focus"] = task_focus

        messages = list(focused_context.get("messages") or [])
        focus_text = self._task_focus_text(task_focus)
        if messages and messages[-1].get("role") == "user":
            content = str(messages[-1].get("content") or "")
            if "Task focus:" not in content:
                messages[-1] = {**messages[-1], "content": f"{content}\n\n{focus_text}"}
        else:
            messages.append({"role": "user", "content": focus_text})
        focused_context["messages"] = messages
        return focused_context

    def _task_focus_text(self, task_focus: dict[str, Any]) -> str:
        lines = [
            "Task focus:",
            f"- task id: {task_focus.get('taskId')}",
            f"- status: {task_focus.get('status')}",
            f"- goal: {task_focus.get('goal')}",
        ]
        current_step = task_focus.get("currentStep")
        if current_step:
            lines.append(f"- current step: {current_step}")

        acceptance = task_focus.get("acceptanceCriteria") or []
        if acceptance:
            lines.append("Acceptance criteria:")
            lines.extend(f"- {item}" for item in acceptance)

        out_of_scope = task_focus.get("outOfScope") or []
        if out_of_scope:
            lines.append("Out of scope:")
            lines.extend(f"- {item}" for item in out_of_scope)
        return "\n".join(lines)
