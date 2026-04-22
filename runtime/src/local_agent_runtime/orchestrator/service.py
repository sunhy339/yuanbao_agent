from __future__ import annotations

import json
from typing import Any

from ..context.builder import ContextBuilder
from ..event_bus import EventBus
from ..models import RuntimeEvent
from ..planner.service import Planner
from ..services.session_service import SessionService


class Orchestrator:
    """Coordinates the first-pass agent loop for Sprint 1."""

    def __init__(
        self,
        store: Any,
        event_bus: EventBus,
        tool_registry: Any,
        provider: Any,
    ) -> None:
        self._store = store
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._provider = provider
        self._planner = Planner()
        self._context_builder = ContextBuilder(store)
        self._session_service = SessionService(store)

    def open_workspace(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace = self._store.upsert_workspace(path=params["path"])
        return {"workspace": workspace}

    def create_session(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session_service.create_session(
            workspace_id=params["workspaceId"],
            title=params["title"],
        )
        return {"session": session}

    def send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._store.require_session(params["sessionId"])
        context = self._context_builder.build(session_id=session["id"], goal=params["content"])
        plan = self._planner.plan(params["content"], context=context)
        task = self._store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal=params["content"],
            plan=plan,
        )
        runtime_task = {**task, "plan": plan}

        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="task.started",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task["plan"],
                "context": context,
            },
        )
        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="assistant.token",
            payload={"delta": "Building context and preparing the first tool calls..."},
        )

        try:
            tool_results = self._run_minimal_loop(
                session_id=session["id"],
                task=runtime_task,
                goal=params["content"],
                context=context,
            )
            if runtime_task["status"] == "waiting_approval":
                return {"task": runtime_task}
            summary = self._provider.summarize_findings(
                goal=params["content"],
                context=context,
                tool_results=tool_results,
            )
            self._publish(
                session_id=session["id"],
                task=runtime_task,
                event_type="assistant.token",
                payload={"delta": summary},
            )

            runtime_task["plan"] = self._planner.advance(
                runtime_task["plan"],
                "summarize-findings",
                final_status="completed",
            )
            completed_task = self._store.update_task(
                task_id=task["id"],
                status="completed",
                plan=runtime_task["plan"],
                result_summary=summary,
            )
            runtime_task = {
                **completed_task,
                "plan": runtime_task["plan"],
                "resultSummary": summary,
            }
            self._publish(
                session_id=session["id"],
                task=runtime_task,
                event_type="task.completed",
                payload={
                    "status": runtime_task["status"],
                    "plan": runtime_task["plan"],
                    "detail": summary,
                },
            )
            return {"task": runtime_task}
        except Exception as exc:  # noqa: BLE001
            failed_task = self._store.update_task(
                task_id=task["id"],
                status="failed",
                plan=runtime_task["plan"],
                error_code="LOOP_EXECUTION_FAILED",
            )
            runtime_task = {
                **failed_task,
                "plan": runtime_task["plan"],
                "errorCode": "LOOP_EXECUTION_FAILED",
            }
            self._publish(
                session_id=session["id"],
                task=runtime_task,
                event_type="task.failed",
                payload={
                    "status": runtime_task["status"],
                    "plan": runtime_task["plan"],
                    "detail": str(exc),
                    "errorCode": "LOOP_EXECUTION_FAILED",
                },
            )
            return {"task": runtime_task}

    def cancel_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task = self._store.update_task(task_id=params["taskId"], status="cancelled")
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="task.updated",
            payload={"status": task["status"]},
        )
        return {"task": task}

    def submit_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        approval = self._store.resolve_approval(
            approval_id=params["approvalId"],
            decision=params["decision"],
        )
        task = self._store.get_task({"taskId": approval["taskId"]})["task"]
        if approval["decision"] == "approved":
            task = self._store.update_task_status(task_id=approval["taskId"], status="running")
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="task.updated",
                payload={"status": "running", "detail": "Approval accepted"},
            )
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="approval.resolved",
            payload={
                "approvalId": approval["id"],
                "taskId": task["id"],
                "decision": approval["decision"],
            },
        )
        if approval["decision"] == "approved" and approval["kind"] == "run_command":
            task = self._resume_approved_command(task=task, approval=approval)
        return {"approval": approval}

    def _resume_approved_command(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(approval.get("requestJson") or "{}")
        tool_spec = {
            "name": "run_command",
            "arguments": {
                **request,
                "approvalId": approval["id"],
            },
            "plan_step_id": "run-command",
            "start_token": "Approval accepted. Running the command now...",
        }
        runtime_task = {**task, "plan": task.get("plan") or []}
        try:
            tool_result = self._execute_tool(
                session_id=task["sessionId"],
                task=runtime_task,
                tool_spec=tool_spec,
            )
            runtime_task["plan"] = self._planner.advance(
                runtime_task["plan"],
                "run-command",
                next_step_id="summarize-findings",
            )
            runtime_task["plan"] = self._planner.advance(
                runtime_task["plan"],
                "summarize-findings",
                final_status="completed",
            )
            command_result = tool_result.get("result", {})
            command_log = command_result.get("commandLog", {})
            summary = (
                f"Approved command finished with status {command_result.get('status')} "
                f"and exit code {command_result.get('exitCode')}."
            )
            completed_task = self._store.update_task(
                task_id=task["id"],
                status="completed",
                plan=runtime_task["plan"],
                result_summary=summary,
            )
            runtime_task = {
                **completed_task,
                "plan": runtime_task["plan"],
                "resultSummary": summary,
            }
            self._publish(
                session_id=task["sessionId"],
                task=runtime_task,
                event_type="task.completed",
                payload={
                    "status": "completed",
                    "plan": runtime_task["plan"],
                    "detail": summary,
                    "commandLogId": command_log.get("id"),
                },
            )
            return runtime_task
        except Exception as exc:  # noqa: BLE001
            failed_task = self._store.update_task(
                task_id=task["id"],
                status="failed",
                plan=runtime_task["plan"],
                error_code="COMMAND_EXECUTION_FAILED",
            )
            self._publish(
                session_id=task["sessionId"],
                task=failed_task,
                event_type="task.failed",
                payload={
                    "status": "failed",
                    "plan": runtime_task["plan"],
                    "detail": str(exc),
                    "errorCode": "COMMAND_EXECUTION_FAILED",
                },
            )
            return failed_task

    def _publish(self, session_id: str, task: dict[str, Any], event_type: str, payload: dict[str, Any]) -> None:
        event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id=session_id,
            task_id=task["id"],
            type=event_type,
            ts=self._store.now(),
            payload=payload,
        )
        self._event_bus.publish(event)

    def _run_minimal_loop(
        self,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        tool_results: list[dict[str, Any]] = []
        tool_sequence = self._provider.choose_tool_sequence(goal=goal, context=context)

        for index, tool_spec in enumerate(tool_sequence):
            tool_result = self._execute_tool(
                session_id=session_id,
                task=task,
                tool_spec=tool_spec,
            )
            tool_results.append(tool_result)
            if task["status"] == "waiting_approval":
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="task.updated",
                    payload={"status": task["status"], "plan": task["plan"]},
                )
                return tool_results
            task["plan"] = self._planner.advance(
                task["plan"],
                tool_spec["plan_step_id"],
                next_step_id=self._next_step_id(tool_sequence, index),
            )
            self._store.update_task(
                task_id=task["id"],
                status=task["status"],
                plan=task["plan"],
            )
            self._publish(
                session_id=session_id,
                task=task,
                event_type="task.updated",
                payload={"status": task["status"], "plan": task["plan"]},
            )

        follow_up_tool = self._provider.pick_follow_up_tool(context=context, tool_results=tool_results)
        if follow_up_tool is not None:
            tool_results.append(
                self._execute_tool(
                    session_id=session_id,
                    task=task,
                    tool_spec=follow_up_tool,
                )
            )

        task["plan"] = self._planner.advance(
            task["plan"],
            "search-relevant-files",
            next_step_id="summarize-findings",
        )
        self._store.update_task(
            task_id=task["id"],
            status=task["status"],
            plan=task["plan"],
        )
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.updated",
            payload={"status": task["status"], "plan": task["plan"]},
        )
        self._publish(
            session_id=session_id,
            task=task,
            event_type="assistant.token",
            payload={"delta": "Completed the minimal tool loop and preparing a summary..."},
        )
        return tool_results

    def _execute_tool(
        self,
        session_id: str,
        task: dict[str, Any],
        tool_spec: dict[str, Any],
    ) -> dict[str, Any]:
        tool_call_id = self._store.new_id("tc")
        tool_arguments = {
            **tool_spec["arguments"],
            "taskId": task["id"],
            "sessionId": session_id,
        }
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
        result = self._tool_registry.execute(tool_spec["name"], tool_arguments)
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

            if result.get("status") == "approval_required":
                approval = result.get("approval", {})
                task["status"] = "waiting_approval"
                self._store.update_task(task_id=task["id"], status="waiting_approval", plan=task["plan"])
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="approval.requested",
                    payload={
                        "approvalId": approval.get("id"),
                        "taskId": task["id"],
                        "kind": approval.get("kind", "run_command"),
                        "request": json.loads(approval.get("requestJson", "{}")),
                    },
                )
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="task.waiting_approval",
                    payload={
                        "status": "waiting_approval",
                        "detail": "Command requires approval before execution.",
                    },
                )
            elif result.get("status") in {"failed", "timeout", "killed"}:
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="tool.failed",
                    payload={
                        "toolCallId": tool_call_id,
                        "toolName": tool_spec["name"],
                        "arguments": tool_spec["arguments"],
                        "result": result,
                    },
                )
                raise RuntimeError(f"Command execution {result.get('status')}")

        tool_result = {
            "id": tool_call_id,
            "name": tool_spec["name"],
            "arguments": tool_arguments,
            "result": result,
        }
        if tool_spec["name"] == "run_command" and result.get("status") == "approval_required":
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
        self._publish(
            session_id=session_id,
            task=task,
            event_type="tool.completed" if result.get("status") not in {"failed", "timeout", "killed"} else "tool.failed",
            payload={
                "toolCallId": tool_call_id,
                "toolName": tool_spec["name"],
                "arguments": tool_arguments,
                "result": result,
            },
        )
        return tool_result

    def _next_step_id(self, tool_sequence: list[dict[str, Any]], current_index: int) -> str | None:
        if current_index + 1 >= len(tool_sequence):
            return "summarize-findings"
        return tool_sequence[current_index + 1]["plan_step_id"]
