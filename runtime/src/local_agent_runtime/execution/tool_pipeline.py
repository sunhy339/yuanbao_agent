"""Tool Execution Pipeline — extracted from ReactRunnerMixin.

Handles tool validation, approval gating, execution, and observation.
Design target:

    before_tool_call hooks
      -> schema validation
      -> policy guard
      -> approval gate
      -> worktree path enforcement (initially disabled)
      -> execute tool
      -> after_tool_call hooks
      -> trace/store observation
"""
from __future__ import annotations

import json
import re as _re
from typing import Any

from ..services.worker_budget import WorkerBudget

_WORKTREE_BOUND_TOOLS = {
    "list_dir",
    "search_files",
    "read_file",
    "run_command",
    "apply_patch",
    "git_status",
    "git_diff",
    "write_file",
    "code_search",
}
_ACTIVE_WORKTREE_STATUSES = {"creating", "active", "paused", "ready_for_review"}


class ToolExecutionMixin:
    """Mixin providing the full tool execution pipeline."""

    # -- Child worker safety guards --------------------------------------------

    def _ensure_tool_allowed_for_child_worker(self, tool_name: str) -> None:
        allowed = self._child_tool_allowlist()
        if allowed is None:
            return
        allowed_set = set(allowed)
        if tool_name in allowed_set or (tool_name.startswith("mcp__") and "mcp__*" in allowed_set):
            return
        raise ValueError(f"Tool is not allowed in child worker process: {tool_name}")

    def _ensure_command_safe_for_child_worker(self, command: str) -> None:
        """Block git commit/push in child workers — only root may commit."""
        allowed = self._child_tool_allowlist()
        if allowed is None:
            return  # root task, no restriction
        blocked = (
            _re.compile(r"\bgit\s+commit\b", _re.IGNORECASE),
            _re.compile(r"\bgit\s+push\b", _re.IGNORECASE),
        )
        for pattern in blocked:
            if pattern.search(command):
                raise ValueError(
                    "Child workers cannot run git commit/push directly. "
                    "Only the root task may commit changes."
                )

    # -- Budget consumption ----------------------------------------------------

    def _consume_budget_for_tool_call(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        budget: WorkerBudget | None,
        tool_name: str,
    ) -> None:
        if budget is None:
            return
        consumed = budget.consume_tool_call()
        self._publish(
            session_id=session_id,
            task=task,
            event_type="collab.worker.budget.updated",
            payload={
                "dimension": "toolCalls",
                "consumed": consumed,
                "toolName": tool_name,
                "budget": budget.to_metadata(),
            },
        )

    # -- Main execution pipeline -----------------------------------------------

    def _apply_task_worktree_to_tool_arguments(
        self,
        *,
        task_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name not in _WORKTREE_BOUND_TOOLS:
            return arguments
        try:
            worktree = self._store.get_worktree_by_task({"taskId": task_id}).get("worktree")
        except Exception:  # noqa: BLE001
            return arguments
        if not isinstance(worktree, dict):
            return arguments
        if worktree.get("status") not in _ACTIVE_WORKTREE_STATUSES:
            return arguments
        worktree_path = worktree.get("worktreePath")
        if not isinstance(worktree_path, str) or not worktree_path.strip():
            return arguments

        original_root = arguments.get("workspaceRoot") or arguments.get("workspace_root")
        bound = dict(arguments)
        if original_root and original_root != worktree_path:
            bound["originalWorkspaceRoot"] = original_root
        bound["workspaceRoot"] = worktree_path
        bound["activeWorktreeId"] = worktree.get("id")
        if tool_name == "run_command":
            bound.setdefault("cwd", ".")
        return bound

    def _execute_tool(
        self,
        session_id: str,
        task: dict[str, Any],
        tool_spec: dict[str, Any],
        budget: WorkerBudget | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_tool_allowed_for_child_worker(tool_spec["name"])
        # Child workers cannot run git commit/push via run_command
        if tool_spec["name"] == "run_command":
            command = tool_spec.get("arguments", {}).get("command", "")
            if isinstance(command, str):
                self._ensure_command_safe_for_child_worker(command)
        tool_call_id = tool_spec.get("id") or self._store.new_id("tc")
        tool_arguments = {
            **tool_spec["arguments"],
            "taskId": task["id"],
            "sessionId": session_id,
        }
        if isinstance(context, dict):
            untrusted_signals = context.get("untrustedContentSignals")
            if isinstance(untrusted_signals, list) and untrusted_signals:
                tool_arguments["untrustedContentSignals"] = [dict(item) for item in untrusted_signals if isinstance(item, dict)]
        tool_arguments = self._apply_task_worktree_to_tool_arguments(
            task_id=task["id"],
            tool_name=tool_spec["name"],
            arguments=tool_arguments,
        )
        self._consume_budget_for_tool_call(
            session_id=session_id,
            task=task,
            budget=budget,
            tool_name=tool_spec["name"],
        )
        self._publish(
            session_id=session_id,
            task=task,
            event_type="assistant.token",
            payload={"delta": tool_spec["start_token"]},
        )
        self._publish(
            session_id=session_id,
            task=task,
            event_type="tool.started",
            payload={
                "toolCallId": tool_call_id,
                "toolName": tool_spec["name"],
                "arguments": tool_arguments,
            },
        )
        self._fire_hooks("before_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"]})
        if tool_spec["name"] == "apply_patch":
            self._fire_hooks("before_patch_apply", session_id, task, extra_context={"toolCallId": tool_call_id, "patchArguments": tool_spec.get("arguments", {})})
        # MCP-specific lifecycle event
        is_mcp_tool = tool_spec["name"].startswith("mcp__")
        if is_mcp_tool:
            parts = tool_spec["name"].split("__", 2)
            mcp_server_id = parts[1] if len(parts) >= 2 else ""
            self._publish_mcp_event("mcp.tool.started", {
                "toolCallId": tool_call_id,
                "toolName": tool_spec["name"],
                "serverId": mcp_server_id,
            })
        tool_span = self._tracer.start_span(
            "tool_call",
            trace_id=getattr(self, "_active_trace_id", None),
            parent_span_id=getattr(self, "_active_parent_span_id", None),
            attributes={"toolName": tool_spec["name"]},
        )
        try:
            if tool_spec["name"] == "task":
                self._fire_hooks("before_subagent_start", session_id, task, extra_context={"toolArguments": tool_arguments})
                try:
                    result = self._subagent_service.dispatch(tool_arguments)
                    sub_status = result.get("status", "")
                    if sub_status == "failed":
                        self._fire_hooks("on_subagent_failed", session_id, task, extra_context={"subagentResult": result})
                    else:
                        self._fire_hooks("after_subagent_complete", session_id, task, extra_context={"subagentResult": result})
                except Exception as sub_exc:
                    self._fire_hooks("on_subagent_failed", session_id, task, extra_context={"subagentError": str(sub_exc)})
                    raise
            else:
                result = self._tool_registry.execute(tool_spec["name"], tool_arguments, session_id=session_id)
            self._tracer.end_span(tool_span.span_id, status="ok")
        except Exception as exc:  # noqa: BLE001
            import asyncio as _asyncio
            is_timeout = isinstance(exc, _asyncio.TimeoutError)
            result = {
                "status": "failed",
                "ok": False,
                "error": str(exc),
                "summary": f"Tool {tool_spec['name']} raised an exception: {exc}",
            }
            if is_timeout:
                result["timeout"] = True
            self._tracer.end_span(tool_span.span_id, status="error")
            if is_mcp_tool:
                event_name = "mcp.tool.timeout" if is_timeout else "mcp.tool.failed"
                self._publish_mcp_event(event_name, {
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "serverId": mcp_server_id,
                    "error": str(exc),
                    "timeout": is_timeout,
                })
        if tool_spec["name"] == "run_command":
            command_log = result.get("commandLog") or {}
            command_id = command_log.get("id")
            if command_id and result.get("stdout"):
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="command.output",
                    payload={
                        "commandId": command_id,
                        "stream": "stdout",
                        "chunk": result["stdout"],
                    },
                )
            if command_id and result.get("stderr"):
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="command.output",
                    payload={
                        "commandId": command_id,
                        "stream": "stderr",
                        "chunk": result["stderr"],
                    },
                )

        if tool_spec["name"] == "task":
            tool_result = {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.completed",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "arguments": tool_arguments,
                    "result": result,
                },
            )
            return tool_result

        if result.get("status") == "blocked":
            failure = self._annotate_failed_tool_recovery(
                session_id=session_id,
                task=task,
                tool_call_id=tool_call_id,
                tool_name=tool_spec["name"],
                arguments=tool_arguments,
                result=result,
            )
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.blocked",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "reason": result.get("error", "Blocked by permission policy."),
                    "failureKind": failure.get("failureKind"),
                    "recoveryHint": failure.get("recoveryHint"),
                    "recoveryDecision": result.get("recoveryDecision"),
                },
            )
            return {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }

        if result.get("status") == "approval_required":
            approval = result.get("approval", {})
            self._validate_task_transition(task["status"], "waiting_approval", task["id"])
            task["status"] = "waiting_approval"
            self._store.update_task(task_id=task["id"], status="waiting_approval", plan=task["plan"])
            if tool_spec["name"] == "apply_patch":
                patch = result.get("patch", {})
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="patch.proposed",
                    payload={
                        "patchId": patch.get("id"),
                        "summary": patch.get("summary", ""),
                        "filesChanged": patch.get("filesChanged", 0),
                    },
                )
            self._publish(
                session_id=session_id,
                task=task,
                event_type="approval.requested",
                payload={
                    "approvalId": approval.get("id"),
                    "taskId": task["id"],
                    "kind": approval.get("kind", tool_spec["name"]),
                    "request": json.loads(approval.get("requestJson", "{}")),
                    "patchId": result.get("patch", {}).get("id"),
                },
            )
            self._fire_hooks("on_approval_required", session_id, task, extra_context={"approvalId": approval.get("id"), "kind": approval.get("kind", tool_spec["name"])})
            self._publish(
                session_id=session_id,
                task=task,
                event_type="task.waiting_approval",
                payload={
                    "status": "waiting_approval",
                    "detail": "执行前需要先审批补丁。"
                    if tool_spec["name"] == "apply_patch"
                    else "执行前需要先审批命令。",
                },
            )
            tool_result = {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.completed",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "arguments": tool_arguments,
                    "result": result,
                },
            )
            return tool_result

        if self._is_patch_validation_failure(tool_spec["name"], result):
            self._annotate_failed_tool_recovery(
                session_id=session_id,
                task=task,
                tool_call_id=tool_call_id,
                tool_name=tool_spec["name"],
                arguments=tool_arguments,
                result=result,
            )
            tool_result = {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.failed",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "arguments": tool_arguments,
                    "result": result,
                },
            )
            return tool_result

        if self._tool_failed(tool_spec["name"], result):
            failure = self._annotate_failed_tool_recovery(
                session_id=session_id,
                task=task,
                tool_call_id=tool_call_id,
                tool_name=tool_spec["name"],
                arguments=tool_arguments,
                result=result,
            )
            tool_result = {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }
            self._record_task_run_tool_result(session_id=session_id, task=task, tool_result=tool_result)
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.failed",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "arguments": tool_arguments,
                    "result": result,
                },
            )
            self._fire_hooks("after_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"], "toolStatus": "failed"})
            if is_mcp_tool:
                is_timeout = bool(result.get("timeout"))
                event_name = "mcp.tool.timeout" if is_timeout else "mcp.tool.failed"
                self._publish_mcp_event(event_name, {
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "serverId": mcp_server_id,
                    "error": result.get("error", f"Tool returned status: {result.get('status')}"),
                    "timeout": is_timeout,
                    "failureKind": failure.get("failureKind"),
                    "recoveryHint": failure.get("recoveryHint"),
                    "recoveryDecision": result.get("recoveryDecision"),
                })
            return tool_result

        if tool_spec["name"] == "run_command":
            tool_result = {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }
            self._record_task_run_tool_result(session_id=session_id, task=task, tool_result=tool_result)
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.completed",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "arguments": tool_arguments,
                    "result": result,
                },
            )
            self._fire_hooks("after_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"], "toolStatus": "completed"})
            return tool_result

        if tool_spec["name"] in {"apply_patch", "write_file"}:
            tool_result = {
                "id": tool_call_id,
                "name": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            }
            self._record_task_run_tool_result(session_id=session_id, task=task, tool_result=tool_result)
            self._publish(
                session_id=session_id,
                task=task,
                event_type="tool.completed",
                payload={
                    "toolCallId": tool_call_id,
                    "toolName": tool_spec["name"],
                    "arguments": tool_arguments,
                    "result": result,
                },
            )
            self._fire_hooks("after_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"], "toolStatus": "completed"})
            if tool_spec["name"] == "apply_patch":
                self._fire_hooks("after_patch_apply", session_id, task, extra_context={"toolCallId": tool_call_id, "patchResult": result})
            return tool_result

        tool_result = {
            "id": tool_call_id,
            "name": tool_spec["name"],
            "arguments": tool_arguments,
            "result": result,
        }
        self._publish(
            session_id=session_id,
            task=task,
            event_type="tool.completed",
            payload={
                "toolCallId": tool_call_id,
                "toolName": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            },
        )
        if is_mcp_tool:
            self._publish_mcp_event("mcp.tool.completed", {
                "toolCallId": tool_call_id,
                "toolName": tool_spec["name"],
                "serverId": mcp_server_id,
                "ok": result.get("ok", True),
            })
        self._fire_hooks("after_tool_call", session_id, task, extra_context={"toolCallId": tool_call_id, "toolName": tool_spec["name"], "toolStatus": "completed"})
        if tool_spec["name"] == "memory.remember" and result.get("ok", True):
            self._fire_hooks("on_memory_write", session_id, task, extra_context={"toolCallId": tool_call_id, "memoryResult": result})
        return tool_result
