from __future__ import annotations

import json
import os
from copy import deepcopy
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
        self._pending_react_tasks: dict[str, dict[str, Any]] = {}

    def open_workspace(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace = self._store.upsert_workspace(path=params["path"])
        return {"workspace": workspace}

    def create_session(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session_service.create_session(
            workspace_id=params["workspaceId"],
            title=params["title"],
        )
        return {"session": session}

    def test_provider(self, params: dict[str, Any]) -> dict[str, Any]:
        provider_patch = params.get("provider") if isinstance(params, dict) else None
        config = deepcopy(self._store.get_config({}).get("config", {}))
        provider_config = deepcopy(config.get("provider") or {})
        if isinstance(provider_patch, dict):
            provider_config.update(provider_patch)
        config["provider"] = provider_config

        mode = str(provider_config.get("mode") or provider_config.get("providerMode") or "").strip()
        normalized_mode = mode.lower()
        model = provider_config.get("model") or provider_config.get("defaultModel")
        base_url = provider_config.get("baseUrl") or provider_config.get("base_url") or "https://api.openai.com/v1"
        env_var_name = (
            provider_config.get("apiKeyEnvVarName")
            or provider_config.get("api_key_env_var_name")
            or provider_config.get("envKey")
            or "LOCAL_AGENT_PROVIDER_API_KEY"
        )

        if normalized_mode in {"", "mock"}:
            return {
                "ok": True,
                "status": "mocked",
                "message": "Provider is in mock mode; no network request was made.",
                "providerMode": mode or "mock",
                "model": model,
                "baseUrl": base_url,
                "checkedEnvVarName": env_var_name,
                "source": "runtime",
            }
        if normalized_mode not in {"openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"}:
            return {
                "ok": False,
                "status": "unsupported",
                "message": f"Unsupported provider mode: {mode}",
                "providerMode": mode,
                "model": model,
                "baseUrl": base_url,
                "checkedEnvVarName": env_var_name,
                "source": "runtime",
            }
        direct_key = provider_config.get("apiKey") or provider_config.get("api_key")
        if not direct_key and env_var_name and not os.environ.get(str(env_var_name)):
            return {
                "ok": False,
                "status": "missing_env",
                "message": f"Environment variable {env_var_name} is not set.",
                "providerMode": mode,
                "model": model,
                "baseUrl": base_url,
                "checkedEnvVarName": env_var_name,
                "source": "runtime",
            }

        try:
            response = self._provider.chat(
                messages=[
                    {
                        "role": "user",
                        "content": "Reply with a short provider connectivity confirmation.",
                    }
                ],
                tools=None,
                context={"config": config},
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "status": "failed",
                "message": str(exc),
                "providerMode": mode,
                "model": model,
                "baseUrl": base_url,
                "checkedEnvVarName": env_var_name,
                "source": "runtime",
            }

        return {
            "ok": True,
            "status": "ok",
            "message": "Provider connection succeeded.",
            "providerMode": mode,
            "model": response.get("raw", {}).get("model") or model,
            "baseUrl": base_url,
            "checkedEnvVarName": env_var_name,
            "source": "runtime",
            "details": {
                "finishReason": response.get("finish_reason"),
                "usage": response.get("raw", {}).get("usage"),
            },
        }

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
            react_result = self._run_react_loop(
                session_id=session["id"],
                task=runtime_task,
                goal=params["content"],
                context=context,
            )
            if react_result["status"] == "waiting_approval":
                return {"task": runtime_task}
            if react_result["status"] == "completed":
                return {
                    "task": self._complete_task(
                        session_id=session["id"],
                        task=runtime_task,
                        summary=react_result["summary"],
                    )
                }

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

            return {
                "task": self._complete_task(
                    session_id=session["id"],
                    task=runtime_task,
                    summary=summary,
                )
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "task": self._fail_task(
                    session_id=session["id"],
                    task=runtime_task,
                    summary=str(exc),
                    error_code="LOOP_EXECUTION_FAILED",
                )
            }

    def _complete_task(self, session_id: str, task: dict[str, Any], summary: str) -> dict[str, Any]:
        task["plan"] = self._planner.advance(
            task.get("plan") or [],
            "summarize-findings",
            final_status="completed",
        )
        completed_task = self._store.update_task(
            task_id=task["id"],
            status="completed",
            plan=task["plan"],
            result_summary=summary,
        )
        runtime_task = {
            **completed_task,
            "plan": task["plan"],
            "resultSummary": summary,
        }
        self._clear_pending_react_state(task["id"])
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="assistant.message.completed",
            payload={"content": summary},
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.completed",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task["plan"],
                "detail": summary,
            },
        )
        return runtime_task

    def _fail_task(
        self,
        session_id: str,
        task: dict[str, Any],
        summary: str,
        error_code: str,
    ) -> dict[str, Any]:
        task_plan = task.get("plan") or []
        failed_task = self._store.update_task(
            task_id=task["id"],
            status="failed",
            plan=task_plan,
            result_summary=summary,
            error_code=error_code,
        )
        runtime_task = {
            **failed_task,
            "plan": task_plan,
            "errorCode": error_code,
        }
        self._clear_pending_react_state(task["id"])
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.failed",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task["plan"],
                "detail": summary,
                "errorCode": error_code,
            },
        )
        return runtime_task

    def cancel_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task = self._store.update_task(task_id=params["taskId"], status="cancelled")
        self._clear_pending_react_state(task["id"])
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
        pending_state = self._load_pending_react_state(approval["taskId"])
        if pending_state is not None:
            if approval["decision"] == "approved":
                self._resume_react_after_approval(task=task, approval=approval)
            else:
                self._fail_task(
                    session_id=task["sessionId"],
                    task=task,
                    summary="Approval was rejected by the user.",
                    error_code="APPROVAL_REJECTED",
                )
            return {"approval": approval}
        if approval["decision"] == "approved" and approval["kind"] == "run_command":
            task = self._resume_approved_command(task=task, approval=approval)
        if approval["decision"] == "approved" and approval["kind"] == "apply_patch":
            task = self._resume_approved_patch(task=task, approval=approval)
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
            failure_summary = str(exc)
            failed_task = self._store.update_task(
                task_id=task["id"],
                status="failed",
                plan=runtime_task["plan"],
                result_summary=failure_summary,
                error_code="COMMAND_EXECUTION_FAILED",
            )
            self._publish(
                session_id=task["sessionId"],
                task=failed_task,
                event_type="task.failed",
                payload={
                    "status": "failed",
                    "plan": runtime_task["plan"],
                    "detail": failure_summary,
                    "errorCode": "COMMAND_EXECUTION_FAILED",
                },
            )
            return failed_task

    def _resume_approved_patch(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        request = json.loads(approval.get("requestJson") or "{}")
        tool_spec = {
            "name": "apply_patch",
            "arguments": {
                **request,
                "approvalId": approval["id"],
            },
            "plan_step_id": "apply-patch",
            "start_token": "Approval accepted. Applying the patch now...",
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
                "apply-patch",
                next_step_id="summarize-findings",
            )
            runtime_task["plan"] = self._planner.advance(
                runtime_task["plan"],
                "summarize-findings",
                final_status="completed",
            )
            patch_result = tool_result.get("result", {})
            patch_record = patch_result.get("patch", {})
            summary = (
                f"Approved patch finished with status {patch_result.get('status')} "
                f"and {patch_result.get('filesChanged')} file(s) changed."
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
                    "patchId": patch_record.get("id"),
                },
            )
            return runtime_task
        except Exception as exc:  # noqa: BLE001
            failure_summary = str(exc)
            failed_task = self._store.update_task(
                task_id=task["id"],
                status="failed",
                plan=runtime_task["plan"],
                result_summary=failure_summary,
                error_code="PATCH_APPLY_FAILED",
            )
            self._publish(
                session_id=task["sessionId"],
                task=failed_task,
                event_type="task.failed",
                payload={
                    "status": "failed",
                    "plan": runtime_task["plan"],
                    "detail": failure_summary,
                    "errorCode": "PATCH_APPLY_FAILED",
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

    def _save_pending_react_state(self, task_id: str, state: dict[str, Any]) -> None:
        normalized_state = {
            "session_id": state["session_id"],
            "goal": state["goal"],
            "context": deepcopy(state["context"]),
            "messages": deepcopy(state["messages"]),
            "tool_results": deepcopy(state["tool_results"]),
            "steps": int(state["steps"]),
            "react_started": bool(state["react_started"]),
            "pending_tool_call": deepcopy(state["pending_tool_call"]),
            "pending_tool_spec": deepcopy(state["pending_tool_spec"]),
            "remaining_tool_calls": deepcopy(state.get("remaining_tool_calls", [])),
        }
        self._pending_react_tasks[task_id] = normalized_state
        if hasattr(self._store, "upsert_pending_react_state"):
            self._store.upsert_pending_react_state(
                task_id=task_id,
                session_id=normalized_state["session_id"],
                goal=normalized_state["goal"],
                context=normalized_state["context"],
                messages=normalized_state["messages"],
                tool_results=normalized_state["tool_results"],
                pending_tool_call=normalized_state["pending_tool_call"],
                pending_tool_spec=normalized_state["pending_tool_spec"],
                remaining_tool_calls=normalized_state["remaining_tool_calls"],
                steps=normalized_state["steps"],
                react_started=normalized_state["react_started"],
            )

    def _load_pending_react_state(self, task_id: str) -> dict[str, Any] | None:
        if hasattr(self._store, "get_pending_react_state"):
            state = self._store.get_pending_react_state(task_id)
            if state is not None:
                normalized_state = {
                    "session_id": state["session_id"],
                    "goal": state["goal"],
                    "context": deepcopy(state["context"]),
                    "messages": deepcopy(state["messages"]),
                    "tool_results": deepcopy(state["tool_results"]),
                    "steps": int(state["steps"]),
                    "react_started": bool(state["react_started"]),
                    "pending_tool_call": deepcopy(state["pending_tool_call"]),
                    "pending_tool_spec": deepcopy(state["pending_tool_spec"]),
                    "remaining_tool_calls": deepcopy(state.get("remaining_tool_calls", [])),
                }
                self._pending_react_tasks[task_id] = normalized_state
                return normalized_state
        return self._pending_react_tasks.get(task_id)

    def _clear_pending_react_state(self, task_id: str) -> None:
        self._pending_react_tasks.pop(task_id, None)
        if hasattr(self._store, "delete_pending_react_state"):
            self._store.delete_pending_react_state(task_id)

    def _tool_failed(self, tool_name: str, result: dict[str, Any]) -> bool:
        status = result.get("status")
        return status in {"failed", "timeout", "killed"} or result.get("ok") is False

    def _tool_failure_summary(self, tool_spec: dict[str, Any], result: dict[str, Any]) -> str:
        tool_name = tool_spec["name"]
        if tool_name == "run_command":
            status = result.get("status", "failed")
            exit_code = result.get("exitCode")
            stdout = (result.get("stdout") or "").strip()
            stderr = (result.get("stderr") or "").strip()
            preview = stdout.splitlines()[0] if stdout else stderr.splitlines()[0] if stderr else "no output"
            return f"Command failed with status {status} and exit code {exit_code}; first output: {preview[:120]}."
        if tool_name == "apply_patch":
            summary = (result.get("summary") or "Patch tool failed.").strip()
            patch_id = result.get("patch_id", "unknown patch")
            return f"Apply patch failed for {patch_id}: {summary}"
        if tool_name == "git_status":
            summary = (result.get("summary") or "Git status failed.").strip()
            return f"Git status failed: {summary}"
        if tool_name == "git_diff":
            summary = (result.get("summary") or "Git diff failed.").strip()
            return f"Git diff failed: {summary}"
        if tool_name == "search_files":
            query = result.get("query", "unknown query")
            return f"Search failed for query '{query}'."
        if tool_name == "read_file":
            path = result.get("path", "unknown file")
            return f"Read file failed for {path}."
        return f"Tool {tool_name} failed."

    def _run_react_loop(
        self,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not hasattr(self._provider, "generate"):
            if self._has_deterministic_fallback():
                return {"status": "fallback"}
            raise RuntimeError("Provider does not implement generate().")

        max_steps = self._max_task_steps(context)
        messages = deepcopy(state["messages"]) if state else self._initial_react_messages(context, goal)
        tool_results = deepcopy(state["tool_results"]) if state else []
        steps = int(state.get("steps", 0)) if state else 0
        react_started = bool(state.get("react_started", False)) if state else False

        while True:
            if steps >= max_steps:
                raise RuntimeError(f"Reached maxTaskSteps ({max_steps}) before the provider returned a final answer.")

            provider_context = {
                **context,
                "messages": messages,
                "tools": self._provider_tools(context),
                "openai_tools": context.get("openai_tools") or self._provider_tools(context),
                "tool_results": tool_results,
                "step": steps + 1,
                "max_steps": max_steps,
            }
            response = self._request_provider_response(
                session_id=session_id,
                task=task,
                goal=goal,
                provider_context=provider_context,
            )
            parsed = self._parse_provider_response(
                response,
                allow_fallback=not react_started and steps == 0,
                allow_plain_message_final=react_started,
            )
            if parsed["status"] == "fallback":
                return parsed

            react_started = True
            steps += 1
            assistant_text = parsed.get("message") or ""
            if parsed["status"] == "completed" and not assistant_text:
                assistant_text = parsed["summary"]
            if assistant_text and not response.get("_streamed_content"):
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="assistant.token",
                    payload={"delta": assistant_text, "step": steps},
                )

            if parsed["status"] == "completed":
                return {"status": "completed", "summary": parsed["summary"]}

            tool_calls = parsed["tool_calls"]
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_text,
                    "tool_calls": tool_calls,
                }
            )
            for index, tool_call in enumerate(tool_calls):
                tool_spec = self._provider_tool_call_to_spec(tool_call, context)
                tool_result = self._execute_tool(
                    session_id=session_id,
                    task=task,
                    tool_spec=tool_spec,
                )
                if task["status"] == "waiting_approval":
                    self._pending_react_tasks[task["id"]] = {
                        "session_id": session_id,
                        "goal": goal,
                        "context": context,
                        "messages": messages,
                        "tool_results": tool_results,
                        "steps": steps,
                        "react_started": True,
                        "pending_tool_call": tool_call,
                        "pending_tool_spec": tool_spec,
                        "remaining_tool_calls": tool_calls[index + 1 :],
                    }
                    self._save_pending_react_state(task["id"], self._pending_react_tasks[task["id"]])
                    return {"status": "waiting_approval"}

                tool_results.append(tool_result)
                messages.append(self._tool_result_message(tool_call, tool_result))
                self._advance_after_tool(session_id=session_id, task=task, tool_spec=tool_spec)

    def _request_provider_response(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        provider_context: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._should_stream_provider(provider_context):
            self._append_provider_trace(task=task, event_type="provider.request", payload=self._provider_trace_payload(provider_context))
            response = self._provider.generate(goal, provider_context)
            self._append_provider_trace(task=task, event_type="provider.response", payload=self._provider_response_trace(response))
            return response

        self._append_provider_trace(
            task=task,
            event_type="provider.request",
            payload={**self._provider_trace_payload(provider_context), "stream": True},
        )
        final_response: dict[str, Any] | None = None
        streamed_content = False
        for event in self._provider.stream(goal, provider_context):
            event_type = event.get("type")
            if event_type == "content_delta":
                delta = event.get("delta")
                if isinstance(delta, str) and delta:
                    streamed_content = True
                    self._publish(
                        session_id=session_id,
                        task=task,
                        event_type="assistant.token",
                        payload={"delta": delta, "step": provider_context.get("step")},
                    )
            elif event_type == "final":
                response = event.get("response")
                if isinstance(response, dict):
                    final_response = response
            elif event_type == "finish_reason":
                self._append_provider_trace(task=task, event_type="provider.stream.finish", payload=event)
            elif event_type == "tool_call_delta":
                self._append_provider_trace(task=task, event_type="provider.stream.tool_call_delta", payload=event)

        if final_response is None:
            raise RuntimeError("Provider stream ended without a final response.")

        assistant_message = final_response.get("message", {})
        if not isinstance(assistant_message, dict):
            raise RuntimeError("Provider stream returned invalid final response.")
        response = {
            "message": assistant_message.get("content", ""),
            "assistant_message": assistant_message,
            "tool_calls": assistant_message.get("tool_calls") or [],
            "finish_reason": final_response.get("finish_reason"),
            "raw": final_response.get("raw", {}),
            "prompt": goal,
            "context": provider_context,
            "_streamed_content": streamed_content,
        }
        if not response["tool_calls"]:
            response["final"] = response["message"]
            response["final_answer"] = response["message"]
        self._append_provider_trace(
            task=task,
            event_type="provider.response",
            payload={**self._provider_response_trace(response), "stream": True},
        )
        return response

    def _should_stream_provider(self, provider_context: dict[str, Any]) -> bool:
        if not hasattr(self._provider, "stream"):
            return False
        config = provider_context.get("config") or {}
        provider_config = config.get("provider") if isinstance(config, dict) else {}
        if not isinstance(provider_config, dict):
            return False
        mode = str(provider_config.get("mode") or provider_config.get("providerMode") or "").strip().lower()
        return mode in {"openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"}

    def _append_provider_trace(self, *, task: dict[str, Any], event_type: str, payload: dict[str, Any]) -> None:
        if not hasattr(self._store, "append_trace_event"):
            return
        self._store.append_trace_event(
            task_id=task["id"],
            session_id=task["sessionId"],
            event_type=event_type,
            source="provider",
            related_id=payload.get("model"),
            payload=payload,
        )

    def _provider_trace_payload(self, provider_context: dict[str, Any]) -> dict[str, Any]:
        config = provider_context.get("config") or {}
        provider_config = config.get("provider") if isinstance(config, dict) else {}
        if not isinstance(provider_config, dict):
            provider_config = {}
        return {
            "mode": provider_config.get("mode") or provider_config.get("providerMode"),
            "model": provider_config.get("model") or provider_config.get("defaultModel"),
            "baseUrl": provider_config.get("baseUrl") or provider_config.get("base_url"),
            "messageCount": len(provider_context.get("messages") or []),
            "toolCount": len(provider_context.get("openai_tools") or provider_context.get("tools") or []),
            "step": provider_context.get("step"),
        }

    def _provider_response_trace(self, response: Any) -> dict[str, Any]:
        if not isinstance(response, dict):
            return {"valid": False, "type": type(response).__name__}
        raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
        return {
            "valid": True,
            "finishReason": response.get("finish_reason"),
            "model": raw.get("model"),
            "usage": raw.get("usage"),
            "toolCallCount": len(response.get("tool_calls") or []),
            "hasFinal": any(isinstance(response.get(key), str) and bool(response.get(key)) for key in ("final", "final_answer", "answer")),
        }

    def _resume_react_after_approval(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        state = self._load_pending_react_state(task["id"])
        if state is None:
            return task

        runtime_task = {**task, "plan": task.get("plan") or []}
        try:
            pending_spec = deepcopy(state["pending_tool_spec"])
            pending_spec["arguments"] = {
                **pending_spec.get("arguments", {}),
                "approvalId": approval["id"],
            }
            tool_result = self._execute_tool(
                session_id=task["sessionId"],
                task=runtime_task,
                tool_spec=pending_spec,
            )
            if runtime_task["status"] == "waiting_approval":
                state["pending_tool_spec"] = pending_spec
                self._save_pending_react_state(task["id"], state)
                return runtime_task

            state["tool_results"].append(tool_result)
            state["messages"].append(self._tool_result_message(state["pending_tool_call"], tool_result))
            self._advance_after_tool(session_id=task["sessionId"], task=runtime_task, tool_spec=pending_spec)

            for index, tool_call in enumerate(list(state.get("remaining_tool_calls", []))):
                tool_spec = self._provider_tool_call_to_spec(tool_call, state["context"])
                tool_result = self._execute_tool(
                    session_id=task["sessionId"],
                    task=runtime_task,
                    tool_spec=tool_spec,
                )
                if runtime_task["status"] == "waiting_approval":
                    state["pending_tool_call"] = tool_call
                    state["pending_tool_spec"] = tool_spec
                    state["remaining_tool_calls"] = state.get("remaining_tool_calls", [])[index + 1 :]
                    self._save_pending_react_state(task["id"], state)
                    return runtime_task

                state["tool_results"].append(tool_result)
                state["messages"].append(self._tool_result_message(tool_call, tool_result))
                self._advance_after_tool(session_id=task["sessionId"], task=runtime_task, tool_spec=tool_spec)

            state["remaining_tool_calls"] = []
            result = self._run_react_loop(
                session_id=task["sessionId"],
                task=runtime_task,
                goal=state["goal"],
                context=state["context"],
                state=state,
            )
            if result["status"] == "completed":
                return self._complete_task(
                    session_id=task["sessionId"],
                    task=runtime_task,
                    summary=result["summary"],
                )
            return runtime_task
        except Exception as exc:  # noqa: BLE001
            return self._fail_task(
                session_id=task["sessionId"],
                task=runtime_task,
                summary=str(exc),
                error_code="LOOP_EXECUTION_FAILED",
            )

    def _parse_provider_response(
        self,
        response: Any,
        *,
        allow_fallback: bool,
        allow_plain_message_final: bool,
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise RuntimeError("Provider returned invalid output: expected an object.")

        tool_calls = response.get("tool_calls")
        if tool_calls is not None:
            if not isinstance(tool_calls, list):
                raise RuntimeError("Provider returned invalid tool_calls: expected a list.")
            if tool_calls:
                return {
                    "status": "tool_calls",
                    "message": self._assistant_text(response),
                    "tool_calls": tool_calls,
                }

        final_answer = self._final_answer(response, allow_plain_message=allow_plain_message_final)
        if final_answer is not None:
            return {"status": "completed", "summary": final_answer}

        if allow_fallback and self._has_deterministic_fallback():
            return {"status": "fallback"}

        raise RuntimeError("Provider returned no final answer or tool calls.")

    def _assistant_text(self, response: dict[str, Any]) -> str:
        for key in ("message", "content", "text"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    def _final_answer(self, response: dict[str, Any], *, allow_plain_message: bool) -> str | None:
        for key in ("final", "final_answer", "answer"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value
        if response.get("type") in {"final", "final_answer"}:
            text = self._assistant_text(response)
            if text:
                return text
        if allow_plain_message:
            text = self._assistant_text(response)
            if text:
                return text
        return None

    def _has_deterministic_fallback(self) -> bool:
        return hasattr(self._provider, "choose_tool_sequence") and hasattr(self._provider, "summarize_findings")

    def _initial_react_messages(self, context: dict[str, Any], goal: str) -> list[dict[str, Any]]:
        messages = context.get("messages")
        if isinstance(messages, list) and messages:
            return deepcopy(messages)
        return [{"role": "user", "content": goal}]

    def _provider_tools(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        openai_tools = context.get("openai_tools")
        if isinstance(openai_tools, list) and openai_tools:
            return deepcopy(openai_tools)
        tools_by_name: dict[str, dict[str, Any]] = {}
        for schema in context.get("tools") or []:
            if isinstance(schema, dict) and isinstance(schema.get("name"), str):
                tools_by_name[schema["name"]] = schema
        for schema in self._tool_registry.schemas:
            if isinstance(schema, dict) and isinstance(schema.get("name"), str):
                tools_by_name.setdefault(schema["name"], schema)
        return list(tools_by_name.values())

    def _max_task_steps(self, context: dict[str, Any]) -> int:
        config = context.get("config") or {}
        policy = config.get("policy") if isinstance(config, dict) else {}
        raw_value = policy.get("maxTaskSteps", 20) if isinstance(policy, dict) else 20
        try:
            return max(1, int(raw_value))
        except (TypeError, ValueError):
            return 20

    def _provider_tool_call_to_spec(self, tool_call: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(tool_call, dict):
            raise RuntimeError("Provider returned invalid tool call: expected an object.")

        function_payload = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        tool_name = tool_call.get("name") or function_payload.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise RuntimeError("Provider returned a tool call without a tool name.")

        raw_arguments = tool_call.get("arguments", function_payload.get("arguments", {}))
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Provider returned invalid JSON arguments for {tool_name}.") from exc
        elif isinstance(raw_arguments, dict):
            arguments = deepcopy(raw_arguments)
        else:
            raise RuntimeError(f"Provider returned invalid arguments for {tool_name}.")

        self._fill_tool_defaults(tool_name, arguments, context)
        return {
            "id": tool_call.get("id"),
            "name": tool_name,
            "arguments": arguments,
            "plan_step_id": self._plan_step_for_tool(tool_name),
            "start_token": f"Running tool: {tool_name}",
        }

    def _fill_tool_defaults(self, tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> None:
        workspace_tools = {
            "list_dir",
            "search_files",
            "read_file",
            "run_command",
            "apply_patch",
            "git_status",
            "git_diff",
        }
        if tool_name in workspace_tools and "workspaceRoot" not in arguments and "workspace_root" not in arguments:
            arguments["workspaceRoot"] = context["workspace_root"]

        search_config = context.get("search_config", {})
        if tool_name == "list_dir":
            arguments.setdefault("path", ".")
            arguments.setdefault("recursive", False)
            arguments.setdefault("max_depth", 2)
            arguments.setdefault("ignore", search_config.get("ignore", []))
        elif tool_name == "search_files":
            arguments.setdefault("mode", context.get("search_mode", "content"))
            arguments.setdefault("glob", search_config.get("glob", []))
            arguments.setdefault("ignore", search_config.get("ignore", []))
            arguments.setdefault("max_results", 8)
        elif tool_name == "read_file":
            arguments.setdefault("max_bytes", 4000)
            arguments.setdefault("ignore", search_config.get("ignore", []))
        elif tool_name == "run_command":
            arguments.setdefault("cwd", ".")
        elif tool_name == "apply_patch":
            arguments.setdefault("dry_run", False)

    def _plan_step_for_tool(self, tool_name: str) -> str:
        return {
            "list_dir": "inspect-workspace",
            "search_files": "search-relevant-files",
            "read_file": "search-relevant-files",
            "run_command": "run-command",
            "apply_patch": "apply-patch",
            "git_status": "git-status",
            "git_diff": "git-diff",
        }.get(tool_name, tool_name.replace("_", "-"))

    def _tool_result_message(self, tool_call: dict[str, Any], tool_result: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call.get("id") or tool_result["id"],
            "name": tool_result["name"],
            "content": json.dumps(tool_result["result"], ensure_ascii=False),
        }

    def _advance_after_tool(self, session_id: str, task: dict[str, Any], tool_spec: dict[str, Any]) -> None:
        task["plan"] = self._planner.advance(
            task.get("plan") or [],
            tool_spec["plan_step_id"],
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
        tool_call_id = tool_spec.get("id") or self._store.new_id("tc")
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
            self._publish(
                session_id=session_id,
                task=task,
                event_type="task.waiting_approval",
                payload={
                    "status": "waiting_approval",
                    "detail": "Patch requires approval before execution."
                    if tool_spec["name"] == "apply_patch"
                    else "Command requires approval before execution.",
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

        if self._tool_failed(tool_spec["name"], result):
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
            raise RuntimeError(self._tool_failure_summary(tool_spec, result))

        if tool_spec["name"] == "run_command":
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

        if tool_spec["name"] == "apply_patch":
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

    def _next_step_id(self, tool_sequence: list[dict[str, Any]], current_index: int) -> str | None:
        if current_index + 1 >= len(tool_sequence):
            return "summarize-findings"
        return tool_sequence[current_index + 1]["plan_step_id"]
