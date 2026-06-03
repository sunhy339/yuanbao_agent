"""Supplement Flow Mixin â extracted from Orchestrator.

Handles supplemental message routing: finding target tasks, attaching messages,
assessing impact, routing to children, and task focus context.
"""
from __future__ import annotations

import logging
import json
import re
from typing import Any

logger = logging.getLogger(__name__)

ACTIVE_SUPPLEMENT_STATUSES = {"running", "planning", "verifying", "waiting_approval", "queued", "paused"}


class SupplementFlowMixin:
    """Mixin providing supplement message routing and task focus helpers."""

    def _find_supplement_target_task(self, *, session_id: str, task_id: str | None, strict: bool) -> dict[str, Any] | None:
        if not task_id:
            task = self._find_open_session_task(session_id)
            if task is None:
                return None
            if task.get("status") not in ACTIVE_SUPPLEMENT_STATUSES:
                return None
            return task
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
        if task.get("status") not in ACTIVE_SUPPLEMENT_STATUSES:
            return None
        return task

    def _attach_supplemental_message(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        content: str,
        metadata: dict[str, Any] | None = None,
        internal_response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if task.get("status") not in ACTIVE_SUPPLEMENT_STATUSES:
            raise ValueError(f"Cannot supplement task that is not active: {task.get('id')}")
        metadata = dict(metadata or {})
        internal_response = internal_response if isinstance(internal_response, dict) else None
        is_internal_answer = internal_response is not None and internal_response.get("kind") == "ask_user_question"
        if is_internal_answer:
            duplicate = self._find_existing_internal_question_answer(task_id=task["id"], internal_response=internal_response)
            if duplicate is not None:
                return {
                    "task": task,
                    "acceptedMode": "supplement",
                    "duplicate": True,
                    "inboxEntry": duplicate,
                }
            metadata["internalResponse"] = {
                key: value
                for key, value in internal_response.items()
                if isinstance(key, str) and value not in (None, "", [])
            }
        user_msg: dict[str, Any] | None = None
        if not is_internal_answer:
            user_msg = self._store.create_message(
                session_id=session_id,
                task_id=task["id"],
                role="user",
                content=content,
                kind="supplement",
                metadata=metadata,
            )
        # Write to task inbox so the running loop can consume it
        inbox_entry = self._store.create_inbox_entry(
            task_id=task["id"],
            session_id=session_id,
            content=content,
            message_id=user_msg["id"] if user_msg is not None else None,
            metadata=metadata,
        )
        routing = task.get("routing") or {}
        if not is_internal_answer:
            routing = self._routing_with_user_takeover(task=task, content=content)
        updated_task = self._store.update_task(
            task_id=task["id"],
            status=task["status"],
            plan=task.get("plan") or [],
            current_step=task.get("currentStep"),
            routing=routing,
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
                "detail": "User answered the pending question." if is_internal_answer else "Supplemental user message attached to the active task.",
            },
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.supplement.received",
            payload={
                "inboxEntryId": inbox_entry["id"],
                "messageId": user_msg["id"] if user_msg is not None else internal_response.get("messageId"),
                "content": content,
                "internalResponse": internal_response,
            },
            visibility="trace" if is_internal_answer else None,
        )
        takeover = (routing.get("mainWorkflow") or {}).get("userTakeover")
        if isinstance(takeover, dict) and takeover.get("state") != "supplement":
            self._publish(
                session_id=session_id,
                task=runtime_task,
                event_type="task.user_takeover.received",
                payload=takeover,
            )
            runtime_task = self._apply_user_takeover_transition(runtime_task, takeover)
        if not is_internal_answer:
            acknowledgement = self._supplement_acknowledgement(takeover)
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
        routing_result = None
        if not is_internal_answer and user_msg is not None:
            routing_result = self._route_supplement_to_children(
                session_id=session_id,
                root_task=runtime_task,
                content=content,
                message_id=user_msg["id"],
            )
        result = {"task": runtime_task, "acceptedMode": "supplement"}
        if routing_result:
            result["supplementRouting"] = routing_result
        if (
            runtime_task.get("status") == "paused"
            and self._pending_user_question_state(self._load_pending_react_state(runtime_task["id"]) or {}) is not None
            and (
                is_internal_answer
                or (takeover or {}).get("state") in (None, "supplement", "continue_requested")
            )
        ):
            try:
                resumed = self.resume_task({"taskId": runtime_task["id"]})["task"]
                result["task"] = resumed
                result["autoResumed"] = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to auto-resume task %s after user answer: %s", runtime_task.get("id"), exc)
        return result

    def _find_existing_internal_question_answer(
        self,
        *,
        task_id: str,
        internal_response: dict[str, Any],
    ) -> dict[str, Any] | None:
        incoming_keys = self._internal_question_answer_keys(internal_response)
        if not incoming_keys:
            return None
        try:
            entries = self._store.list_task_inbox_items(task_id)
        except Exception:  # noqa: BLE001
            return None
        for entry in entries:
            metadata = self._inbox_entry_metadata(entry)
            existing = metadata.get("internalResponse") if isinstance(metadata.get("internalResponse"), dict) else None
            if not existing or existing.get("kind") != "ask_user_question":
                continue
            if incoming_keys & self._internal_question_answer_keys(existing):
                return entry
        return None

    @staticmethod
    def _internal_question_answer_keys(internal_response: dict[str, Any]) -> set[str]:
        keys: set[str] = set()
        for field in ("requestId", "messageId", "toolCallId"):
            value = internal_response.get(field)
            if isinstance(value, str) and value.strip():
                keys.add(f"{field}:{value.strip()}")
        return keys

    @staticmethod
    def _inbox_entry_metadata(entry: dict[str, Any]) -> dict[str, Any]:
        metadata = entry.get("metadata")
        if isinstance(metadata, dict):
            return metadata
        raw = entry.get("metadata_json")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _apply_user_takeover_transition(
        self,
        task: dict[str, Any],
        takeover: dict[str, Any],
    ) -> dict[str, Any]:
        state = takeover.get("state")
        if state in {"wrap_up_requested", "change_requested"}:
            return self._apply_resumable_takeover_convergence(task=task, takeover=takeover)
        transition_by_state = {
            "stop_requested": self.cancel_task,
            "pause_requested": self.pause_task,
            "continue_requested": self.resume_task,
        }
        transition = transition_by_state.get(state)
        if transition is None:
            return task
        try:
            return transition({"taskId": task["id"]})["task"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to apply %s takeover for task %s: %s", state, task.get("id"), exc)
            return task

    @staticmethod
    def _supplement_acknowledgement(takeover: dict[str, Any] | None) -> str:
        state = takeover.get("state") if isinstance(takeover, dict) else None
        if state == "stop_requested":
            return "\u5df2\u505c\u6b62\u5f53\u524d\u4efb\u52a1\u3002"
        if state == "pause_requested":
            return "\u5df2\u8bb0\u5f55\u6682\u505c\u8bf7\u6c42\uff0c\u5f53\u524d\u4efb\u52a1\u4f1a\u5728\u53ef\u4e2d\u65ad\u70b9\u5904\u7406\u3002"
        if state == "wrap_up_requested":
            return "\u5df2\u8bb0\u5f55\u6536\u5c3e\u8bf7\u6c42\uff0c\u540e\u7eed\u4f18\u5148\u8fdb\u5165\u603b\u7ed3\u548c\u9a8c\u6536\u3002"
        if state == "continue_requested":
            return "\u5df2\u8bb0\u5f55\u7ee7\u7eed\u8bf7\u6c42\u3002"
        if state == "change_requested":
            return "\u5df2\u8bb0\u5f55\u76ee\u6807\u53d8\u66f4\u8bf7\u6c42\uff0c\u540e\u7eed\u4f1a\u6309\u65b0\u76ee\u6807\u91cd\u65b0\u5bf9\u9f50\u3002"
        return "\u5df2\u8865\u5145\u5230\u5f53\u524d\u672a\u5b8c\u6210\u4efb\u52a1\uff0c\u7ee7\u7eed\u6cbf\u7528\u539f\u4efb\u52a1\u8ba1\u5212\u3002"

    def _routing_with_user_takeover(self, *, task: dict[str, Any], content: str) -> dict[str, Any]:
        routing = dict(task.get("routing") or {})
        workflow = dict(routing.get("mainWorkflow") or {})
        takeover_history = list(workflow.get("takeoverHistory") or [])
        decision = self._user_takeover_decision(task=task, content=content)
        state = str(decision.get("state") or self._classify_user_takeover(content))
        takeover = {
            "state": state,
            "messagePreview": str(content or "")[:200],
            "taskStatusAtReceipt": task.get("status"),
            "source": decision.get("source") or "rule_fallback",
        }
        for key in ("intent", "reason", "targetGoal", "handoffFocus", "proposalRecordId", "advisorProposalId"):
            if decision.get(key):
                takeover[key] = decision[key]
        takeover_history.append(takeover)
        workflow["userTakeover"] = takeover
        workflow["takeoverHistory"] = takeover_history[-20:]
        routing["mainWorkflow"] = workflow
        return routing

    def _user_takeover_decision(self, *, task: dict[str, Any], content: str) -> dict[str, Any]:
        fallback_state = self._classify_user_takeover(content)
        decision: dict[str, Any] = {
            "state": fallback_state,
            "source": "rule_fallback",
        }
        advisor = getattr(self, "_decision_advisor", None)
        if advisor is None:
            return decision
        try:
            input_context = {
                "message": str(content or ""),
                "task_status": task.get("status"),
                "task_goal": task.get("goal"),
                "current_step": task.get("currentStep"),
                "main_workflow": (task.get("routing") or {}).get("mainWorkflow")
                if isinstance(task.get("routing"), dict)
                else None,
            }
            result = advisor.advise("user_takeover", input_context)
            payload = result.payload if isinstance(result.payload, dict) else {}
            if result.accepted and payload.get("state"):
                decision.update({
                    "state": str(payload.get("state")),
                    "source": result.source,
                    "intent": str(payload.get("intent") or "")[:500],
                    "reason": str(payload.get("reason") or result.rationale or "")[:500],
                    "targetGoal": str(payload.get("target_goal") or "")[:1000],
                    "handoffFocus": str(payload.get("handoff_focus") or "")[:1000],
                    "advisorProposalId": result.proposal_id,
                })
            record_id = self._record_user_takeover_proposal(
                task=task,
                content=content,
                advice=result,
                runtime_state=decision["state"],
            )
            if record_id:
                decision["proposalRecordId"] = record_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("User takeover advisor failed for task %s: %s", task.get("id"), exc)
        return decision

    def _record_user_takeover_proposal(
        self,
        *,
        task: dict[str, Any],
        content: str,
        advice: Any,
        runtime_state: str,
    ) -> str | None:
        try:
            proposal = dict(getattr(advice, "payload", None) or {})
            proposal.setdefault("state", runtime_state)
            proposal["runtimeState"] = runtime_state
            source = {
                "type": getattr(advice, "source", "unknown"),
                "confidence": getattr(advice, "confidence", None),
                "rationale": getattr(advice, "rationale", None),
                "fallbackReason": getattr(advice, "fallback_reason", None),
                "advisorProposalId": getattr(advice, "proposal_id", None),
            }
            model_id = getattr(advice, "model_id", None)
            if model_id:
                source["model_id"] = model_id
            record = self._store.create_proposal({
                "kind": "user_takeover",
                "sessionId": task["sessionId"],
                "taskId": task["id"],
                "proposal": proposal,
                "source": source,
                "inputSummary": str(content or "")[:500],
                "modelId": model_id,
            })
            proposal_id = record["proposal"]["id"]
            accepted = bool(getattr(advice, "accepted", False))
            reasons = [] if accepted else (
                list(getattr(advice, "validation_reasons", None) or [])
                or [str(getattr(advice, "fallback_reason", None) or "advisor rejected")]
            )
            self._store.validate_proposal({
                "proposalId": proposal_id,
                "status": "accepted" if accepted else "rejected",
                "reasons": reasons,
            })
            return proposal_id
        except Exception:  # noqa: BLE001
            logger.debug("Failed to record user takeover proposal", exc_info=True)
            return None

    def _apply_resumable_takeover_convergence(
        self,
        *,
        task: dict[str, Any],
        takeover: dict[str, Any],
    ) -> dict[str, Any]:
        routing = dict(task.get("routing") or {})
        workflow = dict(routing.get("mainWorkflow") or {})
        convergence = dict(workflow.get("convergence") or {})
        state = str(takeover.get("state") or "")
        convergence.update({
            "state": "wrap_up_requested" if state == "wrap_up_requested" else "change_requested",
            "reason": state,
            "resumable": True,
            "requestedBy": "user",
            "messagePreview": takeover.get("messagePreview"),
        })
        if takeover.get("targetGoal"):
            convergence["targetGoal"] = takeover["targetGoal"]
        if takeover.get("handoffFocus"):
            convergence["handoffFocus"] = takeover["handoffFocus"]
        workflow["convergence"] = convergence
        routing["mainWorkflow"] = workflow
        try:
            updated = self._store.update_task(
                task_id=task["id"],
                status=task.get("status"),
                plan=task.get("plan") or [],
                current_step=task.get("currentStep"),
                routing=routing,
            )
            runtime_task = {**task, **updated, "routing": routing}
            self._publish(
                session_id=runtime_task["sessionId"],
                task=runtime_task,
                event_type="task.user_takeover.convergence",
                payload=convergence,
            )
            return runtime_task
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to apply resumable takeover convergence for task %s: %s", task.get("id"), exc)
            return task

    @staticmethod
    def _classify_user_takeover(content: str) -> str:
        text = str(content or "").strip().lower()
        if not text:
            return "supplement"
        if any(marker in text for marker in ("暂停", "停一下", "等一下", "pause", "hold on")):
            return "pause_requested"
        if any(marker in text for marker in ("继续", "接着", "resume", "continue")):
            return "continue_requested"
        if any(marker in text for marker in ("收尾", "总结", "wrap up", "finish up")):
            return "wrap_up_requested"
        if any(marker in text for marker in ("不用了", "关闭", "停止", "取消", "stop", "cancel", "abort")):
            return "stop_requested"
        if any(marker in text for marker in ("改成", "换成", "转为", "instead", "change to")):
            return "change_requested"
        return "supplement"

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
