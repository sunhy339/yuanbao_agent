from __future__ import annotations

import json
import logging
import os
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from ..context.builder import ContextBuilder
from ..context.compactor import ContextCompactor
from ..context.scratchpad import Scratchpad
from ..context.token_budget import estimate_tokens
from ..event_bus import EventBus
from ..memory import MemoryManager
from ..models import RuntimeEvent
from ..planner.service import Planner
from ..policy.guard import PolicyGuard
from ..provider.adapter import ProviderAdapter
from ..provider.cache import LLMCache
from ..services.collaboration_service import CollaborationService
from ..services.command_background import cancel_background_command, cancel_background_commands, get_background_command_service
from ..services.session_service import SessionService
from ..services.subagent_service import SubagentService
from ..services.worker_budget import WorkerBudget, WorkerBudgetExceededError
from ..services.worker_environment import normalize_child_tool_allowlist
from ..services.worker_runner import WorkerRunner
from ..router import MetaRouter, RoutingDecision
from ..policy.decision_advisor import DecisionAdvisor
from ..react.types import ProviderTurnResult, TurnDecision
from ..skills.registry import SkillRegistry
from ..mcp.client import McpClientManager, McpServerConfig, summarize_mcp_exception
from ..reflection.evaluator import ReflectionEvaluator
from ..reflection.types import ReflectionConfig
from ..planner.decomposer import TaskDecomposer
from ..planner.dag_executor import DAGExecutor
from ..planner.coverage import CoverageEvaluator
from ..orchestration import OrchestrationMode, SupervisorOrchestrator, SwarmOrchestrator
from ..services.hook_service import HookService
from ..store.sqlite_store import SQLiteStore
from ..tools import build_builtin_tools
from ..tools.registry import BUILTIN_TOOL_SCHEMAS, ToolRegistry
from ..observability.tracer import Tracer
from ..execution.tool_pipeline import ToolExecutionMixin
from ..state.task_state_machine import TaskStateMachine
from .approval_flow import ApprovalFlowMixin
from .config_flow import ConfigFlowMixin
from .memory_flow import MemoryFlowMixin
from .mcp_flow import McpFlowMixin
from .message_flow import MessageFlowMixin
from .publishing import PublishingMixin
from .supplement_flow import SupplementFlowMixin
from .skill_flow import SkillFlowMixin
from .react_runner import ReactRunnerMixin
from .resume_flow import ResumeFlowMixin
from .task_lifecycle import TaskLifecycleMixin


