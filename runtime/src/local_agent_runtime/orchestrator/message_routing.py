"""Message Routing Mixin — extracted from MessageFlowMixin.

Handles the send_message entry point: routing, task creation,
context building, and background message dispatch.
"""
from __future__ import annotations

import logging
import re
import threading
from copy import deepcopy
from inspect import Parameter, signature
from pathlib import Path
from typing import Any
from subprocess import CompletedProcess, DEVNULL, PIPE, SubprocessError, run as subprocess_run

logger = logging.getLogger(__name__)

_WRITE_WORKTREE_SCENARIOS = {
    "code_edit",
    "debug",
    "test_write",
    "doc_write",
    "multi_step_task",
    "supervised_task",
    "swarm_task",
}


class MessageRoutingMixin:
    """Mixin providing message routing and background dispatch."""

    def _routing_dict_from_decision(self, routing: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
        strategy = routing.strategy.value
        tool_continuation = self._routing_tool_continuation_from_decision(routing, strategy)
        return {
            "scenario": routing.scenario.value,
            "strategy": strategy,
            "confidence": routing.confidence,
            "max_steps": routing.max_steps,
            "enable_reflection": routing.enable_reflection,
            "enable_planning": routing.enable_planning,
            "reasoning": routing.reasoning,
            "skill_id": routing.skill_id,
            "toolContinuation": tool_continuation,
            "profile_snapshot": self._runtime_profile_snapshot(context),
            "roleSnapshot": {
                "runtimeRole": "root",
                "agentType": "root",
                "profileId": None,
                "profileVersion": 1,
                "agentProfile": {
                    "agentType": "root",
                    "baseRuntimeRole": "root",
                    "toolPolicy": "routing",
                    "capabilities": ["orchestrate", "delegate", "synthesize"],
                    "scopes": [],
                    "riskLevel": "medium",
                    "source": "runtime_default",
                    "version": 1,
                },
                "toolPolicy": "routing",
                "scopes": [],
                "riskLevel": "medium",
                "budget": {},
            },
        }

    def _routing_tool_continuation_from_decision(self, routing: Any, strategy: str) -> dict[str, Any]:
        metadata = getattr(routing, "metadata", None)
        raw = metadata.get("toolContinuation") if isinstance(metadata, dict) else None
        if isinstance(raw, dict) and isinstance(raw.get("allowToolsAfterTaskResults"), bool):
            continuation = {
                "allowToolsAfterTaskResults": raw["allowToolsAfterTaskResults"],
                "allowMoreSubtasksAfterTaskResults": raw.get("allowMoreSubtasksAfterTaskResults") is True,
                "maxTaskToolCalls": self._bounded_routing_task_call_budget(raw.get("maxTaskToolCalls")),
                "source": str(raw.get("source") or "routing_metadata"),
            }
            rationale = raw.get("rationale")
            if isinstance(rationale, str) and rationale.strip():
                continuation["rationale"] = rationale.strip()[:500]
            return continuation
        return {
            "allowToolsAfterTaskResults": strategy in {"plan_execute", "plan_supervise", "plan_swarm"},
            "allowMoreSubtasksAfterTaskResults": False,
            "maxTaskToolCalls": 1,
            "source": "strategy_fallback",
        }

    @staticmethod
    def _bounded_routing_task_call_budget(value: Any) -> int:
        try:
            budget = int(value)
        except (TypeError, ValueError):
            return 1
        if budget <= 0:
            return 1
        return min(budget, 20)

    def _attach_main_workflow_state(
        self,
        *,
        routing: dict[str, Any],
        session: dict[str, Any],
        goal: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach auditable main-workflow state to routing metadata."""
        config = self._store.get_config({})["config"]
        autonomy_profile = self._active_config_profile(config, "autonomy") or {}
        policy = config.get("policy") if isinstance(config.get("policy"), dict) else {}
        approval_mode = str(policy.get("approvalMode") or "on_write_or_command")
        confidence = self._safe_float(routing.get("confidence"), 0.0)
        workflow = {
            "intentConfidence": {
                "score": confidence,
                "band": self._intent_confidence_band(confidence),
                "scenario": routing.get("scenario"),
                "strategy": routing.get("strategy"),
                "reasoning": routing.get("reasoning"),
            },
            "automation": {
                "level": self._automation_level(
                    approval_mode=approval_mode,
                    autonomy_profile=autonomy_profile,
                    background=params.get("background") is True,
                ),
                "approvalMode": approval_mode,
                "autonomyProfileId": autonomy_profile.get("id"),
                "autonomyLevel": autonomy_profile.get("level"),
                "background": params.get("background") is True,
                "source": "config.policy+autonomy",
            },
            "budget": self._main_workflow_budget(
                routing=routing,
                config=config,
                autonomy_profile=autonomy_profile,
            ),
            "workspaceSnapshot": self._main_workflow_workspace_snapshot(session),
            "userTakeover": {
                "state": "none",
                "mode": params.get("mode") or ("background" if params.get("background") is True else "new_task"),
                "targetTaskId": params.get("taskId") or params.get("task_id"),
                "latestUserMessagePreview": str(goal or "")[:200],
            },
        }
        return {**routing, "mainWorkflow": workflow}

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _intent_confidence_band(confidence: float) -> str:
        if confidence >= 0.8:
            return "high"
        if confidence >= 0.5:
            return "medium"
        return "low"

    @staticmethod
    def _automation_level(
        *,
        approval_mode: str,
        autonomy_profile: dict[str, Any],
        background: bool,
    ) -> str:
        mode = approval_mode.strip().lower()
        if mode in {"none", "never", "off"}:
            return "full-auto"
        if mode == "strict":
            return "assist"
        level = str(autonomy_profile.get("level") or "").upper()
        if background or level in {"L3", "L4"}:
            return "auto"
        if mode in {"on_write_or_command", "write", "high_risk"}:
            return "auto"
        return "assist"

    def _main_workflow_budget(
        self,
        *,
        routing: dict[str, Any],
        config: dict[str, Any],
        autonomy_profile: dict[str, Any],
    ) -> dict[str, Any]:
        policy = config.get("policy") if isinstance(config.get("policy"), dict) else {}
        provider = config.get("provider") if isinstance(config.get("provider"), dict) else {}
        return {
            "maxSteps": self._safe_int(routing.get("max_steps"), autonomy_profile.get("maxSteps"), policy.get("maxTaskSteps"), 20),
            "maxParallelSubtasks": self._safe_int(autonomy_profile.get("maxParallelSubtasks"), 4),
            "retryLimit": self._safe_int(autonomy_profile.get("retryLimit"), 0),
            "taskTimeoutMs": self._safe_int(autonomy_profile.get("timeoutMs"), policy.get("commandTimeoutMs"), 600000),
            "commandTimeoutMs": self._safe_int(policy.get("commandTimeoutMs"), 600000),
            "providerTimeoutSeconds": self._safe_int(provider.get("timeout"), 30),
            "maxContextTokens": self._safe_int(provider.get("maxContextTokens"), 256000),
            "convergenceRequired": True,
        }

    @staticmethod
    def _safe_int(*values: Any) -> int:
        for value in values:
            try:
                if value is None:
                    continue
                return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    def _main_workflow_workspace_snapshot(self, session: dict[str, Any]) -> dict[str, Any]:
        workspace_root = self._session_workspace_root(session)
        snapshot: dict[str, Any] = {
            "workspaceId": session.get("workspaceId"),
            "workspaceRoot": workspace_root,
            "exists": bool(workspace_root and Path(workspace_root).exists()),
            "git": {"isRepo": False},
        }
        if not workspace_root or not Path(workspace_root).exists():
            return snapshot
        root = Path(workspace_root)
        git_info = self._git_workspace_status(root)
        snapshot["git"] = git_info
        snapshot["dirty"] = bool(git_info.get("dirty"))
        snapshot["dirtyFileCount"] = git_info.get("dirtyFileCount", 0)
        return snapshot

    def _session_workspace_root(self, session: dict[str, Any]) -> str:
        root = session.get("workspaceRoot")
        if isinstance(root, str) and root.strip():
            return str(Path(root).resolve())
        workspace_id = session.get("workspaceId")
        if not workspace_id:
            return ""
        try:
            workspace = self._store.require_workspace(workspace_id)
        except Exception:  # noqa: BLE001
            return ""
        workspace_root = workspace.get("rootPath")
        if isinstance(workspace_root, str) and workspace_root.strip():
            return str(Path(workspace_root).resolve())
        return ""

    def _git_workspace_status(self, workspace_root: Path) -> dict[str, Any]:
        def run_git(args: list[str]) -> CompletedProcess[str]:
            return subprocess_run(
                ["git", *args],
                cwd=workspace_root,
                stdout=PIPE,
                stderr=DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )

        try:
            inside = run_git(["rev-parse", "--is-inside-work-tree"])
        except (SubprocessError, OSError):
            return {"isRepo": False}
        if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
            return {"isRepo": False}

        branch = run_git(["branch", "--show-current"])
        status = run_git(["status", "--porcelain"])
        lines = [line for line in status.stdout.splitlines() if line.strip()] if status.returncode == 0 else []
        changed = [
            {
                "status": line[:2].strip() or "??",
                "path": line[3:].strip() if len(line) > 3 else line.strip(),
            }
            for line in lines[:25]
        ]
        return {
            "isRepo": True,
            "branch": branch.stdout.strip() if branch.returncode == 0 else "",
            "dirty": bool(lines),
            "dirtyFileCount": len(lines),
            "changedFilesPreview": changed,
        }

    def _persist_task_routing(
        self,
        task: dict[str, Any],
        routing: dict[str, Any],
    ) -> dict[str, Any]:
        updated = self._store.update_task(task_id=task["id"], routing=routing)
        return {**task, "routing": updated.get("routing") or routing}

    def _maybe_bind_task_worktree(
        self,
        *,
        session: dict[str, Any],
        task: dict[str, Any],
        routing: dict[str, Any],
    ) -> dict[str, Any] | None:
        worktree_service = getattr(self, "_worktree_service", None)
        if worktree_service is None or not self._should_auto_bind_worktree(routing):
            return None

        workspace_root_text = session.get("workspaceRoot")
        if not workspace_root_text:
            try:
                workspace = self._store.require_workspace(session["workspaceId"])
                workspace_root_text = workspace.get("rootPath")
            except Exception:  # noqa: BLE001
                workspace_root_text = ""
        if not isinstance(workspace_root_text, str) or not workspace_root_text.strip():
            return None

        existing = self._store.get_worktree_by_task({"taskId": task["id"]}).get("worktree")
        if existing is not None:
            return existing

        worktree_config = self._worktree_config()
        workspace_root = Path(workspace_root_text).resolve()
        task_id = str(task["id"])
        branch_prefix = self._safe_worktree_segment(str(worktree_config.get("branchPrefix") or "agent"))
        branch_name = f"{branch_prefix}/{self._safe_worktree_segment(task_id)}"
        worktree_path = self._worktree_path_for_task(workspace_root, task_id, worktree_config)

        try:
            result = worktree_service.create_for_task({
                "workspaceId": session["workspaceId"],
                "sessionId": session["id"],
                "taskId": task_id,
                "baseRef": str(worktree_config.get("baseRef") or "HEAD"),
                "branchName": branch_name,
                "worktreePath": str(worktree_path),
                "cleanupPolicy": str(worktree_config.get("cleanupPolicy") or "ask_user"),
                "mergePolicy": str(worktree_config.get("mergePolicy") or "approval_required"),
            })
            worktree = result.get("worktree")
            if isinstance(worktree, dict):
                self._publish(
                    session_id=session["id"],
                    task=task,
                    event_type="task.worktree.bound",
                    payload={
                        "worktreeId": worktree.get("id"),
                        "worktreePath": worktree.get("worktreePath"),
                        "branchName": worktree.get("branchName"),
                        "baseRef": worktree.get("baseRef"),
                        "status": worktree.get("status"),
                    },
                )
                return worktree
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to auto-bind worktree for task %s", task_id, exc_info=True)
            self._publish(
                session_id=session["id"],
                task=task,
                event_type="task.worktree.bind_failed",
                payload={"error": str(exc), "taskId": task_id},
            )
        return None

    def _should_auto_bind_worktree(self, routing: dict[str, Any]) -> bool:
        worktree_config = self._worktree_config()
        if worktree_config.get("autoBindWriteTasks", True) is False:
            return False
        scenario = str(routing.get("scenario") or "")
        return scenario in _WRITE_WORKTREE_SCENARIOS

    def _worktree_config(self) -> dict[str, Any]:
        config = self._store.get_config({})["config"]
        worktree_config = config.get("worktree") if isinstance(config, dict) else {}
        return worktree_config if isinstance(worktree_config, dict) else {}

    def _worktree_path_for_task(
        self,
        workspace_root: Path,
        task_id: str,
        worktree_config: dict[str, Any],
    ) -> Path:
        configured_root = str(worktree_config.get("pathRoot") or "").strip()
        if configured_root:
            root = Path(configured_root)
            if not root.is_absolute():
                root = workspace_root.parent / root
        else:
            root = workspace_root.parent / f"{workspace_root.name}.worktrees"
        return root.resolve() / self._safe_worktree_segment(task_id)

    def _safe_worktree_segment(self, value: str) -> str:
        segment = re.sub(r"[^A-Za-z0-9._/-]+", "-", value.strip())
        segment = re.sub(r"/+", "/", segment).strip("/.")
        parts = [part for part in segment.split("/") if part and part not in {".", ".."}]
        return "/".join(parts) or "task"

    def _context_with_worktree_binding(
        self,
        context: dict[str, Any],
        worktree: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(worktree, dict):
            return context
        worktree_path = worktree.get("worktreePath")
        if not isinstance(worktree_path, str) or not worktree_path.strip():
            return context

        bound_context = {**context}
        original_root = bound_context.get("workspace_root")
        bound_context["original_workspace_root"] = original_root
        bound_context["workspace_root"] = worktree_path
        bound_context["active_worktree"] = {
            "id": worktree.get("id"),
            "path": worktree_path,
            "branchName": worktree.get("branchName"),
            "baseRef": worktree.get("baseRef"),
            "status": worktree.get("status"),
            "originalWorkspaceRoot": original_root,
        }

        messages = list(bound_context.get("messages") or [])
        binding_text = "\n".join([
            "Active worktree:",
            f"- path: {worktree_path}",
            f"- branch: {worktree.get('branchName')}",
            f"- base: {worktree.get('baseRef')}",
            "- use this worktree for file, shell and git operations for this task.",
        ])
        if messages and messages[-1].get("role") == "user":
            content = str(messages[-1].get("content") or "")
            if "Active worktree:" not in content:
                messages[-1] = {**messages[-1], "content": f"{content}\n\n{binding_text}"}
        else:
            messages.append({"role": "user", "content": binding_text})
        bound_context["messages"] = messages
        return bound_context

    def send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._shutting_down:
            raise RuntimeError("服务正在关闭，暂不接受新任务")
        session = self._store.require_session(params["sessionId"])
        goal = params["content"]
        client_message_id = params.get("clientMessageId")

        explicit_supplement = params.get("mode") == "supplement"
        explicit_task_id = params.get("taskId") or params.get("task_id")
        is_queued_mode = params.get("mode") == "queued"
        should_auto_supplement = not is_queued_mode and params.get("background") is not True and params.get("newTask") is not True
        if explicit_supplement or should_auto_supplement:
            active_task = (
                self._find_supplement_target_task(
                    session_id=session["id"],
                    task_id=str(explicit_task_id) if explicit_task_id else None,
                    strict=explicit_supplement,
                )
                if explicit_task_id
                else self._find_open_session_task(session["id"])
            )
            if active_task is not None:
                return self._attach_supplemental_message(session_id=session["id"], task=active_task, content=goal)

        # --- Queued mode: create task but don't execute if another is running ---
        if params.get("mode") == "queued":
            active_task = self._find_open_session_task(session["id"])
            if active_task is not None:
                routing = self._route_goal(goal)
                routing_dict = self._routing_dict_from_decision(routing)
                routing_dict = self._attach_main_workflow_state(
                    routing=routing_dict,
                    session=session,
                    goal=goal,
                    params=params,
                )
                queued_task = self._store.create_task(
                    session_id=session["id"],
                    task_type="edit",
                    goal=goal,
                    plan=[],
                    status="queued",
                    routing=routing_dict,
                )
                user_msg = self._store.create_message(
                    session_id=session["id"],
                    task_id=queued_task["id"],
                    role="user",
                    content=goal,
                    client_message_id=client_message_id,
                    kind="normal",
                    status="completed",
                )
                worktree = self._maybe_bind_task_worktree(
                    session=session,
                    task=queued_task,
                    routing=routing_dict,
                )
                if worktree is not None:
                    routing_dict["activeWorktree"] = worktree
                    queued_task = self._persist_task_routing(queued_task, routing_dict)
                self._publish(session["id"], queued_task, "message.created", {"message": user_msg})
                self._publish(session["id"], queued_task, "task.created", {"status": "queued", "goal": goal})
                self._publish(session["id"], queued_task, "task.queued", {"status": "queued", "goal": goal})
                self._publish(
                    session["id"],
                    queued_task,
                    "task.routing.decided",
                    {**routing_dict, "latency_ms": 0},
                )
                return {"task": queued_task, "userMessage": user_msg}

        # --- Phase 0: MetaRouter scenario classification ---
        import time as _time
        _route_t0 = _time.monotonic()
        routing_span = self._tracer.start_span("routing_decision", attributes={"goal": goal[:200]})
        try:
            routing = self._route_goal(goal)
        except Exception:
            self._tracer.end_span(routing_span.span_id, status="error")
            raise
        _route_latency_ms = int((_time.monotonic() - _route_t0) * 1000)
        routing_dict = self._routing_dict_from_decision(routing)
        routing_dict = self._attach_main_workflow_state(
            routing=routing_dict,
            session=session,
            goal=goal,
            params=params,
        )
        self._tracer.end_span(
            routing_span.span_id,
            status="ok",
            attributes={
                "scenario": routing.scenario.value,
                "strategy": routing.strategy.value,
                "confidence": routing.confidence,
                "skill_id": routing.skill_id,
                "latency_ms": _route_latency_ms,
            },
        )

        if params.get("background") is True:
            # Pass routing info to the background worker without blocking on
            # context build — the worker will build context with routing data.
            plan = self._planner.plan(goal, context={"routing": routing_dict})
            task = self._store.create_task(
                session_id=session["id"],
                task_type="edit",
                goal=goal,
                plan=plan,
                acceptance_criteria=self._default_acceptance_criteria(goal),
                out_of_scope=self._default_out_of_scope(),
                routing=routing_dict,
            )
            runtime_task = {**task, "plan": plan}
            user_msg = self._store.create_message(
                session_id=session["id"],
                task_id=runtime_task["id"],
                role="user",
                content=goal,
                client_message_id=client_message_id,
                kind="normal",
                status="completed",
            )
            assistant_msg = self._store.create_message(
                session_id=session["id"],
                task_id=runtime_task["id"],
                role="assistant",
                content="",
                kind="normal",
                status="streaming",
            )
            self._store.update_task(
                task_id=runtime_task["id"],
                active_assistant_message_id=assistant_msg["id"],
            )
            runtime_task["activeAssistantMessageId"] = assistant_msg["id"]
            worktree = self._maybe_bind_task_worktree(
                session=session,
                task=runtime_task,
                routing=routing_dict,
            )
            if worktree is not None:
                routing_dict["activeWorktree"] = worktree
                runtime_task = self._persist_task_routing(runtime_task, routing_dict)
            self._record_routing_proposal(
                session_id=session["id"],
                task_id=runtime_task["id"],
                goal=goal,
                routing=routing,
                routing_dict=routing_dict,
            )
            self._publish(
                session_id=session["id"],
                task=runtime_task,
                event_type="message.created",
                payload={"message": user_msg},
            )
            self._publish(
                session_id=session["id"],
                task=runtime_task,
                event_type="message.created",
                payload={"message": assistant_msg},
            )
            self._publish(
                session_id=session["id"],
                task=runtime_task,
                event_type="task.created",
                payload={"status": runtime_task["status"], "goal": goal},
            )
            self._publish(
                session_id=session["id"],
                task=runtime_task,
                event_type="task.routing.decided",
                payload={**routing_dict, "latency_ms": _route_latency_ms},
            )
            self._start_background_message(
                session_id=session["id"],
                task=runtime_task,
                goal=goal,
                context=None,
                routing=routing_dict,
                skill_id=routing.skill_id,
            )
            self._record_skill_usage(
                task_id=runtime_task["id"],
                session_id=session["id"],
                skill_id=routing.skill_id,
            )
            return {"task": runtime_task, "userMessage": user_msg, "assistantMessage": assistant_msg}

        context = self._context_builder.build(session_id=session["id"], goal=goal, skill_id=routing.skill_id, lightweight=False)
        if isinstance(context.get("skillFallback"), dict):
            routing_dict["skillFallback"] = context["skillFallback"]
        routing_dict["profile_snapshot"] = self._runtime_profile_snapshot(context)
        # Inject routing decision into context as a plain dict for JSON safety.
        context["routing"] = routing_dict
        # Emit tool filter event if skill filtering was applied
        self._maybe_publish_tool_filter(context, routing.skill_id)
        logger.info(
            "Routing decision: scenario=%s strategy=%s confidence=%.2f max_steps=%d skill=%s",
            routing.scenario.value, routing.strategy.value,
            routing.confidence, routing.max_steps, routing.skill_id,
        )

        plan = self._planner.plan(goal, context=context)
        task = self._store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal=goal,
            plan=plan,
            acceptance_criteria=self._default_acceptance_criteria(goal),
            out_of_scope=self._default_out_of_scope(),
            routing=routing_dict,
        )
        runtime_task = {**task, "plan": plan}
        user_msg = self._store.create_message(
            session_id=session["id"],
            task_id=runtime_task["id"],
            role="user",
            content=goal,
            client_message_id=client_message_id,
            kind="normal",
            status="completed",
        )
        assistant_msg = self._store.create_message(
            session_id=session["id"],
            task_id=runtime_task["id"],
            role="assistant",
            content="",
            kind="normal",
            status="streaming",
        )
        self._store.update_task(
            task_id=runtime_task["id"],
            active_assistant_message_id=assistant_msg["id"],
        )
        runtime_task["activeAssistantMessageId"] = assistant_msg["id"]
        context = self._context_with_task_focus(context, runtime_task)
        worktree = self._maybe_bind_task_worktree(
            session=session,
            task=runtime_task,
            routing=routing_dict,
        )
        if worktree is not None:
            routing_dict["activeWorktree"] = worktree
            runtime_task = self._persist_task_routing(runtime_task, routing_dict)
            context["routing"] = routing_dict
            context = self._context_with_worktree_binding(context, worktree)

        self._record_routing_proposal(
            session_id=session["id"],
            task_id=runtime_task["id"],
            goal=goal,
            routing=routing,
            routing_dict=routing_dict,
        )

        self._record_skill_usage(
            task_id=runtime_task["id"],
            session_id=session["id"],
            skill_id=routing.skill_id,
        )

        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="message.created",
            payload={"message": user_msg},
        )
        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="message.created",
            payload={"message": assistant_msg},
        )
        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="task.created",
            payload={"status": runtime_task["status"], "goal": goal},
        )
        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="task.started",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task["plan"],
                "currentStep": runtime_task.get("currentStep"),
                "context": self._event_context_summary(context),
                "routing": context["routing"],
            },
        )
        self._fire_hooks("before_task_start", session["id"], runtime_task, extra_context={"routing": context.get("routing")})
        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="task.routing.decided",
            payload={**routing_dict, "latency_ms": _route_latency_ms},
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
            goal=goal,
            context=context,
        )

    def _route_goal(self, goal: str) -> Any:
        route_context = {"config": self._store.get_config({})["config"]}
        route = self._meta_router.route
        try:
            params = signature(route).parameters
            accepts_context = (
                len(params) >= 2
                or any(param.kind == Parameter.VAR_KEYWORD for param in params.values())
            )
        except (TypeError, ValueError):
            accepts_context = True
        if accepts_context:
            return route(goal, route_context)
        return route(goal)

    def _start_background_message(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any] | None,
        routing: dict[str, Any] | None = None,
        skill_id: str | None = None,
    ) -> None:
        worker = threading.Thread(
            target=self._run_background_message,
            kwargs={
                "session_id": session_id,
                "task": deepcopy(task),
                "goal": goal,
                "context": deepcopy(context) if context is not None else None,
                "routing": deepcopy(routing) if routing is not None else None,
                "skill_id": skill_id,
            },
            name=f"message-task-{task['id']}",
            daemon=True,
        )
        worker.start()
