from __future__ import annotations

import json
import logging
from typing import Any

from ..provider.failure_recovery import classify_provider_failure

logger = logging.getLogger(__name__)

from ..context.builder import ContextBuilder
from ..context.compactor import ContextCompactor
from ..context.scratchpad import Scratchpad
from ..event_bus import EventBus
from ..memory import MemoryManager
from ..planner.service import Planner
from ..provider.adapter import ProviderAdapter
from ..provider.cache import LLMCache
from ..services.collaboration_service import CollaborationService
from ..services.session_service import SessionService
from ..services.subagent_service import SubagentService
from ..services.worker_runner import WorkerRunner
from ..router import MetaRouter, RoutingDecision
from ..policy.decision_advisor import DecisionAdvisor
from ..skills.registry import SkillRegistry
from ..mcp.client import McpClientManager
from ..reflection.evaluator import ReflectionEvaluator
from ..reflection.types import ReflectionConfig
from ..planner.decomposer import TaskDecomposer
from ..planner.dag_executor import DAGExecutor
from ..planner.coverage import CoverageEvaluator
from ..orchestration import SupervisorOrchestrator, SwarmOrchestrator
from ..services.hook_service import HookService
from ..store.sqlite_store import SQLiteStore
from ..tools.registry import BUILTIN_TOOL_SCHEMAS
from ..observability.tracer import Tracer
from ..execution.tool_pipeline import ToolExecutionMixin
from ..execution.tool_recovery import ToolRecoveryMixin
from ..state.task_state_machine import TaskStateMachine
from .approval_flow import ApprovalFlowMixin
from .child_task import ChildTaskMixin
from .config_flow import ConfigFlowMixin
from .memory_flow import MemoryFlowMixin
from .mcp_flow import McpFlowMixin
from .message_routing import MessageRoutingMixin
from .message_execution import MessageExecutionMixin
from .publishing import PublishingMixin
from .supplement_flow import SupplementFlowMixin
from .agent_profile_flow import AgentProfileFlowMixin
from .skill_flow import SkillFlowMixin
from .provider_turn import ProviderTurnMixin
from .react_runner import ReactRunnerMixin
from .react_resume import ReactResumeMixin
from .react_tool_helpers import ReactToolHelpersMixin
from .resume_flow import ResumeFlowMixin
from .task_lifecycle import TaskLifecycleMixin