class Orchestrator(
    ReactRunnerMixin,
    TaskLifecycleMixin,
    MemoryFlowMixin,
    ResumeFlowMixin,
    ApprovalFlowMixin,
    ConfigFlowMixin,
    ToolExecutionMixin,
    McpFlowMixin,
    MessageFlowMixin,
    PublishingMixin,
    SkillFlowMixin,
    SupplementFlowMixin,
):
    """Coordinates the first-pass agent loop for Sprint 1."""

    _VALID_TASK_TRANSITIONS = TaskStateMachine.VALID_TRANSITIONS

    def __init__(
        self,
        store: Any,
        event_bus: EventBus,
        tool_registry: Any,
        provider: Any,
        meta_router: MetaRouter | None = None,
        memory_manager: MemoryManager | None = None,
        *,
        decision_advisor: DecisionAdvisor | None = None,
        hook_service: HookService | None = None,
        _skip_orphan_cleanup: bool = False,
    ) -> None:
        self._store = store
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._provider = provider
        self._decision_advisor = decision_advisor
        self._hook_service = hook_service
        self._task_state_machine = TaskStateMachine()
        self._meta_router = meta_router or MetaRouter(
            provider=provider,
            decision_advisor=decision_advisor,
        )
        self._memory_manager = memory_manager
        self._planner = Planner()
        self._scratchpad = Scratchpad(store)
        self._compactor = ContextCompactor(store, provider=provider)
        self._skill_registry = SkillRegistry(store)
        self._context_builder = ContextBuilder(
            store,
            tool_schemas=self._context_tool_schemas(),
            compactor=self._compactor,
            scratchpad=self._scratchpad,
            skill_registry=self._skill_registry,
        )
        self._session_service = SessionService(store)
        self._collaboration_service = CollaborationService(store, event_bus)
        self._worker_runner = WorkerRunner(self._collaboration_service)
        self._subagent_service = SubagentService(store, self._collaboration_service, runner=self._worker_runner)
        self._mcp_manager = McpClientManager(store)
        self._cache = LLMCache(store)
        if isinstance(provider, ProviderAdapter):
            provider._cache = self._cache
        self._reflector = self._build_reflector(store, provider)
        self._decomposer = TaskDecomposer(provider=provider)
        self._dag_executor = DAGExecutor(subagent_service=self._subagent_service)
        self._coverage_evaluator = CoverageEvaluator()
        self._supervisor = SupervisorOrchestrator(provider=provider, subagent_service=self._subagent_service)
        self._swarm = SwarmOrchestrator(provider=provider, subagent_service=self._subagent_service)
        self._pending_react_tasks: dict[str, dict[str, Any]] = {}
        self._tracer = Tracer(store)
        self._shutting_down = False
        self._streaming_mode_cache: bool | None = None
        if not _skip_orphan_cleanup:
            self._cleanup_orphan_tasks()

    @staticmethod
    def _build_reflector(store: SQLiteStore, provider: ProviderAdapter) -> ReflectionEvaluator | None:
        config = store.get_config({})["config"]
        rc = config.get("reflection", {})
        if not rc.get("enabled"):
            return None
        reflection_config = ReflectionConfig(
            enabled=True,
            max_retries=rc.get("maxRetries", 2),
            confidence_threshold=rc.get("confidenceThreshold", 0.7),
            evaluation_prompt=rc.get("evaluationPrompt", ""),
        )
        return ReflectionEvaluator(provider=provider, config=reflection_config)

    # ── validation helpers ──────────────────────────────────────────

    def _validate_mcp_config(self, params: dict[str, Any]) -> None:
        """Validate MCP server config before create/update. Raises ValueError with fix suggestions."""
        transport = (params.get("transport") or "stdio").lower()

        if transport == "stdio":
            command = params.get("command")
            if not command or not isinstance(command, str) or not command.strip():
                raise ValueError(
                    "stdio transport requires a non-empty 'command'. "
                    "Fix: set command to the executable path, e.g. 'npx' or 'python'."
                )
            args = params.get("args")
            if args is not None:
                if not isinstance(args, list):
                    raise ValueError("'args' must be a string array. Fix: pass args as [\"--port\", \"8080\"].")
                for i, a in enumerate(args):
                    if not isinstance(a, str):
                        raise ValueError(f"'args[{i}]' must be a string, got {type(a).__name__}. Fix: convert to string.")

        elif transport in ("sse", "streamable_http"):
            url = params.get("url")
            if not url or not isinstance(url, str) or not url.strip():
                raise ValueError(
                    f"{transport} transport requires a non-empty 'url'. "
                    "Fix: set url to the server endpoint."
                )

        env = params.get("env")
        if env is not None and not isinstance(env, dict):
            raise ValueError("'env' must be an object with string keys and values.")

        headers = params.get("headers")
        if headers is not None and not isinstance(headers, dict):
            raise ValueError("'headers' must be an object with string keys and values.")

    def _validate_task_transition(self, current_status: str, target_status: str, task_id: str, *, silent: bool = False) -> None:
        """Validate task status transition. Delegates to TaskStateMachine."""
        self._task_state_machine.assert_transition(
            current_status, target_status, task_id, silent=silent,
        )

    def open_workspace(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace = self._store.upsert_workspace(path=params["path"])
        return {"workspace": workspace}

    def create_session(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session_service.create_session(
            workspace_id=params["workspaceId"],
            title=params["title"],
        )
        return {"session": session}


    # ------------------------------------------------------------------

    def _record_routing_proposal(
        self,
        *,
        session_id: str,
        task_id: str,
        goal: str,
        routing: RoutingDecision,
        routing_dict: dict[str, Any],
    ) -> None:
        """Create a proposal record when routing used LLM advisory."""
        advice = self._meta_router.last_advice
        if advice is None:
            return
        try:
            proposal_payload = {
                "scenario": routing.scenario.value,
                "strategy": routing.strategy.value,
                "skill_id": routing.skill_id,
            }
            source = {
                "type": advice.source,
                "confidence": advice.confidence,
                "rationale": advice.rationale,
            }
            if advice.model_id:
                source["model_id"] = advice.model_id
            status = "accepted" if advice.accepted else "rejected"
            record = self._store.create_proposal({
                "kind": "routing_strategy",
                "sessionId": session_id,
                "taskId": task_id,
                "proposal": proposal_payload,
                "source": source,
                "inputSummary": goal[:500],
                "modelId": advice.model_id,
            })
            proposal_id = record["proposal"]["id"]
            if status == "accepted":
                self._store.validate_proposal({
                    "proposalId": proposal_id,
                    "status": "accepted",
                    "reasons": [],
                })
            else:
                self._store.validate_proposal({
                    "proposalId": proposal_id,
                    "status": "rejected",
                    "reasons": advice.validation_reasons or [advice.fallback_reason or "advisor rejected"],
                })
            # Publish decision event
            self._publish(
                session_id=session_id,
                task={"id": task_id},
                event_type="agent.decision.routing_strategy",
                payload={
                    "proposalId": proposal_id,
                    "outcome": status,
                    "scenario": routing.scenario.value,
                    "strategy": routing.strategy.value,
                    "source": advice.source,
                    "confidence": advice.confidence,
                    "rationale": advice.rationale,
                },
            )
        except Exception:  # noqa: BLE001
            logger.debug("Failed to record routing proposal", exc_info=True)

    # ------------------------------------------------------------------
    # MCP Server management
    # ------------------------------------------------------------------




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

    def _find_open_session_task(self, session_id: str) -> dict[str, Any] | None:
        open_statuses = {"running", "planning", "verifying", "waiting_approval", "queued", "paused"}
        try:
            tasks = self._store.list_tasks({"sessionId": session_id}).get("tasks", [])
        except Exception:  # noqa: BLE001
            return None
        for task in tasks:
            if task.get("status") in open_statuses:
                return task
        return None

    def _drain_session_queue(self, session_id: str) -> None:
        """Start the next queued task in the session, if any."""
        # Only check truly active states (not queued)
        _active_statuses = {"running", "planning", "verifying", "waiting_approval", "paused"}
        try:
            tasks = self._store.list_tasks({"sessionId": session_id}).get("tasks", [])
        except Exception:  # noqa: BLE001
            return
        for t in tasks:
            if t.get("status") in _active_statuses:
                return
        queued_tasks = self._store.list_tasks_by_session_and_status(session_id, "queued")
        if not queued_tasks:
            return
        next_task = queued_tasks[0]
        self._validate_task_transition("queued", "running", next_task["id"])
        self._store.update_task_status(task_id=next_task["id"], status="running")
        next_task["status"] = "running"
        assistant_msg = self._store.create_message(
            session_id=session_id,
            task_id=next_task["id"],
            role="assistant",
            content="",
            kind="normal",
            status="streaming",
        )
        self._store.update_task(task_id=next_task["id"], active_assistant_message_id=assistant_msg["id"])
        next_task["activeAssistantMessageId"] = assistant_msg["id"]
        self._publish(session_id, next_task, "message.created", {"message": assistant_msg})
        self._publish(session_id, next_task, "task.started", {"status": "running", "goal": next_task.get("goal")})
        self._start_background_message(
            session_id=session_id,
            task=next_task,
            goal=next_task["goal"],
            context=None,
            routing=next_task.get("routing") or {},
            skill_id=(next_task.get("routing") or {}).get("skill_id"),
        )


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
        child_role = params.get("agentType", "worker")
        context = self._context_builder.build(session_id=session["id"], goal=prompt.strip(), lightweight=False, role=child_role)
        context = self._context_with_worker_budget(context, budget)
        plan = self._planner.plan(prompt.strip(), context=context)
        task = self._store.create_task(
            session_id=session["id"],
            task_type="subagent",
            goal=prompt.strip(),
            plan=plan,
            acceptance_criteria=self._default_acceptance_criteria(prompt.strip()),
            out_of_scope=self._default_out_of_scope(),
            role=child_role,
            routing={
                "childCollaborationTaskId": collaboration_task_id,
                "parentRuntimeTaskId": params.get("parentRuntimeTaskId"),
            } if collaboration_task_id else None,
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
                self._tracer.end_span(span.span_id, status="ok", attributes={"status": "waiting_approval"})
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
                    skip_drain=True,
                )
                self._tracer.end_span(span.span_id, status="ok")
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
                self._tracer.end_span(span.span_id, status="ok", attributes={"status": "waiting_approval"})
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
                skip_drain=True,
            )
            self._tracer.end_span(span.span_id, status="ok")
            return {
                "status": "completed",
                "task": completed_task,
                "summary": completed_task.get("resultSummary") or summary,
                "budget": budget.to_metadata(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Worker task execution failed: %s", exc, exc_info=True)
            self._tracer.end_span(span.span_id, status="error", attributes={"error": str(exc)})
            self._fail_task(
                session_id=session["id"],
                task=runtime_task,
                summary=str(exc),
                error_code=str(getattr(exc, "code", "LOOP_EXECUTION_FAILED")),
                skip_drain=True,
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


    