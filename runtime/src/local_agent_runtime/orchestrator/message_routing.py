"""Message Routing Mixin — extracted from MessageFlowMixin.

Handles the send_message entry point: routing, task creation,
context building, and background message dispatch.
"""
from __future__ import annotations

import logging
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any
from subprocess import CompletedProcess, DEVNULL, PIPE, SubprocessError, run as subprocess_run

logger = logging.getLogger(__name__)

_INLINE_FILE_REFERENCE_PATTERN = re.compile(r"(^|\s)@([^\s@]+)")
_INLINE_FILE_REFERENCE_TRAILING = "),.;:!?，。；：！？）"
_INLINE_FILE_REFERENCE_TERMINATORS = ("，", "。", "；", "！", "？")



class MessageRoutingMixin:
    """Mixin providing message routing and background dispatch."""

    def _model_first_routing_context(
        self,
        *,
        goal: str,
        session: dict[str, Any],
        params: dict[str, Any],
        requested_skill_id: str | None,
    ) -> dict[str, Any]:
        """Build the minimal runtime context for the provider/tool loop.

        This is deliberately not an intent router.  The provider decides the
        actual trajectory by producing assistant text and tool calls; the
        backend only carries stable execution metadata and explicit user or
        session constraints.
        """
        max_steps = self._safe_int(
            params.get("maxSteps"),
            params.get("max_steps"),
            (self._store.get_config({})["config"].get("policy") or {}).get("maxTaskSteps"),
            30,
        )
        routing: dict[str, Any] = {
            "mode": "model_first",
            "max_steps": max_steps,
            "skill_id": requested_skill_id,
            "toolContinuation": {
                "allowToolsAfterTaskResults": True,
                "allowMoreSubtasksAfterTaskResults": True,
                "source": "model_first_default",
            },
        }
        if requested_skill_id:
            routing["requestedSkillId"] = requested_skill_id
        if self._explicit_plan_mode_requested(params):
            routing["planModeToolsEnabled"] = True
            routing["explicitPlanMode"] = True
        if params.get("background") is True:
            routing["background"] = True
        routing = self._apply_session_launch_to_routing(routing, session)
        return self._attach_main_workflow_state(
            routing=routing,
            session=session,
            goal=goal,
            params=params,
        )


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
        automation = self._main_workflow_automation(
            approval_mode=approval_mode,
            autonomy_profile=autonomy_profile,
            background=params.get("background") is True,
        )
        budget = self._main_workflow_budget(
            routing=routing,
            config=config,
            autonomy_profile=autonomy_profile,
        )
        workflow = {
            "automation": automation,
            "budget": budget,
            "convergence": self._initial_main_workflow_convergence(automation=automation, budget=budget),
            "workspaceSnapshot": self._main_workflow_workspace_snapshot(session, include_git=False),
            "userTakeover": {
                "state": "none",
                "mode": params.get("mode") or ("background" if params.get("background") is True else "new_task"),
                "targetTaskId": params.get("taskId") or params.get("task_id"),
                "latestUserMessagePreview": str(goal or "")[:200],
            },
        }
        explicit_plan_mode = self._explicit_plan_mode_requested(params)
        return {
            **routing,
            "mainWorkflow": workflow,
            **(
                {
                    "planModeToolsEnabled": True,
                    "explicitPlanMode": True,
                }
                if explicit_plan_mode
                else {}
            ),
        }

    @staticmethod
    def _explicit_plan_mode_requested(params: dict[str, Any]) -> bool:
        for key in ("planMode", "plan_mode", "explicitPlanMode", "explicit_plan_mode", "planModeToolsEnabled", "plan_mode_tools_enabled"):
            if params.get(key) is True:
                return True
        mode = str(params.get("mode") or params.get("executionMode") or params.get("execution_mode") or "").strip().lower()
        return mode in {"plan", "plan_mode", "approval_plan"}

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

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

    def _main_workflow_automation(
        self,
        *,
        approval_mode: str,
        autonomy_profile: dict[str, Any],
        background: bool,
    ) -> dict[str, Any]:
        level = self._automation_level(
            approval_mode=approval_mode,
            autonomy_profile=autonomy_profile,
            background=background,
        )
        return {
            "level": level,
            "approvalMode": approval_mode,
            "autonomyProfileId": autonomy_profile.get("id"),
            "autonomyLevel": autonomy_profile.get("level"),
            "background": background,
            "source": "config.policy+autonomy",
            "controls": {
                "pause": True,
                "resume": True,
                "cancel": True,
                "supplement": True,
                "continueAfterBudgetExhaustion": "requires_user_action",
                "continueAfterConvergence": "requires_user_action",
            },
            "convergencePolicy": {
                "decisionKind": "budget_convergence",
                "onBudgetPressure": "record_and_continue",
                "onBudgetExhaustion": "summarize_partial_for_review",
                "autoContinuePastHardBudget": False,
            },
        }

    def _main_workflow_budget(
        self,
        *,
        routing: dict[str, Any],
        config: dict[str, Any],
        autonomy_profile: dict[str, Any],
    ) -> dict[str, Any]:
        policy = config.get("policy") if isinstance(config.get("policy"), dict) else {}
        provider = config.get("provider") if isinstance(config.get("provider"), dict) else {}
        max_steps = self._safe_int(autonomy_profile.get("maxSteps"), policy.get("maxTaskSteps"), 20)
        max_context_tokens = self._safe_int(provider.get("maxContextTokens"), 256000)
        child_task_timeout_ms = self._safe_int(
            autonomy_profile.get("childTaskTimeoutMs"),
            autonomy_profile.get("childTimeoutMs"),
            policy.get("childTaskTimeoutMs"),
            policy.get("childTimeoutMs"),
            autonomy_profile.get("timeoutMs"),
            policy.get("commandTimeoutMs"),
            600000,
        )
        return {
            "maxSteps": max_steps,
            "maxParallelSubtasks": self._safe_int(autonomy_profile.get("maxParallelSubtasks"), 4),
            "retryLimit": self._safe_int(autonomy_profile.get("retryLimit"), 0),
            "taskTimeoutMs": self._safe_int(autonomy_profile.get("timeoutMs"), policy.get("commandTimeoutMs"), 600000),
            "childTaskTimeoutMs": child_task_timeout_ms,
            "commandTimeoutMs": self._safe_int(policy.get("commandTimeoutMs"), 600000),
            "providerTimeoutSeconds": self._safe_int(provider.get("timeout"), 30),
            "maxContextTokens": max_context_tokens,
            "runtimeMaxStepsHint": self._safe_int(routing.get("max_steps"), 0),
            "pressure": "normal",
            "exhausted": False,
            "convergenceRequired": True,
            "dimensions": {
                "steps": self._main_workflow_step_dimension(consumed=0, limit=max_steps),
                "context": {
                    "limit": max_context_tokens,
                    "pressure": "unknown",
                },
            },
        }

    @staticmethod
    def _main_workflow_step_dimension(*, consumed: int, limit: int) -> dict[str, Any]:
        remaining = max(0, limit - consumed) if limit > 0 else 0
        ratio = (consumed / limit) if limit > 0 else 1.0
        if remaining <= 0:
            pressure = "exhausted"
        elif ratio >= 0.85 or remaining <= 1:
            pressure = "critical"
        elif ratio >= 0.65 or remaining <= 2:
            pressure = "watch"
        else:
            pressure = "normal"
        return {
            "limit": limit,
            "consumed": consumed,
            "remaining": remaining,
            "pressure": pressure,
        }

    @staticmethod
    def _initial_main_workflow_convergence(
        *,
        automation: dict[str, Any],
        budget: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "state": "active",
            "reason": "within_budget",
            "recommendedAction": "continue",
            "resumable": True,
            "requiresUserDecision": False,
            "budgetPressure": budget.get("pressure") or "normal",
            "automationLevel": automation.get("level"),
            "availableActions": ["pause", "cancel", "supplement"],
            "source": "runtime",
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

    def _main_workflow_workspace_snapshot(self, session: dict[str, Any], *, include_git: bool = False) -> dict[str, Any]:
        workspace_root = self._session_workspace_root(session)
        snapshot: dict[str, Any] = {
            "workspaceId": session.get("workspaceId"),
            "workspaceRoot": workspace_root,
            "exists": bool(workspace_root and Path(workspace_root).exists()),
            "git": {"isRepo": False},
        }
        if not include_git or not workspace_root or not Path(workspace_root).exists():
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
        if not self._workspace_can_create_task_worktree(workspace_root):
            return None
        task_id = str(task["id"])
        branch_prefix = self._safe_worktree_segment(str(worktree_config.get("branchPrefix") or "agent"))
        branch_name = f"{branch_prefix}/{self._safe_worktree_segment(task_id)}"
        worktree_path = self._worktree_path_for_task(workspace_root, task_id, worktree_config)
        base_ref = str(routing.get("baseRef") or worktree_config.get("baseRef") or "HEAD")

        try:
            result = worktree_service.create_for_task({
                "workspaceId": session["workspaceId"],
                "sessionId": session["id"],
                "taskId": task_id,
                "baseRef": base_ref,
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
        if routing.get("disableWorktreeBinding") is True:
            return False
        return bool(routing.get("worktreeBindingRequired") is True)

    def _workspace_can_create_task_worktree(self, workspace_root: Path) -> bool:
        git = self._git_workspace_status(workspace_root)
        if git.get("isRepo") is not True:
            return False
        try:
            top = subprocess_run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=workspace_root,
                stdout=PIPE,
                stderr=DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
        except (SubprocessError, OSError):
            return False
        if top.returncode != 0:
            return False
        workspace_git_root = Path(top.stdout.strip() or workspace_root).resolve()
        if workspace_git_root != workspace_root.resolve():
            return False
        worktree_service = getattr(self, "_worktree_service", None)
        git_adapter = getattr(worktree_service, "_git", None)
        configured_root = getattr(git_adapter, "_repo_root", None)
        if not isinstance(configured_root, str) or not configured_root.strip():
            return True
        try:
            configured = Path(configured_root).resolve()
        except (OSError, RuntimeError):
            return False
        return workspace_git_root == configured

    def _return_worktree_binding_failed_task(
        self,
        *,
        session: dict[str, Any],
        task: dict[str, Any],
        goal: str,
        routing_dict: dict[str, Any],
        user_msg: dict[str, Any] | None = None,
        assistant_msg: dict[str, Any] | None = None,
        accepted_mode: str = "new",
        summary: str = "Write-oriented task requires an active worktree, but worktree binding failed.",
        latency_ms: int = 0,
    ) -> dict[str, Any]:
        if user_msg is not None:
            self._publish(session["id"], task, "message.created", {"message": user_msg})
        if assistant_msg is not None:
            self._publish(session["id"], task, "message.created", {"message": assistant_msg})
        self._publish(
            session["id"],
            task,
            "task.created",
            {"status": task.get("status"), "goal": goal},
        )
        self._publish(
            session["id"],
            task,
            "runtime.context.prepared",
            {**routing_dict, "latency_ms": latency_ms},
            visibility="trace",
        )
        failed = self._fail_task(
            session_id=session["id"],
            task=task,
            summary=summary,
            error_code="WORKTREE_BINDING_FAILED",
            structured_result={
                "failureKind": "worktree_binding_failed",
                "worktreeBindingRequired": True,
            },
        )
        result: dict[str, Any] = {
            "task": failed,
            "acceptedMode": accepted_mode,
        }
        if user_msg is not None:
            result["userMessage"] = user_msg
        if assistant_msg is not None:
            result["assistantMessage"] = assistant_msg
        return result

    @staticmethod
    def _session_launch_metadata(session: dict[str, Any]) -> dict[str, Any]:
        launch = session.get("launch")
        if isinstance(launch, dict):
            return dict(launch)
        metadata = session.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("launch"), dict):
            return dict(metadata["launch"])
        return {}

    def _apply_session_launch_to_routing(self, routing: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
        launch = self._session_launch_metadata(session)
        repository = launch.get("repository")
        if not isinstance(repository, dict):
            return routing
        updated = dict(routing)
        updated["repository"] = dict(repository)
        branch = repository.get("branch")
        if isinstance(branch, str) and branch.strip():
            updated["baseRef"] = branch.strip()
        if repository.get("worktree") is False:
            updated["worktreeBindingRequired"] = False
            updated["disableWorktreeBinding"] = True
        elif repository.get("worktree") is True:
            updated["preferredWorktree"] = True
        return updated

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
        requested_skill_id = self._requested_skill_id(params)
        message_metadata = self._message_reference_metadata(params)

        explicit_supplement = params.get("mode") == "supplement"
        explicit_task_id = params.get("taskId") or params.get("task_id")
        internal_response = params.get("internalResponse")
        is_internal_response = (
            isinstance(internal_response, dict)
            and internal_response.get("kind") == "ask_user_question"
        )
        is_queued_mode = params.get("mode") == "queued"
        should_auto_supplement = not is_queued_mode and params.get("background") is not True and params.get("newTask") is not True
        if is_internal_response:
            active_task = self._find_supplement_target_task(
                session_id=session["id"],
                task_id=str(explicit_task_id) if explicit_task_id else None,
                strict=False,
            )
            if active_task is None:
                if explicit_task_id:
                    try:
                        target_task = self._store.get_task({"taskId": str(explicit_task_id)})["task"]
                    except Exception as exc:  # noqa: BLE001
                        raise ValueError(f"Cannot answer missing ask_user_question task: {explicit_task_id}") from exc
                    if target_task.get("sessionId") == session["id"]:
                        return {
                            "task": target_task,
                            "acceptedMode": "supplement",
                            "duplicate": True,
                        }
                raise ValueError("Cannot answer ask_user_question without an active target task.")
            return self._attach_supplemental_message(
                session_id=session["id"],
                task=active_task,
                content=goal,
                metadata=message_metadata,
                internal_response=internal_response,
            )
        if explicit_supplement or should_auto_supplement:
            active_task = self._find_supplement_target_task(
                session_id=session["id"],
                task_id=str(explicit_task_id) if explicit_task_id else None,
                strict=explicit_supplement,
            )
            if active_task is not None:
                return self._attach_supplemental_message(
                    session_id=session["id"],
                    task=active_task,
                    content=goal,
                    metadata=message_metadata,
                    internal_response=params.get("internalResponse"),
                )

        # --- Queued mode: create task but don't execute if another is running ---
        if params.get("mode") == "queued":
            active_task = self._find_open_session_task(session["id"])
            if active_task is not None:
                routing_dict = self._model_first_routing_context(
                    goal=goal,
                    session=session,
                    params=params,
                    requested_skill_id=requested_skill_id,
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
                    metadata=message_metadata,
                )
                self._publish(session["id"], queued_task, "message.created", {"message": user_msg})
                self._publish(session["id"], queued_task, "task.created", {"status": "queued", "goal": goal})
                self._publish(session["id"], queued_task, "task.queued", {"status": "queued", "goal": goal})
                self._publish(
                    session["id"],
                    queued_task,
                    "runtime.context.prepared",
                    {**routing_dict, "latency_ms": 0},
                    visibility="trace",
                )
                return {"task": queued_task, "userMessage": user_msg, "acceptedMode": "queued"}

        _route_latency_ms = 0
        routing_dict = self._model_first_routing_context(
            goal=goal,
            session=session,
            params=params,
            requested_skill_id=requested_skill_id,
        )

        if params.get("background") is True:
            # Pass routing info to the background worker without blocking on
            # context build — the worker will build context with routing data.
            task = self._store.create_task(
                session_id=session["id"],
                task_type="edit",
                goal=goal,
                plan=[],
                acceptance_criteria=self._default_acceptance_criteria(goal),
                out_of_scope=self._default_out_of_scope(),
                routing=routing_dict,
            )
            runtime_task = {**task, "plan": []}
            user_msg = self._store.create_message(
                session_id=session["id"],
                task_id=runtime_task["id"],
                role="user",
                content=goal,
                client_message_id=client_message_id,
                kind="normal",
                status="completed",
                metadata=message_metadata,
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
            elif routing_dict.get("worktreeBindingRequired") is True and routing_dict.get("preferredWorktree") is not True:
                return self._return_worktree_binding_failed_task(
                    session=session,
                    task=runtime_task,
                    goal=goal,
                    routing_dict=routing_dict,
                    user_msg=user_msg,
                    assistant_msg=assistant_msg,
                    accepted_mode="new",
                    summary="Write-oriented background task requires an active worktree, but worktree binding failed.",
                    latency_ms=_route_latency_ms,
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
                event_type="runtime.context.prepared",
                payload={**routing_dict, "latency_ms": _route_latency_ms},
                visibility="trace",
            )
            self._start_background_message(
                session_id=session["id"],
                task=runtime_task,
                goal=goal,
                context=None,
                routing=routing_dict,
                skill_id=requested_skill_id,
            )
            self._record_skill_usage(
                task_id=runtime_task["id"],
                session_id=session["id"],
                skill_id=requested_skill_id,
            )
            return {"task": runtime_task, "userMessage": user_msg, "assistantMessage": assistant_msg, "acceptedMode": "new"}

        context = self._context_builder.build(
            session_id=session["id"],
            goal=goal,
            skill_id=requested_skill_id,
            lightweight=True,
            current_message_metadata=message_metadata,
        )
        if isinstance(context.get("skillFallback"), dict):
            routing_dict["skillFallback"] = context["skillFallback"]
        routing_dict["profile_snapshot"] = self._runtime_profile_snapshot(context)
        # Attach runtime metadata to context as a plain dict for JSON safety.
        context["routing"] = routing_dict
        # Emit tool filter event if skill filtering was applied
        self._maybe_publish_tool_filter(context, requested_skill_id)
        logger.info(
            "Prepared model-first runtime context: max_steps=%d skill=%s background=%s",
            routing_dict.get("max_steps", 0), requested_skill_id, routing_dict.get("background") is True,
        )

        task = self._store.create_task(
            session_id=session["id"],
            task_type="edit",
            goal=goal,
            plan=[],
            acceptance_criteria=self._default_acceptance_criteria(goal),
            out_of_scope=self._default_out_of_scope(),
            routing=routing_dict,
        )
        runtime_task = {**task, "plan": []}
        user_msg = self._store.create_message(
            session_id=session["id"],
            task_id=runtime_task["id"],
            role="user",
            content=goal,
            client_message_id=client_message_id,
            kind="normal",
            status="completed",
            metadata=message_metadata,
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
        elif routing_dict.get("worktreeBindingRequired") is True and routing_dict.get("preferredWorktree") is not True:
            return self._return_worktree_binding_failed_task(
                session=session,
                task=runtime_task,
                goal=goal,
                routing_dict=routing_dict,
                user_msg=user_msg,
                assistant_msg=assistant_msg,
                accepted_mode="new",
                latency_ms=_route_latency_ms,
            )

        self._record_skill_usage(
            task_id=runtime_task["id"],
            session_id=session["id"],
            skill_id=requested_skill_id,
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
            },
            visibility="panel",
        )
        self._fire_hooks("before_task_start", session["id"], runtime_task, extra_context={"routing": context.get("routing")})
        self._publish(
            session_id=session["id"],
            task=runtime_task,
            event_type="runtime.context.prepared",
            payload={**routing_dict, "latency_ms": _route_latency_ms},
            visibility="trace",
        )
        result = self._execute_message_task(
            session_id=session["id"],
            task=runtime_task,
            goal=goal,
            context=context,
        )
        result.setdefault("acceptedMode", "new")
        return result

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

    @staticmethod
    def _requested_skill_id(params: dict[str, Any]) -> str | None:
        for key in ("skillId", "skill_id"):
            value = params.get(key)
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    return stripped
        return None

    @staticmethod
    def _clean_message_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            text = item.strip().replace("\\", "/")
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        return cleaned

    @staticmethod
    def _inline_file_references(content: Any) -> list[str]:
        text = str(content or "")
        references: list[str] = []
        seen: set[str] = set()
        for match in _INLINE_FILE_REFERENCE_PATTERN.finditer(text):
            reference = (match.group(2) or "").strip().strip("\"'`")
            for terminator in _INLINE_FILE_REFERENCE_TERMINATORS:
                if terminator in reference:
                    reference = reference.split(terminator, 1)[0]
            reference = reference.rstrip(_INLINE_FILE_REFERENCE_TRAILING).replace("\\", "/").strip()
            if not reference or reference in seen:
                continue
            seen.add(reference)
            references.append(reference)
        return references

    def _message_reference_metadata(self, params: dict[str, Any]) -> dict[str, Any]:
        attachments = self._clean_message_string_list(params.get("attachments"))
        file_references = self._clean_message_string_list(params.get("fileReferences"))
        for reference in self._inline_file_references(params.get("content")):
            if reference not in file_references:
                file_references.append(reference)

        metadata: dict[str, Any] = {}
        if attachments:
            metadata["attachments"] = attachments
        if file_references:
            metadata["fileReferences"] = file_references
        internal_response = params.get("internalResponse")
        if isinstance(internal_response, dict):
            metadata["internalResponse"] = {
                key: value
                for key, value in internal_response.items()
                if isinstance(key, str) and value not in (None, "", [])
            }
        return metadata
