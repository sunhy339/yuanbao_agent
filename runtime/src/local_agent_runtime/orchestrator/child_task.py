"""Child Task Mixin â extracted from Orchestrator.

Handles child task execution and worker management.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

from ..services.worker_budget import WorkerBudget, WorkerBudgetExceededError
from ..services.worker_environment import normalize_child_tool_allowlist
from ..tools.registry import BUILTIN_TOOL_SCHEMAS


class ChildTaskMixin:
    """Mixin providing child task execution and worker management."""

    _CHILD_RUNTIME_ROLES = frozenset({"planner", "worker", "reviewer", "summarizer"})

    def run_child_task(self, params: dict[str, Any]) -> dict[str, Any]:

        session_id = params.get("sessionId")
        prompt = params.get("prompt")
        collaboration_task_id = params.get("collaborationTaskId") or params.get("collaboration_task_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("sessionId is required")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt is required")
        if collaboration_task_id is not None and not isinstance(collaboration_task_id, str):
            raise ValueError("collaborationTaskId must be a string")

        span = self._tracer.start_span(
            "child_task",
            attributes={"prompt": prompt[:200]},
        )

        session = self._store.require_session(session_id)
        budget = WorkerBudget.from_metadata(params.get("budget"), params)
        agent_type = self._child_agent_type(params.get("agentType"))
        child_role = self._child_runtime_role(agent_type)
        profile = params.get("profile") if isinstance(params.get("profile"), dict) else {}
        planning_prompt = params.get("planningPrompt") if isinstance(params.get("planningPrompt"), str) else None
        skill_id = params.get("skillId") if isinstance(params.get("skillId"), str) else params.get("skill_id")
        skill_id = skill_id.strip() if isinstance(skill_id, str) and skill_id.strip() else None
        mcp_policy = params.get("mcpPolicy") if isinstance(params.get("mcpPolicy"), dict) else params.get("mcp_policy")
        mcp_policy = dict(mcp_policy) if isinstance(mcp_policy, dict) else None
        active_worktree = params.get("activeWorktree") if isinstance(params.get("activeWorktree"), dict) else params.get("active_worktree")
        active_worktree = dict(active_worktree) if isinstance(active_worktree, dict) else None
        owned_scope = profile.get("ownedScope")
        if isinstance(owned_scope, str):
            normalized_owned_scope = [owned_scope]
        elif isinstance(owned_scope, list):
            normalized_owned_scope = [str(item).strip() for item in owned_scope if str(item).strip()]
        else:
            normalized_owned_scope = []
        child_goal = prompt.strip()
        clean_child_context = self._child_uses_clean_context(profile=profile)
        context = self._context_builder.build(
            session_id=session["id"],
            goal=child_goal,
            lightweight=False,
            skill_id=skill_id,
            role=child_role,
            include_history=not clean_child_context,
            include_scratchpad=not clean_child_context,
        )
        context["agentType"] = agent_type
        context["runtimeRole"] = child_role
        context["_child_worker"] = True
        context["_child_clean_context"] = clean_child_context
        context["_worker_budget"] = params.get("budget") if isinstance(params.get("budget"), dict) else {}
        preferred_cwd = self._child_preferred_cwd(profile)
        if preferred_cwd:
            context["cwd"] = preferred_cwd
            context["preferredCwd"] = preferred_cwd
        if self._child_plan_mode_required(profile):
            context["_plan_mode"] = True
            context["_plan_mode_reason"] = "child agent profile requires plan mode before execution"
        if profile:
            context["_child_profile"] = profile
        if mcp_policy is not None:
            context["mcpPolicy"] = mcp_policy
        if active_worktree is not None:
            child_routing_seed = dict(context.get("routing") if isinstance(context.get("routing"), dict) else {})
            child_routing_seed["activeWorktree"] = active_worktree
            context["routing"] = child_routing_seed
            context = self._context_with_worktree_binding(context, active_worktree)
        child_allowlist = self._child_tool_allowlist_from_params(params)
        if child_allowlist is not None:
            context["_child_tool_allowlist"] = list(child_allowlist)
        context = self._context_with_child_runtime_hints(
            context,
            child_allowlist=child_allowlist,
            preferred_cwd=preferred_cwd,
        )
        context = self._context_with_worker_budget(context, budget)
        plan_goal = self._child_plan_goal(
            prompt=child_goal,
            planning_prompt=planning_prompt,
            profile=profile,
        )
        context["_childPlanGoal"] = plan_goal
        plan = self._planner.plan(plan_goal, context=context)
        child_can_write = bool(child_allowlist is not None and set(child_allowlist) & {"write_file", "apply_patch", "run_command"})
        role_snapshot = {
            "runtimeRole": child_role,
            "agentType": agent_type,
            "profileId": None,
            "profileVersion": 1,
            "agentProfile": {
                "agentType": agent_type,
                "baseRuntimeRole": child_role,
                "toolPolicy": "child_allowlist" if child_allowlist is not None else "read_only",
                "capabilities": ["workspace_write"] if child_can_write else [],
                "scopes": list(normalized_owned_scope),
                "riskLevel": "medium" if child_can_write else "low",
                "source": "planner_profile" if profile else "runtime_default",
                "version": 1,
                "expectedArtifacts": profile.get("expectedArtifacts") if isinstance(profile.get("expectedArtifacts"), list) else [],
                "verificationRequirements": profile.get("verificationRequirements") if isinstance(profile.get("verificationRequirements"), list) else [],
            },
            "toolPolicy": "child_allowlist" if child_allowlist is not None else "read_only",
            "scopes": list(normalized_owned_scope),
            "riskLevel": "medium" if child_can_write else "low",
            "budget": params.get("budget") if isinstance(params.get("budget"), dict) else {},
        }
        child_routing = {
            "parentRuntimeTaskId": params.get("parentRuntimeTaskId"),
            "agentType": agent_type,
            "runtimeRole": child_role,
            "roleSnapshot": role_snapshot,
        }
        if skill_id is not None:
            child_routing["skill_id"] = skill_id
        if active_worktree is not None:
            child_routing["activeWorktree"] = active_worktree
        if profile:
            child_routing["profile"] = profile
        if collaboration_task_id:
            child_routing["childCollaborationTaskId"] = collaboration_task_id
        task = self._store.create_task(
            session_id=session["id"],
            task_type="subagent",
            goal=child_goal,
            plan=plan,
            acceptance_criteria=self._default_acceptance_criteria(child_goal),
            out_of_scope=self._default_out_of_scope(),
            role=child_role,
            routing=child_routing,
        )
        runtime_task = {**task, "plan": plan}
        if active_worktree is None and child_can_write:
            worktree_routing = {
                **child_routing,
                "worktreeBindingRequired": True,
            }
            worktree = self._maybe_bind_task_worktree(
                session=session,
                task=runtime_task,
                routing=worktree_routing,
            )
            if worktree is not None:
                active_worktree = worktree
                child_routing = {**child_routing, "activeWorktree": worktree}
                runtime_task = self._persist_task_routing(runtime_task, child_routing)
                child_routing_seed = dict(context.get("routing") if isinstance(context.get("routing"), dict) else {})
                child_routing_seed["activeWorktree"] = worktree
                context["routing"] = child_routing_seed
                context = self._context_with_worktree_binding(context, worktree)
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

        _finalised = False
        try:
            react_result = self._run_react_loop(
                session_id=session["id"],
                task=runtime_task,
                goal=child_goal,
                context=context,
                budget=budget,
            )
            if react_result["status"] == "waiting_approval":
                _finalised = True
                self._tracer.end_span(span.span_id, status="ok", attributes={"status": "waiting_approval"})
                return self._waiting_child_task_response(
                    task=runtime_task,
                    summary="Child worker is waiting for parent approval.",
                    budget=budget,
                )
            if react_result["status"] == "completed":
                _finalised = True
                completed_task = self._complete_task(
                    session_id=session["id"],
                    task=runtime_task,
                    summary=react_result["summary"],
                    context=context,
                    tool_results=react_result.get("tool_results", []),
                    skip_drain=True,
                )
                final_status = str(completed_task.get("status") or "completed")
                self._tracer.end_span(
                    span.span_id,
                    status="ok" if final_status == "completed" else "error",
                    attributes={"status": final_status},
                )
                return {
                    "status": final_status,
                    "task": completed_task,
                    "summary": completed_task.get("resultSummary") or react_result["summary"],
                    "budget": budget.to_metadata(),
                }

            _finalised = True
            failed_task = self._fail_task(
                session_id=session["id"],
                task=runtime_task,
                summary=f"Unexpected child ReAct status: {react_result.get('status')}",
                error_code="CHILD_REACT_UNEXPECTED_STATUS",
                structured_result={"reactResult": react_result},
            )
            final_status = str(failed_task.get("status") or "failed")
            self._tracer.end_span(
                span.span_id,
                status="ok" if final_status == "completed" else "error",
                attributes={"status": final_status},
            )
            return {
                "status": final_status,
                "task": failed_task,
                "summary": failed_task.get("resultSummary") or "Child ReAct loop did not produce a terminal result.",
                "budget": budget.to_metadata(),
            }
        except BaseException as exc:
            logger.error("Worker task execution failed: %s", exc, exc_info=True)
            self._tracer.end_span(span.span_id, status="error", attributes={"error": str(exc)})
            if not _finalised:
                try:
                    self._fail_task(
                        session_id=session["id"],
                        task=runtime_task,
                        summary=str(exc),
                        error_code=str(getattr(exc, "code", "LOOP_EXECUTION_FAILED")),
                        skip_drain=True,
                    )
                except Exception:
                    logger.exception("Secondary failure while marking child task as failed")
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

    def _child_agent_type(self, value: Any) -> str:
        if not isinstance(value, str):
            return "worker"
        stripped = value.strip()
        return stripped or "worker"

    def _child_runtime_role(self, agent_type: str) -> str:
        normalized = agent_type.strip().lower()
        if normalized in self._CHILD_RUNTIME_ROLES:
            return normalized
        return "worker"

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
        def _allowed(schema: dict[str, Any]) -> bool:
            name = str(schema.get("name") or "")
            if not name:
                return False
            if name in allowed_set:
                return True
            return name.startswith("mcp__") and "mcp__*" in allowed_set

        schemas = list(BUILTIN_TOOL_SCHEMAS)
        registry = getattr(self, "_tool_registry", None)
        if registry is not None:
            for schema in getattr(registry, "schemas", []):
                name = schema.get("name")
                if name and not any(existing.get("name") == name for existing in schemas):
                    schemas.append(schema)
        return [schema for schema in schemas if _allowed(schema)]

    def _child_tool_allowlist(self) -> list[str] | None:
        raw = os.environ.get("LOCAL_AGENT_CHILD_TOOL_ALLOWLIST")
        if raw is None:
            return None
        return normalize_child_tool_allowlist(raw)

    def _child_tool_allowlist_from_params(self, params: dict[str, Any]) -> tuple[str, ...] | None:
        budget = params.get("budget") if isinstance(params.get("budget"), dict) else {}
        raw = (
            params.get("childToolAllowlist")
            or params.get("child_tool_allowlist")
            or budget.get("childToolAllowlist")
            or budget.get("child_tool_allowlist")
            or budget.get("toolAllowlist")
            or budget.get("tool_allowlist")
        )
        if raw is None:
            return None
        return normalize_child_tool_allowlist(raw)

    def _context_with_child_runtime_hints(
        self,
        context: dict[str, Any],
        *,
        child_allowlist: tuple[str, ...] | None,
        preferred_cwd: str | None = None,
    ) -> dict[str, Any]:
        run_command_allowed = child_allowlist is not None and "run_command" in set(child_allowlist)
        if not run_command_allowed and not preferred_cwd:
            return context
        workspace_root = str(context.get("workspace_root") or context.get("workspaceRoot") or "").strip()
        python_executable = sys.executable
        pytest_command = self._recommended_python_module_command(python_executable, "pytest", "-q")
        hints = {
            "pythonExecutable": python_executable,
            "recommendedPytestCommand": pytest_command,
            "workspaceRoot": workspace_root,
            "runCommandCwd": "narrowest relevant directory for the command",
            "runCommandAllowed": run_command_allowed,
            "avoidCommands": ["python", "python3", "py", "cd ... && ..."],
        }
        if preferred_cwd:
            hints["preferredCwd"] = preferred_cwd
            hints["runCommandCwd"] = preferred_cwd
        updated = dict(context)
        updated["childRuntimeHints"] = hints
        messages = list(updated.get("messages") or [])
        hint_message = {
            "role": "system",
            "content": self._child_runtime_hint_text(hints),
        }
        if messages and messages[-1].get("role") == "user":
            messages.insert(-1, hint_message)
        else:
            messages.append(hint_message)
        updated["messages"] = messages
        return updated

    def _recommended_python_module_command(self, python_executable: str, module: str, *args: str) -> str:
        executable = str(python_executable).strip() or "python"
        quoted = f'"{executable}"' if os.name == "nt" or any(ch.isspace() for ch in executable) else executable
        return " ".join([quoted, "-m", module, *args])

    def _child_runtime_hint_text(self, hints: dict[str, Any]) -> str:
        workspace_root = str(hints.get("workspaceRoot") or "")
        preferred_cwd = str(hints.get("preferredCwd") or "")
        run_command_allowed = hints.get("runCommandAllowed") is True
        lines = [
            "[Child runtime hints]",
        ]
        if preferred_cwd:
            lines.append(f"- Preferred child cwd: {preferred_cwd}")
        if run_command_allowed:
            lines.extend([
                f"- Python executable: {hints['pythonExecutable']}",
                f"- Preferred pytest command: {hints['recommendedPytestCommand']}",
                "- When running tests, call run_command with this command first; do not probe python, python3, or py.",
                "- Set run_command cwd to the narrowest relevant directory for the command and pass workspaceRoot instead of using shell cd.",
            ])
            if workspace_root:
                lines.append(f"- run_command workspaceRoot: {workspace_root}")
        return "\n".join(lines)

    def _child_uses_clean_context(self, *, profile: dict[str, Any]) -> bool:
        if profile.get("inheritParentContext") is False:
            return True
        if profile.get("inheritParentContext") is True:
            return False
        return True

    @staticmethod
    def _child_preferred_cwd(profile: dict[str, Any]) -> str | None:
        value = profile.get("cwd") or profile.get("workingDirectory") or profile.get("working_directory")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _child_plan_mode_required(profile: dict[str, Any]) -> bool:
        return profile.get("planModeRequired") is True or profile.get("plan_mode_required") is True

    def _child_plan_goal(
        self,
        *,
        prompt: str,
        planning_prompt: str | None,
        profile: dict[str, Any],
    ) -> str:
        candidate = str(planning_prompt or "").strip()
        if candidate:
            return candidate
        lines = [prompt.strip()]
        owned_scope = profile.get("ownedScope")
        if isinstance(owned_scope, list) and owned_scope:
            normalized_scope = [str(item).strip() for item in owned_scope if str(item).strip()]
            if normalized_scope:
                lines.append(f"Owned scope: {', '.join(normalized_scope)}")
        expected_artifacts = profile.get("expectedArtifacts")
        if isinstance(expected_artifacts, list) and expected_artifacts:
            lines.append(f"Expected artifacts: {expected_artifacts}")
        verification_requirements = profile.get("verificationRequirements")
        if isinstance(verification_requirements, list) and verification_requirements:
            lines.append(f"Verification requirements: {verification_requirements}")
        return "\n".join(line for line in lines if line.strip())