class Orchestrator(
    ProviderTurnMixin,
    ReactToolHelpersMixin,
    ReactResumeMixin,
    ReactRunnerMixin,
    TaskLifecycleMixin,
    MemoryFlowMixin,
    ResumeFlowMixin,
    ApprovalFlowMixin,
    ChildTaskMixin,
    ConfigFlowMixin,
    ToolRecoveryMixin,
    ToolExecutionMixin,
    McpFlowMixin,
    MessageExecutionMixin,
    MessageRoutingMixin,
    PublishingMixin,
    SkillFlowMixin,
    AgentProfileFlowMixin,
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
        worktree_service: Any | None = None,
        _skip_orphan_cleanup: bool = False,
    ) -> None:
        self._store = store
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._provider = provider
        self._decision_advisor = decision_advisor
        self._hook_service = hook_service
        self._worktree_service = worktree_service
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
        context_tool_schemas = self._context_tool_schemas()
        self._context_builder = ContextBuilder(
            store,
            tool_schemas=context_tool_schemas,
            tool_schema_provider=None if context_tool_schemas is not None else self._merged_context_tool_schemas,
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

    def _merged_context_tool_schemas(self) -> list[dict[str, Any]]:
        schemas = list(BUILTIN_TOOL_SCHEMAS)
        seen = {schema.get("name") for schema in schemas}
        for schema in self._tool_registry.schemas:
            name = schema.get("name")
            if name and name not in seen:
                schemas.append(schema)
                seen.add(name)
        return schemas

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
            if isinstance(routing_dict.get("toolContinuation"), dict):
                proposal_payload["toolContinuation"] = routing_dict["toolContinuation"]
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
                    "toolContinuation": routing_dict.get("toolContinuation"),
                    "source": advice.source,
                    "confidence": advice.confidence,
                    "rationale": advice.rationale,
                },
            )
        except Exception:  # noqa: BLE001
            logger.debug("Failed to record routing proposal", exc_info=True)

    def _record_failure_recovery_proposal(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        provider_turn_id: str | None,
        failure_recovery: dict[str, Any] | None = None,
        error: BaseException | str | None = None,
        advice: Any | None = None,
    ) -> None:
        try:
            recovery = failure_recovery or classify_provider_failure(error or "").to_dict()
            max_retries = recovery.get("maxRetries")
            if not isinstance(max_retries, int):
                max_retries = 1 if recovery.get("retryable") else 0
            runtime_proposal = {
                "strategy": self._failure_recovery_proposal_strategy(recovery),
                "maxRetries": max_retries,
                "retryable": bool(recovery.get("retryable")),
                "recoverable": bool(recovery.get("recoverable")),
                "category": recovery.get("category"),
                "httpStatus": recovery.get("httpStatus"),
            }

            def _create_and_validate(
                *,
                proposal: dict[str, Any],
                source: dict[str, Any],
                status: str,
                reasons: list[str],
                model_id: str | None = None,
            ) -> None:
                record = self._store.create_proposal({
                    "kind": "failure_recovery",
                    "sessionId": session_id,
                    "taskId": task["id"],
                    "proposal": proposal,
                    "source": source,
                    "inputSummary": str(error or recovery.get("userMessage") or "")[:500],
                    "modelId": model_id,
                    "turnId": provider_turn_id,
                })
                self._store.validate_proposal({
                    "proposalId": record["proposal"]["id"],
                    "status": status,
                    "reasons": reasons,
                })

            if advice is not None:
                advice_payload = getattr(advice, "payload", None)
                if isinstance(advice_payload, dict) and advice_payload:
                    proposal = dict(advice_payload)
                    proposal.setdefault("strategy", runtime_proposal["strategy"])
                    proposal.update({
                        "category": recovery.get("category"),
                        "httpStatus": recovery.get("httpStatus"),
                        "runtimeStrategy": runtime_proposal["strategy"],
                    })
                    source = {
                        "type": getattr(advice, "source", "llm"),
                        "confidence": getattr(advice, "confidence", None),
                        "rationale": getattr(advice, "rationale", None),
                        "fallbackReason": getattr(advice, "fallback_reason", None),
                        "runtimeClassifier": {
                            "category": recovery.get("category"),
                            "reason": recovery.get("reason"),
                            "userMessage": recovery.get("userMessage"),
                        },
                    }
                    status = "accepted" if bool(getattr(advice, "accepted", False)) else "rejected"
                    reasons = [] if status == "accepted" else (
                        list(getattr(advice, "validation_reasons", None) or [])
                        or [str(getattr(advice, "fallback_reason", None) or "advisor rejected")]
                    )
                    _create_and_validate(
                        proposal=proposal,
                        source=source,
                        status=status,
                        reasons=reasons,
                        model_id=getattr(advice, "model_id", None),
                    )

            _create_and_validate(
                proposal=runtime_proposal,
                source={
                    "type": "runtime_bounded_recovery" if advice is not None else "runtime_classifier",
                    "reason": recovery.get("reason"),
                    "userMessage": recovery.get("userMessage"),
                    "advisorAccepted": bool(getattr(advice, "accepted", False)) if advice is not None else False,
                },
                status="accepted",
                reasons=[],
            )
        except Exception:  # noqa: BLE001
            logger.debug("Failed to record failure recovery proposal", exc_info=True)

    def _record_provider_preflight_proposal(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        provider_turn_id: str | None,
        preflight: dict[str, Any] | None,
        advice: Any | None = None,
    ) -> None:
        try:
            decision = preflight if isinstance(preflight, dict) else {}
            facts = decision.get("facts") if isinstance(decision.get("facts"), dict) else {}
            input_summary = json.dumps({
                "riskLevel": facts.get("riskLevel"),
                "estimatedInputTokens": facts.get("estimatedInputTokens"),
                "estimatedInputTokensAfter": facts.get("estimatedInputTokensAfter"),
                "maxContextTokens": facts.get("maxContextTokens"),
                "nearContextLimit": facts.get("nearContextLimit"),
                "overContextLimit": facts.get("overContextLimit"),
                "hasPriorProviderFailure": facts.get("hasPriorProviderFailure"),
            }, ensure_ascii=False, sort_keys=True)[:500]
            runtime_action = str(decision.get("runtimeAction") or "proceed")
            provider_preflight = (
                decision.get("providerPreflight")
                if isinstance(decision.get("providerPreflight"), dict)
                else {}
            )
            split_plan = decision.get("splitPlan") if isinstance(decision.get("splitPlan"), dict) else None
            if split_plan is None and isinstance(provider_preflight, dict):
                candidate = provider_preflight.get("splitPlan")
                split_plan = candidate if isinstance(candidate, dict) else None
            proposal_action = "propose_split" if runtime_action == "execute_split" else runtime_action
            if advice is None and runtime_action == "proceed" and facts.get("riskLevel") == "low":
                return
            if runtime_action == "compact_context":
                context_strategy = "compact_recent_context"
            elif runtime_action == "execute_split":
                context_strategy = "split_into_bounded_subtasks"
            else:
                context_strategy = "preserve_context"
            runtime_proposal = {
                "action": proposal_action,
                "riskLevel": facts.get("riskLevel") or "low",
                "reason": provider_preflight.get("reason")
                if isinstance(provider_preflight, dict)
                else "Provider preflight runtime decision.",
                "contextStrategy": context_strategy,
                "runtimeAction": runtime_action,
                "runtimeApplied": bool(decision.get("runtimeApplied")),
                "estimatedInputTokens": facts.get("estimatedInputTokens"),
                "estimatedInputTokensAfter": facts.get("estimatedInputTokensAfter"),
                "maxContextTokens": facts.get("maxContextTokens"),
                "compactionThreshold": facts.get("compactionThreshold"),
            }
            if proposal_action == "propose_split" and isinstance(split_plan, dict):
                runtime_proposal["splitRecommendation"] = {
                    "subtasks": list(split_plan.get("subtasks") or []),
                    "dag": split_plan.get("dag"),
                    "executionOrder": split_plan.get("execution_order") or split_plan.get("executionOrder"),
                    "reason": split_plan.get("reason"),
                }

            def _create_and_validate(
                *,
                proposal: dict[str, Any],
                source: dict[str, Any],
                status: str,
                reasons: list[str],
                model_id: str | None = None,
            ) -> None:
                record = self._store.create_proposal({
                    "kind": "provider_preflight",
                    "sessionId": session_id,
                    "taskId": task["id"],
                    "proposal": proposal,
                    "source": source,
                    "inputSummary": input_summary,
                    "modelId": model_id,
                    "turnId": provider_turn_id,
                })
                self._store.validate_proposal({
                    "proposalId": record["proposal"]["id"],
                    "status": status,
                    "reasons": reasons,
                })

            if advice is not None:
                advice_payload = getattr(advice, "payload", None)
                if isinstance(advice_payload, dict) and advice_payload:
                    proposal = dict(advice_payload)
                    proposal.setdefault("action", proposal_action)
                    proposal.setdefault("riskLevel", facts.get("riskLevel") or "low")
                    proposal["runtimeAction"] = runtime_action
                    proposal["runtimeApplied"] = bool(decision.get("runtimeApplied"))
                    source = {
                        "type": getattr(advice, "source", "llm"),
                        "confidence": getattr(advice, "confidence", None),
                        "rationale": getattr(advice, "rationale", None),
                        "fallbackReason": getattr(advice, "fallback_reason", None),
                        "runtimeFacts": {
                            "nearContextLimit": facts.get("nearContextLimit"),
                            "overContextLimit": facts.get("overContextLimit"),
                            "hasPriorProviderFailure": facts.get("hasPriorProviderFailure"),
                        },
                    }
                    status = "accepted" if bool(getattr(advice, "accepted", False)) else "rejected"
                    reasons = [] if status == "accepted" else (
                        list(getattr(advice, "validation_reasons", None) or [])
                        or [str(getattr(advice, "fallback_reason", None) or "advisor rejected")]
                    )
                    _create_and_validate(
                        proposal=proposal,
                        source=source,
                        status=status,
                        reasons=reasons,
                        model_id=getattr(advice, "model_id", None),
                    )

            _create_and_validate(
                proposal=runtime_proposal,
                source={
                    "type": "runtime_provider_preflight",
                    "advisorAccepted": bool(getattr(advice, "accepted", False)) if advice is not None else False,
                    "advisorAction": (
                        getattr(advice, "payload", {}) or {}
                    ).get("action") if advice is not None and isinstance(getattr(advice, "payload", None), dict) else None,
                },
                status="accepted",
                reasons=[],
            )
        except Exception:  # noqa: BLE001
            logger.debug("Failed to record provider preflight proposal", exc_info=True)

    @staticmethod
    def _failure_recovery_proposal_strategy(recovery: dict[str, Any]) -> str:
        strategy = recovery.get("strategy")
        if isinstance(strategy, str) and strategy.strip():
            return strategy.strip()
        action = str(recovery.get("recommendedAction") or "")
        if action.startswith("retry"):
            return "retry"
        if action in {"ask_user_or_change_request", "fix_provider_credentials"}:
            return "ask_user"
        if bool(recovery.get("recoverable")):
            return "fallback"
        return "abort"

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




    
