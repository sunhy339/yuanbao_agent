from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..context.builder import ContextBuilder
from ..event_bus import EventBus
from ..models import RuntimeEvent
from ..planner.service import Planner
from ..policy.guard import PolicyGuard
from ..provider.adapter import ProviderAdapter
from ..services.collaboration_service import CollaborationService
from ..services.command_background import cancel_background_commands
from ..services.session_service import SessionService
from ..services.subagent_service import SubagentService
from ..services.worker_budget import WorkerBudget, WorkerBudgetExceededError
from ..services.worker_environment import normalize_child_tool_allowlist
from ..services.worker_runner import WorkerRunner
from ..store.sqlite_store import SQLiteStore
from ..tools.builtin import build_builtin_tools
from ..tools.registry import BUILTIN_TOOL_SCHEMAS, ToolRegistry


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
        self._context_builder = ContextBuilder(store, tool_schemas=self._context_tool_schemas())
        self._session_service = SessionService(store)
        self._collaboration_service = CollaborationService(store, event_bus)
        self._worker_runner = WorkerRunner(self._collaboration_service)
        self._subagent_service = SubagentService(store, self._collaboration_service, runner=self._worker_runner)
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

    def _redact_provider_secret(self, text: str, env_var_name: Any, direct_key: Any = None) -> str:
        redacted = text
        if direct_key:
            redacted = redacted.replace(str(direct_key), "[redacted]")
        if env_var_name:
            env_value = os.environ.get(str(env_var_name))
            if env_value:
                redacted = redacted.replace(env_value, "[redacted]")
        return redacted

    def test_provider(self, params: dict[str, Any]) -> dict[str, Any]:
        params = params if isinstance(params, dict) else {}
        provider_patch = params.get("provider") if isinstance(params, dict) else None
        checked_at = self._store.now() if hasattr(self._store, "now") else 0
        config = deepcopy(self._store.get_config({}).get("config", {}))
        provider_root = deepcopy(config.get("provider") or {})
        provider_config, profile_id, profile_name = self._provider_config_for_test(
            provider_root=provider_root,
            profile_id=params.get("profileId"),
            provider_patch=provider_patch if isinstance(provider_patch, dict) else None,
        )
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
            result = {
                "ok": True,
                "status": "mocked",
                "message": "Provider is in mock mode; no network request was made.",
                "profileId": profile_id,
                "profileName": profile_name,
                "providerMode": mode or "mock",
                "model": model,
                "baseUrl": base_url,
                "checkedEnvVarName": env_var_name,
                "envVarName": env_var_name,
                "lastCheckedAt": checked_at,
                "lastStatus": "mocked",
                "lastErrorSummary": "Mock mode does not contact a remote model.",
                "source": "runtime",
                "details": {
                    "errorSummary": "Mock mode does not contact a remote model.",
                },
            }
            self._persist_provider_test_result(profile_id, result, persist=provider_patch is None)
            return result
        if normalized_mode not in {"openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"}:
            result = {
                "ok": False,
                "status": "unsupported",
                "message": f"Unsupported provider mode: {mode}",
                "profileId": profile_id,
                "profileName": profile_name,
                "providerMode": mode,
                "model": model,
                "baseUrl": base_url,
                "checkedEnvVarName": env_var_name,
                "envVarName": env_var_name,
                "lastCheckedAt": checked_at,
                "lastStatus": "unsupported",
                "lastErrorSummary": f"Unsupported provider mode: {mode}",
                "source": "runtime",
                "details": {
                    "errorSummary": f"Unsupported provider mode: {mode}",
                },
            }
            self._persist_provider_test_result(profile_id, result, persist=provider_patch is None)
            return result
        direct_key = provider_config.get("apiKey") or provider_config.get("api_key")
        if not direct_key and env_var_name and not os.environ.get(str(env_var_name)):
            result = {
                "ok": False,
                "status": "missing_env",
                "message": f"Environment variable {env_var_name} is not set.",
                "profileId": profile_id,
                "profileName": profile_name,
                "providerMode": mode,
                "model": model,
                "baseUrl": base_url,
                "checkedEnvVarName": env_var_name,
                "envVarName": env_var_name,
                "lastCheckedAt": checked_at,
                "lastStatus": "missing_env",
                "lastErrorSummary": f"Set {env_var_name} in the runtime environment.",
                "source": "runtime",
                "details": {
                    "errorSummary": f"Set {env_var_name} in the runtime environment.",
                },
            }
            self._persist_provider_test_result(profile_id, result, persist=provider_patch is None)
            return result

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
            error_summary = self._redact_provider_secret(str(exc), env_var_name, direct_key)
            result = {
                "ok": False,
                "status": "failed",
                "message": error_summary,
                "profileId": profile_id,
                "profileName": profile_name,
                "providerMode": mode,
                "model": model,
                "baseUrl": base_url,
                "checkedEnvVarName": env_var_name,
                "envVarName": env_var_name,
                "lastCheckedAt": checked_at,
                "lastStatus": "failed",
                "lastErrorSummary": error_summary,
                "source": "runtime",
                "details": {
                    "errorSummary": error_summary,
                    "errorType": type(exc).__name__,
                },
            }
            self._persist_provider_test_result(profile_id, result, persist=provider_patch is None)
            return result

        result = {
            "ok": True,
            "status": "ok",
            "message": "Provider connection succeeded.",
            "profileId": profile_id,
            "profileName": profile_name,
            "providerMode": mode,
            "model": response.get("raw", {}).get("model") or model,
            "baseUrl": base_url,
            "checkedEnvVarName": env_var_name,
            "envVarName": env_var_name,
            "lastCheckedAt": checked_at,
            "lastStatus": "ok",
            "lastErrorSummary": None,
            "source": "runtime",
            "details": {
                "finishReason": response.get("finish_reason"),
                "usage": response.get("raw", {}).get("usage"),
            },
        }
        self._persist_provider_test_result(profile_id, result, persist=provider_patch is None)
        return result

    def _persist_provider_test_result(
        self,
        profile_id: str | None,
        result: dict[str, Any],
        *,
        persist: bool,
    ) -> None:
        if not persist or not profile_id or not hasattr(self._store, "update_provider_profile_health"):
            return
        self._store.update_provider_profile_health(
            profile_id,
            last_checked_at=int(result.get("lastCheckedAt") or 0),
            last_status=str(result.get("lastStatus") or result.get("status") or "failed"),
            last_error_summary=result.get("lastErrorSummary"),
        )

    def _provider_config_for_test(
        self,
        *,
        provider_root: dict[str, Any],
        profile_id: Any,
        provider_patch: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], str | None, str | None]:
        selected = self._select_provider_profile(provider_root, profile_id)
        provider_config = {
            key: deepcopy(value)
            for key, value in provider_root.items()
            if key not in {"profiles", "activeProfileId"}
        }
        selected_profile_id: str | None = None
        selected_profile_name: str | None = None

        if selected is not None:
            provider_config.update(deepcopy(selected))
            selected_profile_id = selected.get("id") if isinstance(selected.get("id"), str) else None
            selected_profile_name = selected.get("name") if isinstance(selected.get("name"), str) else None

        if provider_patch:
            patch_selected = self._select_provider_profile(
                provider_patch,
                provider_patch.get("activeProfileId") or provider_patch.get("profileId"),
            )
            if patch_selected is not None:
                provider_config.update(deepcopy(patch_selected))
                selected_profile_id = patch_selected.get("id") if isinstance(patch_selected.get("id"), str) else selected_profile_id
                selected_profile_name = patch_selected.get("name") if isinstance(patch_selected.get("name"), str) else selected_profile_name
            else:
                for key, value in provider_patch.items():
                    if key not in {"profiles", "activeProfileId", "profileId"}:
                        provider_config[key] = deepcopy(value)
                if isinstance(provider_patch.get("id"), str):
                    selected_profile_id = provider_patch["id"]
                if isinstance(provider_patch.get("name"), str):
                    selected_profile_name = provider_patch["name"]

        return provider_config, selected_profile_id, selected_profile_name

    def _select_provider_profile(self, provider_root: dict[str, Any], profile_id: Any) -> dict[str, Any] | None:
        profiles = provider_root.get("profiles")
        if not isinstance(profiles, list) or not profiles:
            return None

        requested_id = profile_id if isinstance(profile_id, str) and profile_id.strip() else provider_root.get("activeProfileId")
        if isinstance(requested_id, str) and requested_id.strip():
            for profile in profiles:
                if isinstance(profile, dict) and profile.get("id") == requested_id:
                    return profile
        return next((profile for profile in profiles if isinstance(profile, dict)), None)

    def send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._store.require_session(params["sessionId"])
        if params.get("background") is True:
            plan = self._planner.plan(params["content"])
            task = self._store.create_task(
                session_id=session["id"],
                task_type="edit",
                goal=params["content"],
                plan=plan,
                acceptance_criteria=self._default_acceptance_criteria(params["content"]),
                out_of_scope=self._default_out_of_scope(),
            )
            runtime_task = {**task, "plan": plan}
            self._store.create_message(
                session_id=session["id"],
                task_id=runtime_task["id"],
                role="user",
                content=params["content"],
            )
            self._start_background_message(
                session_id=session["id"],
                task=runtime_task,
                goal=params["content"],
                context=None,
            )
            return {"task": runtime_task}

        context = self._context_builder.build(session_id=session["id"], goal=params["content"])
        plan = self._planner.plan(params["content"], context=context)
        task = self._store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal=params["content"],
            plan=plan,
            acceptance_criteria=self._default_acceptance_criteria(params["content"]),
            out_of_scope=self._default_out_of_scope(),
        )
        runtime_task = {**task, "plan": plan}
        self._store.create_message(
            session_id=session["id"],
            task_id=runtime_task["id"],
            role="user",
            content=params["content"],
        )
        context = self._context_with_task_focus(context, runtime_task)

        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="task.started",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task["plan"],
                "currentStep": runtime_task.get("currentStep"),
                "context": self._event_context_summary(context),
            },
        )
        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="assistant.token",
            payload={"delta": "Building context and preparing the first tool calls..."},
        )

        return self._execute_message_task(
            session_id=session["id"],
            task=runtime_task,
            goal=params["content"],
            context=context,
        )

    def _execute_message_task(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            react_result = self._run_react_loop(
                session_id=session_id,
                task=task,
                goal=goal,
                context=context,
            )
            if react_result["status"] == "waiting_approval":
                return {"task": task}
            if react_result["status"] == "completed":
                return {
                    "task": self._complete_task(
                        session_id=session_id,
                        task=task,
                        summary=react_result["summary"],
                        context=context,
                        tool_results=react_result.get("tool_results", []),
                    )
                }

            tool_results = self._run_minimal_loop(
                session_id=session_id,
                task=task,
                goal=goal,
                context=context,
            )
            if task["status"] == "waiting_approval":
                return {"task": task}
            summary = self._provider.summarize_findings(
                goal=goal,
                context=context,
                tool_results=tool_results,
            )
            self._publish(
                session_id=session_id,
                task=task,
                event_type="assistant.token",
                payload={"delta": summary},
            )

            return {
                "task": self._complete_task(
                    session_id=session_id,
                    task=task,
                    summary=summary,
                    context=context,
                    tool_results=tool_results,
                )
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="LOOP_EXECUTION_FAILED",
                )
            }

    def _start_background_message(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any] | None,
    ) -> None:
        worker = threading.Thread(
            target=self._run_background_message,
            kwargs={
                "session_id": session_id,
                "task": deepcopy(task),
                "goal": goal,
                "context": deepcopy(context) if context is not None else None,
            },
            name=f"message-task-{task['id']}",
            daemon=True,
        )
        worker.start()

    def _run_background_message(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any] | None,
    ) -> None:
        background_store: SQLiteStore | None = None
        worker = self
        try:
            worker, background_store = self._background_worker_orchestrator()
            if context is None:
                context = worker._context_builder.build(session_id=session_id, goal=goal)
                plan = worker._planner.plan(goal, context=context)
                task = worker._store.update_task(task_id=task["id"], plan=plan)
                task = {**task, "plan": plan}
                context = worker._context_with_task_focus(context, task)
                worker._publish(
                    session_id=session_id,
                    task=task,
                    event_type="task.started",
                    payload={
                        "status": task["status"],
                        "plan": task["plan"],
                        "currentStep": task.get("currentStep"),
                        "context": worker._event_context_summary(context),
                    },
                )
                worker._publish(
                    session_id=session_id,
                    task=task,
                    event_type="assistant.token",
                    payload={"delta": "Building context and preparing the first tool calls..."},
                )
            else:
                context = worker._context_with_task_focus(context, task)
            worker._execute_message_task(
                session_id=session_id,
                task=task,
                goal=goal,
                context=context,
            )
        except Exception as exc:  # noqa: BLE001
            worker._fail_task(
                session_id=session_id,
                task=task,
                summary=str(exc),
                error_code="BACKGROUND_LOOP_FAILED",
            )
        finally:
            if background_store is not None:
                background_store.close()

    def _background_worker_orchestrator(self) -> tuple["Orchestrator", SQLiteStore | None]:
        database_path = str(getattr(self._store, "database_path", ":memory:"))
        if database_path == ":memory:":
            return self, None

        store = SQLiteStore(database_path)
        config = store.get_config({})["config"]
        policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
        collaboration = CollaborationService(store, self._event_bus)
        subagent_service = SubagentService(store, collaboration)
        tool_registry = ToolRegistry(
            build_builtin_tools(policy_guard=policy_guard, store=store, subagent_service=subagent_service)
        )
        return (
            Orchestrator(
                store=store,
                event_bus=self._event_bus,
                tool_registry=tool_registry,
                provider=ProviderAdapter(),
            ),
            store,
        )

    def _event_context_summary(self, context: dict[str, Any]) -> dict[str, Any]:
        budget_stats = context.get("budgetStats")
        summary: dict[str, Any] = {
            "workspaceId": context.get("workspace_id"),
            "workspaceName": context.get("workspace_name"),
            "workspaceRoot": context.get("workspace_root"),
            "projectFocus": context.get("project_focus"),
            "projectMemory": context.get("project_memory"),
            "searchQuery": context.get("search_query"),
            "searchMode": context.get("search_mode"),
            "toolCount": len(context.get("tools") or []),
        }
        if isinstance(budget_stats, dict):
            summary["budgetStats"] = {
                "estimatedTokens": budget_stats.get("estimatedTokens"),
                "estimatedInputTokens": budget_stats.get("estimatedInputTokens"),
                "messageTokens": budget_stats.get("messageTokens"),
                "toolSchemaTokens": budget_stats.get("toolSchemaTokens"),
                "maxContextTokens": budget_stats.get("maxContextTokens"),
                "droppedSections": budget_stats.get("droppedSections"),
                "trimmedSections": budget_stats.get("trimmedSections"),
            }
        task_focus = context.get("task_focus")
        if isinstance(task_focus, dict):
            summary["taskFocus"] = {
                "taskId": task_focus.get("taskId"),
                "currentStep": task_focus.get("currentStep"),
                "acceptanceCriteriaCount": len(task_focus.get("acceptanceCriteria") or []),
                "outOfScopeCount": len(task_focus.get("outOfScope") or []),
            }
        return summary

    def _default_acceptance_criteria(self, goal: str) -> list[str]:
        normalized_goal = " ".join(str(goal).split())
        return [
            f"Resolve the user's request: {normalized_goal}",
            "Keep the work focused on the requested task and avoid unrelated changes.",
            "Use local tools for inspection, edits and commands; report real results instead of guessing.",
            "When files or commands are involved, summarize changed files, command outcomes and verification.",
        ]

    def _default_out_of_scope(self) -> list[str]:
        return [
            "Unrelated refactors, broad rewrites or cosmetic churn not needed for the request.",
            "Operations outside the workspace root unless the user explicitly supplies or approves them.",
            "Claiming success before required edits, commands or verification have actually completed.",
        ]

    def _context_with_task_focus(self, context: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
        focused_context = deepcopy(context)
        task_focus = {
            "taskId": task.get("id"),
            "goal": task.get("goal"),
            "status": task.get("status"),
            "currentStep": task.get("currentStep"),
            "acceptanceCriteria": list(task.get("acceptanceCriteria") or []),
            "outOfScope": list(task.get("outOfScope") or []),
        }
        focused_context["task_focus"] = task_focus

        messages = deepcopy(focused_context.get("messages") or [])
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

    def run_child_task(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("sessionId")
        prompt = params.get("prompt")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("sessionId is required")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt is required")

        session = self._store.require_session(session_id)
        budget = WorkerBudget.from_metadata(params.get("budget"), params)
        context = self._context_builder.build(session_id=session["id"], goal=prompt.strip())
        context = self._context_with_worker_budget(context, budget)
        plan = self._planner.plan(prompt.strip(), context=context)
        task = self._store.create_task(
            session_id=session["id"],
            task_type="subagent",
            goal=prompt.strip(),
            plan=plan,
            acceptance_criteria=self._default_acceptance_criteria(prompt.strip()),
            out_of_scope=self._default_out_of_scope(),
        )
        runtime_task = {**task, "plan": plan}
        context = self._context_with_task_focus(context, runtime_task)

        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="task.started",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task["plan"],
                "currentStep": runtime_task.get("currentStep"),
                "context": context,
                "childWorker": True,
            },
        )

        try:
            react_result = self._run_react_loop(
                session_id=session["id"],
                task=runtime_task,
                goal=prompt.strip(),
                context=context,
                budget=budget,
            )
            if react_result["status"] == "waiting_approval":
                return self._waiting_child_task_response(
                    task=runtime_task,
                    summary="Child worker is waiting for parent approval.",
                    budget=budget,
                )
            if react_result["status"] == "completed":
                completed_task = self._complete_task(
                    session_id=session["id"],
                    task=runtime_task,
                    summary=react_result["summary"],
                    context=context,
                    tool_results=react_result.get("tool_results", []),
                )
                return {
                    "status": "completed",
                    "task": completed_task,
                    "summary": completed_task.get("resultSummary") or react_result["summary"],
                    "budget": budget.to_metadata(),
                }

            tool_results = self._run_minimal_loop(
                session_id=session["id"],
                task=runtime_task,
                goal=prompt.strip(),
                context=context,
                budget=budget,
            )
            if runtime_task["status"] == "waiting_approval":
                return self._waiting_child_task_response(
                    task=runtime_task,
                    summary="Child worker is waiting for parent approval.",
                    budget=budget,
                )
            summary = self._provider.summarize_findings(
                goal=prompt.strip(),
                context=context,
                tool_results=tool_results,
            )
            completed_task = self._complete_task(
                session_id=session["id"],
                task=runtime_task,
                summary=summary,
                context=context,
                tool_results=tool_results,
            )
            return {
                "status": "completed",
                "task": completed_task,
                "summary": completed_task.get("resultSummary") or summary,
                "budget": budget.to_metadata(),
            }
        except Exception as exc:  # noqa: BLE001
            self._fail_task(
                session_id=session["id"],
                task=runtime_task,
                summary=str(exc),
                error_code=str(getattr(exc, "code", "LOOP_EXECUTION_FAILED")),
            )
            raise

    def _waiting_child_task_response(
        self,
        *,
        task: dict[str, Any],
        summary: str,
        budget: WorkerBudget,
    ) -> dict[str, Any]:
        persisted_task = self._store.get_task({"taskId": task["id"]})["task"]
        approval = self._latest_pending_approval(task["id"])
        return {
            "status": "waiting_approval",
            "task": persisted_task,
            "summary": summary,
            "approval": approval,
            "budget": budget.to_metadata(),
        }

    def _latest_pending_approval(self, task_id: str) -> dict[str, Any] | None:
        if not hasattr(self._store, "_conn"):
            return None
        row = self._store._conn.execute(  # noqa: SLF001
            """
            SELECT *
            FROM approvals
            WHERE task_id = ? AND decision IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return self._store._serialize_approval(dict(row))  # noqa: SLF001

    def _context_tool_schemas(self) -> list[dict[str, Any]] | None:
        allowed = self._child_tool_allowlist()
        if allowed is None:
            return None
        allowed_set = set(allowed)
        return [schema for schema in BUILTIN_TOOL_SCHEMAS if schema.get("name") in allowed_set]

    def _child_tool_allowlist(self) -> list[str] | None:
        raw = os.environ.get("LOCAL_AGENT_CHILD_TOOL_ALLOWLIST")
        if raw is None:
            return None
        return normalize_child_tool_allowlist(raw)

    def _complete_task(
        self,
        session_id: str,
        task: dict[str, Any],
        summary: str,
        *,
        context: dict[str, Any] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        validation = self._run_post_task_validation(
            session_id=session_id,
            task=task,
            context=context or {},
            tool_results=tool_results or [],
        )
        final_summary = self._merge_completion_summary(summary=summary, validation=validation)
        task["plan"] = self._planner.advance(
            task.get("plan") or [],
            "summarize-findings",
            final_status="completed",
        )
        completed_task = self._store.update_task(
            task_id=task["id"],
            status="completed",
            plan=task["plan"],
            summary=final_summary,
            result_summary=final_summary,
        )
        runtime_task = {
            **completed_task,
            "plan": task["plan"],
            "resultSummary": final_summary,
        }
        self._store.create_message(
            session_id=session_id,
            task_id=runtime_task["id"],
            role="assistant",
            content=final_summary,
        )
        self._remember_task_result(session_id=session_id, task=runtime_task)
        self._clear_pending_react_state(task["id"])
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="assistant.message.completed",
            payload={"content": final_summary},
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
                "summary": final_summary,
                "resultSummary": final_summary,
                "detail": final_summary,
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
            summary=summary,
            result_summary=summary,
            error_code=error_code,
        )
        runtime_task = {
            **failed_task,
            "plan": task_plan,
            "errorCode": error_code,
        }
        self._store.create_message(
            session_id=session_id,
            task_id=runtime_task["id"],
            role="assistant",
            content=summary,
        )
        self._remember_task_result(session_id=session_id, task=runtime_task)
        self._clear_pending_react_state(task["id"])
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
        return runtime_task

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
        ran = validation.get("ran")
        checks = validation.get("checks")
        failed_checks = [check for check in checks if isinstance(check, dict) and check.get("status") == "failed"] if isinstance(checks, list) else []
        if not ran and not failed_checks:
            return base
        validation_summary = (validation.get("summary") or "").strip()
        if not validation_summary:
            return base
        if not base:
            return validation_summary
        return f"{base} {validation_summary}"

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

    def _publish_task_run_snapshot(self, *, session_id: str, task: dict[str, Any]) -> None:
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.updated",
            payload={
                "status": task["status"],
                "plan": task.get("plan"),
                "currentStep": task.get("currentStep"),
                "changedFiles": task.get("changedFiles") or [],
                "commands": task.get("commands") or [],
                "verification": task.get("verification") or [],
                "summary": task.get("summary"),
                "resultSummary": task.get("resultSummary"),
            },
        )

    def cancel_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task = self._store.get_task({"taskId": params["taskId"]})["task"]
        cancel_background_commands(database_path=self._store.database_path, task_id=task["id"])
        task = self._store.update_task(task_id=params["taskId"], status="cancelled")
        self._clear_pending_react_state(task["id"])
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="task.cancelled",
            payload={"status": task["status"]},
        )
        return {"task": task}

    def pause_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task = self._store.get_task({"taskId": params["taskId"]})["task"]
        if task["status"] not in {"running", "waiting_approval"}:
            return {"task": task}
        paused_task = self._store.update_task(task_id=task["id"], status="paused")
        self._publish(
            session_id=paused_task["sessionId"],
            task=paused_task,
            event_type="task.paused",
            payload={"status": paused_task["status"], "previousStatus": task["status"]},
        )
        return {"task": paused_task}

    def resume_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task = self._store.get_task({"taskId": params["taskId"]})["task"]
        if task["status"] != "paused":
            return {"task": task}

        pending_state = self._load_pending_react_state(task["id"])
        if pending_state is not None:
            approval = self._latest_approval_for_task(task["id"])
            if approval is not None and approval.get("decision") == "approved":
                running_task = self._store.update_task_status(task_id=task["id"], status="running")
                self._publish(
                    session_id=running_task["sessionId"],
                    task=running_task,
                    event_type="task.resumed",
                    payload={"status": "running", "detail": "Resuming approved pending ReAct task."},
                )
                resumed_task = self._resume_react_after_approval(task=running_task, approval=approval)
                return {"task": resumed_task}
            if approval is not None and approval.get("decision") == "rejected":
                self._publish(
                    session_id=task["sessionId"],
                    task=task,
                    event_type="task.resumed",
                    payload={"status": "running", "detail": "Resuming rejected pending ReAct task."},
                )
                failed_task = self._fail_task(
                    session_id=task["sessionId"],
                    task=task,
                    summary="Approval was rejected by the user.",
                    error_code="APPROVAL_REJECTED",
                )
                return {"task": failed_task}

            waiting_task = self._store.update_task_status(task_id=task["id"], status="waiting_approval")
            self._publish(
                session_id=waiting_task["sessionId"],
                task=waiting_task,
                event_type="task.resumed",
                payload={"status": "waiting_approval", "detail": "Pending ReAct task is waiting for approval."},
            )
            return {"task": waiting_task}

        running_task = self._store.update_task_status(task_id=task["id"], status="running")
        self._publish(
            session_id=running_task["sessionId"],
            task=running_task,
            event_type="task.resumed",
            payload={"status": running_task["status"]},
        )
        return {"task": running_task}

    def submit_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        approval = self._store.resolve_approval(
            approval_id=params["approvalId"],
            decision=params["decision"],
        )
        task = self._store.get_task({"taskId": approval["taskId"]})["task"]
        if task["status"] in {"cancelled", "completed", "failed"}:
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="approval.resolved",
                payload={
                    "approvalId": approval["id"],
                    "taskId": task["id"],
                    "decision": approval["decision"],
                    "ignored": True,
                    "taskStatus": task["status"],
                },
            )
            return {"approval": approval}
        if task["status"] == "paused":
            self._publish(
                session_id=task["sessionId"],
                task=task,
                event_type="approval.resolved",
                payload={
                    "approvalId": approval["id"],
                    "taskId": task["id"],
                    "decision": approval["decision"],
                    "deferred": True,
                    "taskStatus": task["status"],
                },
            )
            return {"approval": approval}
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
            child_task = self._blocked_child_collaboration_for_runtime_task(approval=approval, runtime_task=task)
            if child_task is not None and self._should_resume_child_approval_in_process(params, child_task):
                try:
                    runtime_task = self._worker_runner.resume_child_approval(
                        approval=approval,
                        child_task=child_task,
                    )
                except Exception as exc:  # noqa: BLE001
                    runtime_task = self._fail_task(
                        session_id=task["sessionId"],
                        task=task,
                        summary=str(exc),
                        error_code=str(getattr(exc, "code", None) or "CHILD_WORKER_APPROVAL_RESUME_FAILED"),
                    )
                self._finalize_child_collaboration_after_approval(approval=approval, runtime_task=runtime_task)
                return {"approval": approval}
            if approval["decision"] == "approved":
                resumed_task = self._resume_react_after_approval(task=task, approval=approval)
                self._finalize_child_collaboration_after_approval(approval=approval, runtime_task=resumed_task)
            else:
                failed_task = self._fail_task(
                    session_id=task["sessionId"],
                    task=task,
                    summary="Approval was rejected by the user.",
                    error_code="APPROVAL_REJECTED",
                )
                self._finalize_child_collaboration_after_approval(approval=approval, runtime_task=failed_task)
            return {"approval": approval}
        if approval["decision"] == "approved" and approval["kind"] == "run_command":
            task = self._resume_approved_command(task=task, approval=approval)
        if approval["decision"] == "approved" and approval["kind"] == "apply_patch":
            task = self._resume_approved_patch(task=task, approval=approval)
        return {"approval": approval}

    def _should_resume_child_approval_in_process(self, params: dict[str, Any], child_task: dict[str, Any]) -> bool:
        if params.get("_childWorkerApprovalResume") is True:
            return False
        metadata = child_task.get("metadata") if isinstance(child_task.get("metadata"), dict) else {}
        return metadata.get("executionMode") == "process-rpc"

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
            return self._fail_task(
                session_id=task["sessionId"],
                task={**runtime_task, "sessionId": task["sessionId"]},
                summary=str(exc),
                error_code="COMMAND_EXECUTION_FAILED",
            )

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
            summary = (
                f"Approved patch finished with status {patch_result.get('status')} "
                f"and {patch_result.get('filesChanged')} file(s) changed."
            )
            runtime_task = self._complete_task(
                session_id=task["sessionId"],
                task=runtime_task,
                summary=summary,
                context=self._context_builder.build(
                    session_id=task["sessionId"],
                    goal=task.get("goal") or summary,
                ),
                tool_results=[tool_result],
            )
            return runtime_task
        except Exception as exc:  # noqa: BLE001
            return self._fail_task(
                session_id=task["sessionId"],
                task={**runtime_task, "sessionId": task["sessionId"]},
                summary=str(exc),
                error_code="PATCH_APPLY_FAILED",
            )

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

    def _latest_approval_for_task(self, task_id: str) -> dict[str, Any] | None:
        if hasattr(self._store, "find_latest_approval"):
            return self._store.find_latest_approval(task_id=task_id)
        return None

    def _tool_failed(self, tool_name: str, result: dict[str, Any]) -> bool:
        status = result.get("status")
        return status in {"failed", "timeout", "killed"} or result.get("ok") is False

    def _is_patch_validation_failure(self, tool_name: str, result: dict[str, Any]) -> bool:
        return tool_name == "apply_patch" and result.get("status") == "validation_failed"

    def _tool_failure_summary(self, tool_spec: dict[str, Any], result: dict[str, Any]) -> str:
        tool_name = tool_spec["name"]
        if tool_name == "run_command":
            status = result.get("status", "failed")
            exit_code = result.get("exitCode")
            stdout = (result.get("stdout") or "").strip()
            stderr = (result.get("stderr") or "").strip()
            preview = stdout.splitlines()[0] if stdout else stderr.splitlines()[0] if stderr else "no output"
            return f"Command failed with status {status} and exit code {exit_code}; first output: {preview[:120]}."
        if tool_name == "task":
            child_task_id = result.get("childTaskId") or result.get("task", {}).get("id") or "unknown child task"
            summary = (result.get("summary") or result.get("result", {}).get("summary") or "Child task failed.").strip()
            return f"Child task {child_task_id} failed: {summary}"
        if tool_name == "apply_patch":
            if result.get("status") == "validation_failed":
                summary = (result.get("summary") or "Patch validation failed.").strip()
                error = (result.get("error") or "Unknown validation error.").strip()
                return f"Patch validation failed for {summary}: {error}"
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

    def _max_patch_repair_attempts(self, context: dict[str, Any]) -> int:
        config = context.get("config") or {}
        policy = config.get("policy") if isinstance(config, dict) else {}
        raw_value = policy.get("maxPatchRepairAttempts", 2) if isinstance(policy, dict) else 2
        try:
            return max(0, min(int(raw_value), 10))
        except (TypeError, ValueError):
            return 2

    def _run_react_loop(
        self,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        state: dict[str, Any] | None = None,
        budget: WorkerBudget | None = None,
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
        patch_repair_attempts = int(state.get("patch_repair_attempts", 0)) if state else 0

        if budget is not None and budget.tool_calls.exhausted:
            raise WorkerBudgetExceededError(
                dimension="tool_calls",
                limit=budget.tool_calls.limit or 0,
                attempted=budget.tool_calls.consumed + 1,
                consumed=budget.tool_calls.consumed,
            )

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
                budget=budget,
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
                return {
                    "status": "completed",
                    "summary": parsed["summary"],
                    "tool_results": deepcopy(tool_results),
                }

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
                    budget=budget,
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
                        "patch_repair_attempts": patch_repair_attempts,
                        "pending_tool_call": tool_call,
                        "pending_tool_spec": tool_spec,
                        "remaining_tool_calls": tool_calls[index + 1 :],
                    }
                    self._save_pending_react_state(task["id"], self._pending_react_tasks[task["id"]])
                    return {"status": "waiting_approval"}

                tool_results.append(tool_result)
                messages.append(self._tool_result_message(tool_call, tool_result))
                if self._is_patch_validation_failure(tool_spec["name"], tool_result["result"]):
                    patch_repair_attempts += 1
                    max_attempts = self._max_patch_repair_attempts(context)
                    if patch_repair_attempts > max_attempts:
                        raise RuntimeError(
                            "Patch repair attempts exhausted "
                            f"({patch_repair_attempts}/{max_attempts}): "
                            f"{self._tool_failure_summary(tool_spec, tool_result['result'])}"
                        )
                    continue
                self._advance_after_tool(session_id=session_id, task=task, tool_spec=tool_spec)

    def _request_provider_response(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        provider_context: dict[str, Any],
        budget: WorkerBudget | None = None,
    ) -> dict[str, Any]:
        if not self._should_stream_provider(provider_context):
            self._append_provider_trace(task=task, event_type="provider.request", payload=self._provider_trace_payload(provider_context))
            response = self._provider.generate(goal, provider_context)
            self._consume_budget_from_provider_response(
                session_id=session_id,
                task=task,
                budget=budget,
                response=response,
            )
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
        self._consume_budget_from_provider_response(
            session_id=session_id,
            task=task,
            budget=budget,
            response=response,
        )
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
                budget=None,
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
                    budget=None,
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
                budget=None,
            )
            if result["status"] == "completed":
                return self._complete_task(
                    session_id=task["sessionId"],
                    task=runtime_task,
                    summary=result["summary"],
                    context=state["context"],
                    tool_results=result.get("tool_results", []),
                )
            return runtime_task
        except Exception as exc:  # noqa: BLE001
            return self._fail_task(
                session_id=task["sessionId"],
                task=runtime_task,
                summary=str(exc),
                error_code="LOOP_EXECUTION_FAILED",
            )

    def _finalize_child_collaboration_after_approval(
        self,
        *,
        approval: dict[str, Any],
        runtime_task: dict[str, Any],
    ) -> None:
        child_task = self._blocked_child_collaboration_for_runtime_task(approval=approval, runtime_task=runtime_task)
        if child_task is None:
            return
        worker = self._prepare_child_collaboration_worker(child_task)
        if worker is None:
            return

        status = runtime_task.get("status")
        summary = (
            runtime_task.get("resultSummary")
            or (runtime_task.get("result") or {}).get("summary")
            or ("Child worker completed after approval." if status == "completed" else "Child worker stopped after approval.")
        )
        result_payload = {
            "summary": summary,
            "runtimeTaskId": runtime_task.get("id"),
            "runtimeTaskStatus": status,
            "approval": deepcopy(approval),
        }
        if isinstance(runtime_task.get("result"), dict):
            result_payload["runtimeTaskResult"] = deepcopy(runtime_task["result"])

        if status == "completed":
            completion = self._collaboration_service.complete_collaboration_task(
                {
                    "taskId": child_task["id"],
                    "workerId": worker["id"],
                    "result": result_payload,
                }
            )
            self._collaboration_service.send_agent_message(
                {
                    "senderWorkerId": completion["worker"]["id"],
                    "taskId": completion["task"]["id"],
                    "kind": "result",
                    "body": str(summary),
                    "payload": {
                        "executionMode": "process-rpc",
                        "approval": deepcopy(approval),
                        "runtimeTask": deepcopy(runtime_task),
                    },
                }
            )
            return

        if status in {"failed", "cancelled"}:
            error = {
                "code": runtime_task.get("errorCode") or "CHILD_WORKER_APPROVAL_RESUME_FAILED",
                "message": str(summary),
                "type": "ChildApprovalResumeError",
                "approval": deepcopy(approval),
                "runtimeTaskId": runtime_task.get("id"),
            }
            failure = self._collaboration_service.fail_collaboration_task(
                {
                    "taskId": child_task["id"],
                    "workerId": worker["id"],
                    "error": error,
                }
            )
            self._collaboration_service.send_agent_message(
                {
                    "senderWorkerId": failure["worker"]["id"],
                    "taskId": failure["task"]["id"],
                    "kind": "result",
                    "body": str(summary),
                    "payload": {"error": error, "runtimeTask": deepcopy(runtime_task)},
                }
            )
            return

        self._collaboration_service.update_collaboration_task(
            {
                "taskId": child_task["id"],
                "status": "blocked",
                "result": result_payload,
            }
        )

    def _blocked_child_collaboration_for_runtime_task(
        self,
        *,
        approval: dict[str, Any],
        runtime_task: dict[str, Any],
    ) -> dict[str, Any] | None:
        session_id = runtime_task.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            return None
        tasks = self._collaboration_service.list_collaboration_tasks({"sessionId": session_id}).get("tasks", [])
        for task in tasks:
            if task.get("status") != "blocked":
                continue
            result = task.get("result") if isinstance(task.get("result"), dict) else {}
            approval_payload = result.get("approval") if isinstance(result.get("approval"), dict) else {}
            if result.get("runtimeTaskId") == approval.get("taskId"):
                return task
            if approval_payload.get("id") == approval.get("id"):
                return task
            if approval_payload.get("approvalId") == approval.get("id"):
                return task
        return None

    def _prepare_child_collaboration_worker(self, child_task: dict[str, Any]) -> dict[str, Any] | None:
        worker_id = child_task.get("assignedWorkerId")
        if not isinstance(worker_id, str) or not worker_id:
            return None
        try:
            worker = self._collaboration_service.get_agent_worker({"workerId": worker_id})["worker"]
        except ValueError:
            return None
        return self._collaboration_service.upsert_agent_worker(
            {
                "workerId": worker["id"],
                "name": worker["name"],
                "role": worker["role"],
                "status": "busy",
                "currentTaskId": child_task["id"],
                "capabilities": worker.get("capabilities", []),
                "metadata": worker.get("metadata", {}),
            }
        )["worker"]

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

    def _context_with_worker_budget(self, context: dict[str, Any], budget: WorkerBudget) -> dict[str, Any]:
        if budget.tokens.limit is None:
            return context
        updated_context = deepcopy(context)
        config = deepcopy(updated_context.get("config") or {})
        provider = deepcopy(config.get("provider") or {})
        existing = provider.get("maxTokens") or provider.get("maxOutputTokens")
        try:
            existing_limit = int(existing) if existing is not None else None
        except (TypeError, ValueError):
            existing_limit = None
        remaining = budget.tokens.remaining
        capped_limit = remaining if existing_limit is None or remaining is None else min(existing_limit, remaining)
        if capped_limit is not None:
            provider["maxTokens"] = capped_limit
            provider["maxOutputTokens"] = capped_limit
            config["provider"] = provider
            updated_context["config"] = config
        return updated_context

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
        elif tool_name == "task":
            arguments.setdefault("priority", 3)

    def _plan_step_for_tool(self, tool_name: str) -> str:
        return {
            "list_dir": "inspect-workspace",
            "search_files": "search-relevant-files",
            "read_file": "search-relevant-files",
            "task": "task",
            "run_command": "run-command",
            "apply_patch": "apply-patch",
            "git_status": "git-status",
            "git_diff": "git-diff",
        }.get(tool_name, tool_name.replace("_", "-"))

    def _ensure_tool_allowed_for_child_worker(self, tool_name: str) -> None:
        allowed = self._child_tool_allowlist()
        if allowed is None or tool_name in set(allowed):
            return
        raise ValueError(f"Tool is not allowed in child worker process: {tool_name}")

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
        updated_task = self._store.update_task(
            task_id=task["id"],
            status=task["status"],
            plan=task["plan"],
        )
        task.update(updated_task)
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.updated",
            payload={"status": task["status"], "plan": task["plan"], "currentStep": task.get("currentStep")},
        )

    def _run_minimal_loop(
        self,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        budget: WorkerBudget | None = None,
    ) -> list[dict[str, Any]]:
        tool_results: list[dict[str, Any]] = []
        tool_sequence = self._provider.choose_tool_sequence(goal=goal, context=context)

        for index, tool_spec in enumerate(tool_sequence):
            tool_result = self._execute_tool(
                session_id=session_id,
                task=task,
                tool_spec=tool_spec,
                budget=budget,
            )
            tool_results.append(tool_result)
            if self._is_patch_validation_failure(tool_spec["name"], tool_result["result"]):
                raise RuntimeError(self._tool_failure_summary(tool_spec, tool_result["result"]))
            if task["status"] == "waiting_approval":
                self._publish(
                    session_id=session_id,
                    task=task,
                    event_type="task.updated",
                    payload={"status": task["status"], "plan": task["plan"], "currentStep": task.get("currentStep")},
                )
                return tool_results
            task["plan"] = self._planner.advance(
                task["plan"],
                tool_spec["plan_step_id"],
                next_step_id=self._next_step_id(tool_sequence, index),
            )
            updated_task = self._store.update_task(
                task_id=task["id"],
                status=task["status"],
                plan=task["plan"],
            )
            task.update(updated_task)
            self._publish(
                session_id=session_id,
                task=task,
                event_type="task.updated",
                payload={"status": task["status"], "plan": task["plan"], "currentStep": task.get("currentStep")},
            )

        follow_up_tool = self._provider.pick_follow_up_tool(context=context, tool_results=tool_results)
        if follow_up_tool is not None:
            tool_results.append(
                self._execute_tool(
                    session_id=session_id,
                    task=task,
                    tool_spec=follow_up_tool,
                    budget=budget,
                )
            )

        task["plan"] = self._planner.advance(
            task["plan"],
            "search-relevant-files",
            next_step_id="summarize-findings",
        )
        updated_task = self._store.update_task(
            task_id=task["id"],
            status=task["status"],
            plan=task["plan"],
        )
        task.update(updated_task)
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.updated",
            payload={"status": task["status"], "plan": task["plan"], "currentStep": task.get("currentStep")},
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
        budget: WorkerBudget | None = None,
    ) -> dict[str, Any]:
        self._ensure_tool_allowed_for_child_worker(tool_spec["name"])
        tool_call_id = tool_spec.get("id") or self._store.new_id("tc")
        tool_arguments = {
            **tool_spec["arguments"],
            "taskId": task["id"],
            "sessionId": session_id,
        }
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
        if tool_spec["name"] == "task":
            result = self._subagent_service.dispatch(tool_arguments)
        else:
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

        if self._is_patch_validation_failure(tool_spec["name"], result):
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
            raise RuntimeError(self._tool_failure_summary(tool_spec, result))

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
            return tool_result

        if tool_spec["name"] == "apply_patch":
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

    def _consume_budget_from_provider_response(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        budget: WorkerBudget | None,
        response: dict[str, Any],
    ) -> None:
        if budget is None:
            return
        raw = response.get("raw")
        usage = raw.get("usage") if isinstance(raw, dict) else None
        consumed = budget.consume_provider_usage(usage)
        if consumed <= 0:
            return
        self._publish(
            session_id=session_id,
            task=task,
            event_type="collab.worker.budget.updated",
            payload={
                "dimension": "tokens",
                "consumed": consumed,
                "budget": budget.to_metadata(),
            },
        )

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

    def _next_step_id(self, tool_sequence: list[dict[str, Any]], current_index: int) -> str | None:
        if current_index + 1 >= len(tool_sequence):
            return "summarize-findings"
        return tool_sequence[current_index + 1]["plan_step_id"]
