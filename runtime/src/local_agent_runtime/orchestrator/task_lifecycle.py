from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TaskLifecycleMixin:
    def _complete_task(
        self,
        session_id: str,
        task: dict[str, Any],
        summary: str,
        *,
        context: dict[str, Any] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        skip_reflection: bool = False,
        skip_drain: bool = False,
    ) -> dict[str, Any]:
        validation = self._run_post_task_validation(
            session_id=session_id,
            task=task,
            context=context or {},
            tool_results=tool_results or [],
        )
        final_summary = self._merge_completion_summary(summary=summary, validation=validation)

        # --- Reflection phase ---
        reflection_data = None
        reflection_result = None if skip_reflection else self._reflect_on_result(
            session_id=session_id,
            task=task,
            goal=task.get("goal", ""),
            summary=final_summary,
            context=context or {},
        )
        if reflection_result is not None:
            reflection_data = self._reflector.to_dict(reflection_result)
            if reflection_result.improved_summary:
                final_summary = reflection_result.improved_summary
        # --- End reflection ---

        task["plan"] = self._planner.advance(
            task.get("plan") or [],
            "summarize-findings",
            final_status="completed",
        )
        self._validate_task_transition(task["status"], "completed", task["id"], silent=True)
        if task["status"] in {"completed", "failed", "cancelled"}:
            return {**task, "resultSummary": final_summary}
        # --- Completion decision advisory ---
        completion_advice = self._consult_completion_advisor(task, final_summary, context or {})
        # Build structured result from task fields
        structured_result = {
            "summary": final_summary,
            "status": "success",
            "changedFiles": task.get("changedFiles") or [],
            "testsRun": task.get("testsRun") or [],
            "risks": task.get("risks") or [],
            "keyFindings": [],
        }
        completed_task = self._store.update_task(
            task_id=task["id"],
            status="completed",
            plan=task["plan"],
            summary=final_summary,
            result_summary=final_summary,
            reflection=reflection_data,
            structured_result=structured_result,
        )
        runtime_task = {
            **completed_task,
            "plan": task["plan"],
            "resultSummary": final_summary,
        }
        logger.info("Task %s completed: summary_len=%d", task["id"], len(final_summary))
        # Update existing active assistant message or create a new one
        active_msg_id = runtime_task.get("activeAssistantMessageId")
        if active_msg_id:
            completed_msg = self._store.update_message(
                active_msg_id,
                content=final_summary,
                status="completed",
            )
        else:
            completed_msg = self._store.create_message(
                session_id=session_id,
                task_id=runtime_task["id"],
                role="assistant",
                content=final_summary,
                kind="normal",
                status="completed",
            )
        self._remember_task_result(session_id=session_id, task=runtime_task)
        self._promote_scratchpad_to_memory(session_id)
        self._consolidate_working_memories(session_id)
        self._clear_pending_react_state(task["id"])
        self._record_task_metrics(session_id=session_id, task=runtime_task, tool_results=tool_results, task_status="completed")
        # --- Decision trace: completion ---
        completion_payload = {
            "decision": "completed",
            "whyComplete": final_summary[:500],
            "changedFiles": runtime_task.get("changedFiles") or [],
            "commands": runtime_task.get("commands") or [],
            "testsRun": runtime_task.get("verification") or [],
            "reflection": reflection_data,
            "remainingRisks": runtime_task.get("risks") or [],
        }
        if completion_advice is not None:
            completion_payload["advisorOutcome"] = completion_advice["source"]
            completion_payload["advisorAccepted"] = completion_advice["accepted"]
            if completion_advice.get("rationale"):
                completion_payload["advisorRationale"] = completion_advice["rationale"]
            if completion_advice.get("fallback_reason"):
                completion_payload["advisorFallbackReason"] = completion_advice["fallback_reason"]
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="agent.decision.completion",
            payload=completion_payload,
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="message.completed",
            payload={"messageId": completed_msg["id"], "content": final_summary},
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.completed",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task["plan"],
                "currentStep": runtime_task.get("currentStep"),
                "changedFiles": runtime_task.get("changedFiles") or [],
                "commands": runtime_task.get("commands") or [],
                "verification": runtime_task.get("verification") or [],
                "reflection": reflection_data,
                "summary": final_summary,
                "resultSummary": final_summary,
                "detail": final_summary,
            },
        )
        # Fire after_task_complete hooks
        self._fire_hooks("after_task_complete", session_id, runtime_task)
        # Worker role validation: testsRun and risks should be present
        self._validate_worker_output(session_id=session_id, task=runtime_task)
        if not skip_drain:
            self._drain_session_queue(session_id)
        return runtime_task

    def _consult_completion_advisor(
        self,
        task: dict[str, Any],
        summary: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Ask DecisionAdvisor whether the task is truly complete.

        Returns a dict with advisor result fields, or None if no advisor is configured.
        The advisor proposes, but runtime always proceeds with completion — the result
        is recorded in the decision trace for audit.
        """
        advisor = getattr(self, "_decision_advisor", None)
        if advisor is None:
            return None
        if self._should_skip_completion_advisor(task, context or {}):
            return None
        try:
            input_context: dict[str, Any] = {
                "goal": task.get("goal", ""),
                "summary": summary[:2000],
                "changed_files": [
                    f.get("path", "") for f in (task.get("changedFiles") or [])
                    if isinstance(f, dict)
                ][:20],
            }
            config = (context or {}).get("config") if isinstance(context, dict) else None
            if isinstance(config, dict):
                input_context["config"] = config
            result = advisor.advise("completion_decision", input_context)
            return {
                "accepted": result.accepted,
                "source": result.source,
                "rationale": result.rationale,
                "fallback_reason": result.fallback_reason,
                "proposal_id": result.proposal_id,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Completion advisor call failed for task %s: %s", task.get("id"), exc)
            return None

    def _should_skip_completion_advisor(self, task: dict[str, Any], context: dict[str, Any]) -> bool:
        if context.get("_skip_completion_advisor") is True:
            return True
        if context.get("_child_worker") is True or task.get("role") != "root":
            return not bool(self._advisor_config(context).get("enableChildCompletionAdvisor"))
        return False

    def _validate_worker_output(self, *, session_id: str, task: dict[str, Any]) -> None:
        """Warn if a worker task completes without testsRun or risks."""
        role = task.get("role", "root")
        if role == "root":
            return
        root_task_id = task.get("rootTaskId")
        if not root_task_id or root_task_id == task.get("id"):
            return
        missing: list[str] = []
        if not task.get("testsRun"):
            missing.append("testsRun")
        if not task.get("risks"):
            missing.append("risks")
        if missing:
            self._publish(
                session_id=session_id,
                task=task,
                event_type="task.worker.validation",
                payload={
                    "taskId": task["id"],
                    "missingFields": missing,
                    "warning": f"Worker task completed without required fields: {', '.join(missing)}",
                },
            )
            logger.warning(
                "Worker task %s completed without %s",
                task["id"], ", ".join(missing),
            )

    def _reflect_on_result(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        summary: str,
        context: dict[str, Any],
    ) -> ReflectionResult | None:
        """Conditionally trigger reflection: routing decision + global config enabled."""
        if self._reflector is None:
            return None
        routing = context.get("routing", {})
        if not routing.get("enable_reflection"):
            return None

        self._store.update_task_status(task["id"], "verifying")
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.reflection.started",
            payload={"goal": goal},
        )

        refl_span = self._tracer.start_span(
            "reflection",
            trace_id=getattr(self, "_active_trace_id", None),
            attributes={"taskId": task["id"]},
        )

        tool_output = json.dumps(
            context.get("tool_results", []), ensure_ascii=False,
        )[:2000]

        # Construct retry_fn so the reflection loop can re-generate improved output
        def _retry_fn(feedback: str) -> str:
            retry_prompt = (
                f"你之前的回答存在以下问题:\n{feedback}\n\n"
                f"原始目标: {goal}\n"
                f"请基于以上反馈，重新生成一个改进版的回答。"
            )
            try:
                retry_response = self._provider.generate(
                    retry_prompt,
                    {"messages": [{"role": "user", "content": retry_prompt}]},
                )
                return retry_response.get("message") or retry_response.get("final_answer") or summary
            except Exception:  # noqa: BLE001
                return summary

        result = self._reflector.reflect(
            goal=goal,
            output=summary,
            context=tool_output,
            retry_fn=_retry_fn,
        )

        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.reflection.completed",
            payload={
                "accepted": result.accepted,
                "finalScore": result.final_score,
                "iterations": len(result.iterations),
            },
        )
        self._tracer.end_span(
            refl_span.span_id, status="ok",
            attributes={"accepted": result.accepted, "iterations": len(result.iterations)},
        )
        return result

    def _fail_task(
        self,
        session_id: str,
        task: dict[str, Any],
        summary: str,
        error_code: str,
        *,
        skip_drain: bool = False,
    ) -> dict[str, Any]:
        logger.warning("Task %s failed: error_code=%s summary=%s", task["id"], error_code, summary[:200])
        task_plan = task.get("plan") or []
        self._validate_task_transition(task["status"], "failed", task["id"], silent=True)
        if task["status"] in {"completed", "failed", "cancelled"}:
            return {**task, "errorCode": error_code, "resultSummary": summary}
        failed_task = self._store.update_task(
            task_id=task["id"],
            status="failed",
            plan=task_plan,
            summary=summary,
            result_summary=summary,
            error_code=error_code,
        )
        runtime_task = {
            **failed_task,
            "plan": task_plan,
            "errorCode": error_code,
        }
        # Update existing active assistant message or create a failure message
        active_msg_id = runtime_task.get("activeAssistantMessageId")
        if active_msg_id:
            failed_msg = self._store.update_message(
                active_msg_id,
                content=summary,
                status="failed",
                kind="failure",
            )
        else:
            failed_msg = self._store.create_message(
                session_id=session_id,
                task_id=runtime_task["id"],
                role="assistant",
                content=summary,
                kind="failure",
                status="failed",
            )
        self._remember_task_result(session_id=session_id, task=runtime_task)
        self._promote_scratchpad_to_memory(session_id)
        self._clear_pending_react_state(task["id"])
        self._record_task_metrics(session_id=session_id, task=runtime_task, task_status="failed")
        # --- Decision trace: failure ---
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="agent.decision.completion",
            payload={
                "decision": "failed",
                "whyFailed": runtime_task.get("failureReason") or summary[:500],
                "errorCode": error_code,
            },
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="message.failed",
            payload={"messageId": failed_msg["id"], "content": summary, "errorCode": error_code},
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.failed",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task["plan"],
                "currentStep": runtime_task.get("currentStep"),
                "changedFiles": runtime_task.get("changedFiles") or [],
                "commands": runtime_task.get("commands") or [],
                "verification": runtime_task.get("verification") or [],
                "summary": summary,
                "resultSummary": summary,
                "detail": summary,
                "errorCode": error_code,
            },
        )
        # Fire on_task_failed hooks
        self._fire_hooks("on_task_failed", session_id, runtime_task, extra_context={"errorCode": error_code})
        if not skip_drain:
            self._drain_session_queue(session_id)
        return runtime_task

    def _record_task_metrics(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_results: list[dict[str, Any]] | None = None,
        task_status: str = "completed",
    ) -> None:
        tool_results = tool_results or []
        duration_ms = (task.get("updated_at") or 0) - (task.get("created_at") or 0)
        if duration_ms < 0:
            duration_ms = 0
        command_count = 0
        patch_count = 0
        command_success = 0
        command_failure = 0
        patch_success = 0
        patch_failure = 0
        for tr in tool_results:
            name = tr.get("name", "")
            result = tr.get("result", {})
            if name == "run_command":
                command_count += 1
                status = result.get("status") or result.get("exitCode")
                if status in ("completed", 0):
                    command_success += 1
                else:
                    command_failure += 1
            elif name == "apply_patch":
                patch_count += 1
                if result.get("patch_id") or result.get("applied"):
                    patch_success += 1
                else:
                    patch_failure += 1
        self._store.record_task_metrics({
            "taskId": task["id"],
            "sessionId": session_id,
            "durationMs": duration_ms,
            "toolCallCount": len(tool_results),
            "commandCount": command_count,
            "patchCount": patch_count,
            "commandSuccessCount": command_success,
            "commandFailureCount": command_failure,
            "patchSuccessCount": patch_success,
            "patchFailureCount": patch_failure,
            "taskStatus": task_status,
            "wasCancelled": task_status == "cancelled",
        })

    def _run_post_task_validation(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        patches = self._completed_patch_results(tool_results)
        if not patches:
            return None

        checks: list[dict[str, Any]] = []
        ran: list[str] = []

        if self._workspace_has_git_root(context.get("workspace_root")):
            for tool_name, start_token in (
                ("git_status", "Running post-task git status validation..."),
                ("git_diff", "Running post-task git diff validation..."),
            ):
                check = self._run_validation_tool(
                    session_id=session_id,
                    task=task,
                    tool_name=tool_name,
                    arguments={"workspaceRoot": context.get("workspace_root")},
                    start_token=start_token,
                )
                checks.append(check)
                if check["status"] == "completed":
                    ran.append(tool_name)
        else:
            checks.extend(
                [
                    {
                        "name": "git_status",
                        "status": "skipped",
                        "reason": "Workspace is not a Git repository.",
                    },
                    {
                        "name": "git_diff",
                        "status": "skipped",
                        "reason": "Workspace is not a Git repository.",
                    },
                ]
            )

        validation_command = self._resolve_validation_command(context=context, patches=patches)
        if validation_command:
            command_check = self._run_validation_tool(
                session_id=session_id,
                task=task,
                tool_name="run_command",
                arguments={
                    "workspaceRoot": context.get("workspace_root"),
                    "cwd": ".",
                    "command": validation_command,
                    "internalValidation": True,
                },
                start_token=f"Running post-task validation command: {validation_command}",
            )
            if command_check["status"] == "completed":
                ran.append("run_command")
        else:
            command_check = {
                "name": "run_command",
                "status": "skipped",
                "reason": "No validation command was configured.",
            }
        checks.append(command_check)

        summary = self._format_validation_summary(patches=patches, checks=checks, validation_command=validation_command)
        payload = {
            "patches": patches,
            "checks": checks,
            "ran": ran,
            "command": command_check if command_check["name"] == "run_command" else None,
            "summary": summary,
        }
        self._record_task_verification(session_id=session_id, task=task, validation=payload)
        payload["verification"] = task.get("verification") or []
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.validation.completed",
            payload=payload,
        )
        return payload

    def _workspace_has_git_root(self, workspace_root: Any) -> bool:
        if not isinstance(workspace_root, str) or not workspace_root.strip():
            return False
        root = Path(workspace_root)
        if not root.exists():
            return False
        for candidate in (root, *root.parents):
            if (candidate / ".git").exists():
                return True
        return False

    def _check_git_diff_before_merge(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        execution: dict[str, Any],
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        """Check git diff for conflicts before merging child results.

        Returns a dict with keys:
          - changed_files: list of file paths changed by child tasks
          - conflict_markers: list of files with conflict markers
          - scope_overlaps: list of file paths modified by multiple children
          - warnings: list of human-readable warning strings
          - safe: bool — True if no issues detected
        """
        import subprocess

        result: dict[str, Any] = {
            "changed_files": [],
            "conflict_markers": [],
            "scope_overlaps": [],
            "warnings": [],
            "safe": True,
        }

        # 1. Collect changed files from child task metadata
        child_changed_files: dict[str, list[str]] = {}  # file -> [task_ids]
        for subtask in execution.get("subtasks", []):
            task_id = getattr(subtask, "id", None) or (subtask.get("id") if isinstance(subtask, dict) else None)
            changed = getattr(subtask, "changed_files", None) or (subtask.get("changed_files") if isinstance(subtask, dict) else None) or []
            if isinstance(changed, str):
                try:
                    changed = json.loads(changed)
                except (json.JSONDecodeError, TypeError):
                    changed = []
            for f in changed:
                result["changed_files"].append(f)
                child_changed_files.setdefault(f, []).append(str(task_id))

        # 2. Check scope overlaps (same file modified by multiple children)
        for filepath, task_ids in child_changed_files.items():
            if len(task_ids) > 1:
                result["scope_overlaps"].append(filepath)
                result["warnings"].append(
                    f"File {filepath} was modified by multiple child tasks: {', '.join(task_ids)}"
                )
                result["safe"] = False

        # 2b. Record scope conflict check when overlaps detected
        if result["scope_overlaps"]:
            task_id_val = task.get("id", "")
            overlap_subtask_ids = sorted({tid for f, tids in child_changed_files.items() if len(tids) > 1 for tid in tids})
            scope_map = {tid: [f for f, tids in child_changed_files.items() if tid in tids and len(tids) > 1] for tid in overlap_subtask_ids}
            try:
                self._store.create_scope_conflict_check({
                    "taskId": task_id_val,
                    "sessionId": session_id,
                    "checkType": "pre_merge",
                    "subtaskIds": overlap_subtask_ids,
                    "scopeMap": scope_map,
                    "overlaps": result["scope_overlaps"],
                    "resolution": "merge_required",
                    "safe": False,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to record scope_conflict_check: %s", exc)
            self._publish(
                session_id=session_id, task=task,
                event_type="task.scope.merge.warning",
                payload={
                    "overlaps": result["scope_overlaps"],
                    "overlapCount": len(result["scope_overlaps"]),
                    "resolution": "merge_required",
                },
            )

        # 3. Run git diff checks if workspace root is available
        if workspace_root and self._workspace_has_git_root(workspace_root):
            try:
                # Check for conflict markers
                check_proc = subprocess.run(
                    ["git", "diff", "--check"],
                    capture_output=True, text=True, timeout=10,
                    cwd=workspace_root,
                )
                if check_proc.stdout.strip():
                    conflict_files = set()
                    for line in check_proc.stdout.strip().splitlines():
                        parts = line.split(":", 1)
                        if parts:
                            conflict_files.add(parts[0])
                    result["conflict_markers"] = sorted(conflict_files)
                    result["warnings"].append(
                        f"Git conflict markers detected in: {', '.join(sorted(conflict_files))}"
                    )
                    result["safe"] = False
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                logger.warning("git diff --check failed: %s", exc)

        # 4. Publish merge check event
        if result["warnings"]:
            self._publish(
                session_id=session_id, task=task,
                event_type="task.merge.check",
                payload={
                    "safe": result["safe"],
                    "warnings": result["warnings"],
                    "changedFileCount": len(result["changed_files"]),
                    "overlapCount": len(result["scope_overlaps"]),
                },
            )

        return result

    def _completed_patch_results(self, tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        patches: list[dict[str, Any]] = []
        for tool_result in tool_results:
            if tool_result.get("name") != "apply_patch":
                continue
            result = tool_result.get("result", {})
            if not isinstance(result, dict) or result.get("status") not in {"applied", "completed"}:
                continue
            patch_record = result.get("patch", {}) if isinstance(result.get("patch"), dict) else {}
            patches.append(
                {
                    "summary": result.get("summary") or patch_record.get("summary") or "Updated files",
                    "filesChanged": result.get("filesChanged") or patch_record.get("filesChanged") or 0,
                    "changedPaths": self._changed_paths_from_patch_result(result),
                    "patchId": patch_record.get("id"),
                }
            )
        return patches

    def _changed_paths_from_patch_result(self, result: dict[str, Any]) -> list[str]:
        changed_paths = result.get("changedPaths")
        if isinstance(changed_paths, list):
            return [str(path) for path in changed_paths if str(path).strip()]
        patch_record = result.get("patch")
        if isinstance(patch_record, dict):
            patch_paths = patch_record.get("changedPaths")
            if isinstance(patch_paths, list):
                return [str(path) for path in patch_paths if str(path).strip()]
        diff_text = result.get("diffText")
        if isinstance(diff_text, str):
            return self._changed_paths_from_diff_text(diff_text)
        return []

    def _changed_paths_from_diff_text(self, diff_text: str) -> list[str]:
        paths: list[str] = []
        for line in diff_text.splitlines():
            if line.startswith("+++ "):
                path = line[4:].strip()
                if path == "/dev/null":
                    continue
                paths.append(path[2:] if path.startswith("b/") else path)
            elif line.startswith("--- "):
                path = line[4:].strip()
                if path == "/dev/null":
                    continue
                normalized = path[2:] if path.startswith("a/") else path
                if normalized not in paths:
                    paths.append(normalized)
        return list(dict.fromkeys(path for path in paths if path))

    def _run_validation_tool(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any],
        start_token: str,
    ) -> dict[str, Any]:
        try:
            tool_result = self._execute_tool(
                session_id=session_id,
                task=task,
                tool_spec={
                    "name": tool_name,
                    "arguments": arguments,
                    "plan_step_id": f"validation-{tool_name.replace('_', '-')}",
                    "start_token": start_token,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "name": tool_name,
                "status": "failed",
                "error": str(exc),
            }

        result = tool_result.get("result", {})
        status = result.get("status")
        if not isinstance(status, str) or not status:
            status = "completed"
        check = {
            "name": tool_name,
            "status": status,
            "result": result,
        }
        if tool_name == "run_command" and isinstance(arguments.get("command"), str):
            check["command"] = arguments["command"]
        return check

    def _record_task_verification(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        validation: dict[str, Any],
    ) -> None:
        records = self._verification_records_from_validation(validation)
        if not records:
            return
        updated_task = self._store.update_task(
            task_id=task["id"],
            verification=records,
        )
        task.update(updated_task)
        self._publish_task_run_snapshot(session_id=session_id, task=task)

    def _verification_records_from_validation(self, validation: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for check in validation.get("checks", []):
            if not isinstance(check, dict):
                continue
            result = check.get("result") if isinstance(check.get("result"), dict) else {}
            command_log = result.get("commandLog") if isinstance(result.get("commandLog"), dict) else {}
            command = check.get("command")
            if not isinstance(command, str):
                command = result.get("command") if isinstance(result.get("command"), str) else None
            records.append(
                {
                    "id": command_log.get("id"),
                    "command": command,
                    "status": self._verification_status(check.get("status")),
                    "exitCode": result.get("exitCode") if isinstance(result, dict) else None,
                    "durationMs": result.get("durationMs") if isinstance(result, dict) else None,
                    "summary": self._validation_check_summary(check),
                    "startedAt": command_log.get("startedAt"),
                    "finishedAt": command_log.get("finishedAt"),
                }
            )
        return records

    def _verification_status(self, status: Any) -> str:
        if status == "completed":
            return "passed"
        if status in {"failed", "timeout", "killed", "validation_failed"}:
            return "failed"
        if status == "skipped":
            return "skipped"
        if status == "running":
            return "running"
        return str(status or "not_run")

    def _validation_check_summary(self, check: dict[str, Any]) -> str:
        if isinstance(check.get("reason"), str):
            return check["reason"]
        if isinstance(check.get("error"), str):
            return check["error"]
        result = check.get("result") if isinstance(check.get("result"), dict) else {}
        if isinstance(result.get("summary"), str):
            return result["summary"]
        if isinstance(result.get("stderr"), str) and result["stderr"].strip():
            return result["stderr"].strip().splitlines()[0]
        if isinstance(result.get("stdout"), str) and result["stdout"].strip():
            return result["stdout"].strip().splitlines()[0]
        return f"{check.get('name', 'validation')} {check.get('status', 'not_run')}"

    def _resolve_validation_command(self, *, context: dict[str, Any], patches: list[dict[str, Any]]) -> str | None:
        validation = context.get("post_task_validation")
        if isinstance(validation, dict):
            command = validation.get("command")
            if isinstance(command, str) and command.strip():
                return command.strip()

        changed_test_paths: list[str] = []
        for patch in patches:
            for path in patch.get("changedPaths", []):
                normalized = str(path).replace("\\", "/")
                if normalized.endswith(".py") and ("/tests/" in normalized or normalized.startswith("tests/")):
                    changed_test_paths.append(normalized)
        if changed_test_paths:
            ordered_paths = list(dict.fromkeys(changed_test_paths))
            return "pytest " + " ".join(ordered_paths)
        return None

    def _format_validation_summary(
        self,
        *,
        patches: list[dict[str, Any]],
        checks: list[dict[str, Any]],
        validation_command: str | None,
    ) -> str:
        changed_summaries = list(dict.fromkeys(str(patch["summary"]).strip() for patch in patches if str(patch["summary"]).strip()))
        changed_text = f"Changed: {'; '.join(changed_summaries)}." if changed_summaries else ""

        completed_names: list[str] = []
        for check in checks:
            if check.get("status") != "completed":
                continue
            if check["name"] == "git_status":
                completed_names.append("git status")
            elif check["name"] == "git_diff":
                completed_names.append("git diff")
            elif check["name"] == "run_command" and validation_command:
                completed_names.append(validation_command)

        validation_text = ""
        if completed_names:
            if len(completed_names) == 1:
                validation_text = f"Validated with {completed_names[0]}."
            else:
                validation_text = f"Validated with {', '.join(completed_names[:-1])}, and {completed_names[-1]}."

        failed_checks = [check for check in checks if check.get("status") == "failed"]
        failure_text = ""
        if failed_checks:
            failure_text = " Validation issues: " + " ".join(
                f"{check['name']} failed: {check.get('error', 'unknown error')}." for check in failed_checks
            )

        return " ".join(part for part in (changed_text, validation_text) if part).strip() + failure_text

    def _record_task_run_tool_result(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_result: dict[str, Any],
    ) -> None:
        tool_name = tool_result.get("name")
        result = tool_result.get("result")
        if not isinstance(result, dict):
            return

        update: dict[str, Any] = {}
        if tool_name == "apply_patch":
            changed_files = self._merge_changed_files(
                task.get("changedFiles") or [],
                self._changed_files_from_patch_result(result),
            )
            if changed_files != (task.get("changedFiles") or []):
                update["changed_files"] = changed_files
        elif tool_name == "write_file":
            changed_files = self._merge_changed_files(
                task.get("changedFiles") or [],
                self._changed_files_from_write_file_result(result),
            )
            if changed_files != (task.get("changedFiles") or []):
                update["changed_files"] = changed_files
        elif tool_name == "run_command":
            arguments = tool_result.get("arguments") if isinstance(tool_result.get("arguments"), dict) else {}
            commands = self._merge_command_records(
                task.get("commands") or [],
                self._command_record_from_result(result, arguments),
            )
            if commands != (task.get("commands") or []):
                update["commands"] = commands

        if not update:
            return

        updated_task = self._store.update_task(task_id=task["id"], **update)
        task.update(updated_task)
        self._publish_task_run_snapshot(session_id=session_id, task=task)

    def _changed_files_from_patch_result(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        if result.get("status") not in {"applied", "completed"}:
            return []
        summary = result.get("summary")
        patch = result.get("patch") if isinstance(result.get("patch"), dict) else {}
        patch_id = result.get("patchId") or patch.get("id")
        return [
            {
                "path": path,
                "status": self._patch_file_status(result.get("diffText"), path),
                "reason": summary if isinstance(summary, str) else None,
                "patchId": patch_id,
            }
            for path in self._changed_paths_from_patch_result(result)
        ]

    def _changed_files_from_write_file_result(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        if result.get("status") != "written":
            return []
        path = result.get("path")
        if not isinstance(path, str) or not path.strip():
            return []
        return [
            {
                "path": path,
                "status": "added" if result.get("created") is True else "modified",
                "reason": f"write_file wrote {result.get('bytesWritten', 0)} byte(s)",
            }
        ]

    def _patch_file_status(self, diff_text: Any, path: str) -> str:
        if not isinstance(diff_text, str):
            return "modified"
        normalized = path.replace("\\", "/")
        for section in diff_text.split("diff --git "):
            if not section.strip() or normalized not in section.replace("\\", "/"):
                continue
            if "\n--- /dev/null" in section:
                return "added"
            if "\n+++ /dev/null" in section:
                return "deleted"
            if "\nrename from " in section and "\nrename to " in section:
                return "renamed"
        return "modified"

    def _merge_changed_files(
        self,
        current: list[dict[str, Any]],
        additions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in current:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                merged[item["path"]] = dict(item)
        for item in additions:
            path = item.get("path")
            if isinstance(path, str) and path.strip():
                merged[path] = {**merged.get(path, {}), **item}
        return list(merged.values())

    def _command_record_from_result(
        self,
        result: dict[str, Any],
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        if result.get("status") == "approval_required":
            return None
        command_log = result.get("commandLog") if isinstance(result.get("commandLog"), dict) else {}
        command = command_log.get("command") or result.get("command") or arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        return {
            "id": command_log.get("id"),
            "command": command.strip(),
            "cwd": command_log.get("cwd") or result.get("cwd") or arguments.get("cwd"),
            "shell": result.get("shell") or arguments.get("shell"),
            "status": command_log.get("status") or result.get("status"),
            "exitCode": command_log.get("exitCode") if command_log.get("exitCode") is not None else result.get("exitCode"),
            "durationMs": command_log.get("durationMs") if command_log.get("durationMs") is not None else result.get("durationMs"),
            "summary": self._command_result_summary(result),
            "startedAt": command_log.get("startedAt"),
            "finishedAt": command_log.get("finishedAt"),
            "stdoutPath": command_log.get("stdoutPath"),
            "stderrPath": command_log.get("stderrPath"),
            "background": result.get("background") is True,
        }

    def _merge_command_records(
        self,
        current: list[dict[str, Any]],
        addition: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not addition:
            return current
        merged: list[dict[str, Any]] = []
        replaced = False
        addition_id = addition.get("id")
        for item in current:
            if addition_id and isinstance(item, dict) and item.get("id") == addition_id:
                merged.append({**item, **addition})
                replaced = True
            else:
                merged.append(item)
        if not replaced:
            merged.append(addition)
        return merged

    def _command_result_summary(self, result: dict[str, Any]) -> str:
        status = result.get("status") or "completed"
        stderr = result.get("stderr")
        stdout = result.get("stdout")
        if isinstance(stderr, str) and stderr.strip():
            return stderr.strip().splitlines()[0]
        if isinstance(stdout, str) and stdout.strip():
            return stdout.strip().splitlines()[0]
        exit_code = result.get("exitCode")
        return f"Command {status}" + (f" with exit {exit_code}" if exit_code is not None else "")

