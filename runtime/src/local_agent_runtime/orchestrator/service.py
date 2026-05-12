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
from .memory_flow import MemoryFlowMixin
from .mcp_flow import McpFlowMixin
from .message_flow import MessageFlowMixin
from .react_runner import ReactRunnerMixin
from .resume_flow import ResumeFlowMixin
from .task_lifecycle import TaskLifecycleMixin


class Orchestrator(
    ReactRunnerMixin,
    TaskLifecycleMixin,
    MemoryFlowMixin,
    ResumeFlowMixin,
    ApprovalFlowMixin,
    ToolExecutionMixin,
    McpFlowMixin,
    MessageFlowMixin,
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

    def _redact_provider_secret(self, text: str, env_var_name: Any, direct_key: Any = None) -> str:
        redacted = text
        if direct_key:
            redacted = redacted.replace(str(direct_key), "[redacted]")
        if env_var_name:
            env_value = os.environ.get(str(env_var_name))
            if env_value:
                redacted = redacted.replace(env_value, "[redacted]")
        return redacted

    def config_effective(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Return the fully resolved runtime configuration."""
        config = self._store.get_config({})["config"]
        provider_profile = self._active_config_profile(config, "provider")
        autonomy_profile = self._active_config_profile(config, "autonomy")
        agent_soul_profile = self._active_config_profile(config, "agentSoul")

        provider_config = config.get("provider") if isinstance(config, dict) else {}
        streaming_mode = (
            str((provider_config or {}).get("mode") or "").strip().lower()
            in {"openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"}
            if hasattr(self._provider, "stream") else False
        )

        return {
            "providerProfile": provider_profile or {},
            "autonomyProfile": autonomy_profile,
            "agentSoulProfile": agent_soul_profile,
            "contextBudget": {
                "maxContextTokens": self._context_builder._max_context_tokens(config),
            },
            "toolPolicy": (config.get("policy") or {}).get("toolPolicy", "strict_whitelist"),
            "memoryPolicy": (config.get("memory") or {}).get("policy", {}),
            "searchMode": config.get("search", {}).get("mode", "hybrid"),
            "streamingEnabled": streaming_mode,
            "activeProfileIds": {
                "provider": (provider_config or {}).get("activeProfileId"),
                "autonomy": (config.get("autonomy") or {}).get("activeProfileId"),
                "agentSoul": (config.get("agentSoul") or {}).get("activeProfileId"),
            },
        }

    def prompt_preview(self, params: dict[str, Any]) -> dict[str, Any]:
        """Preview effective prompt layers without executing a task."""
        config = self._store.get_config({})["config"]
        # Resolve workspace_root: sessionId → workspace, or direct workspaceRoot param
        session_id = params.get("sessionId")
        if session_id:
            session = self._store.require_session(session_id)
            workspace = self._store.require_workspace(session["workspaceId"])
            workspace_root = workspace.get("rootPath", "")
        else:
            workspace_root = params.get("workspaceRoot", "/unknown")
        role = params.get("role")
        skill_id = params.get("skillId")
        skill_preset = self._context_builder._resolve_skill(skill_id) if skill_id else None

        system_text, prompt_layers = self._context_builder._compose_system_prompt(
            workspace_root=workspace_root,
            config=config,
            role=role,
            skill_preset=skill_preset,
        )

        # Mark runtime-owned layers as locked
        for layer in prompt_layers:
            if layer.get("name") == "runtime_safety":
                layer["locked"] = True
                layer["editable"] = False

        return {
            "systemPrompt": system_text,
            "layers": prompt_layers,
            "totalTokenEstimate": sum(l.get("tokenEstimate", 0) for l in prompt_layers),
        }

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

    # ------------------------------------------------------------------
    # Skill presets
    # ------------------------------------------------------------------

    def skill_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.list_skills(params)

    def skill_create(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.create_skill(params)

    def skill_update(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.update_skill(params)

    def skill_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.delete_skill(params)

    def skill_usage(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.list_skill_usage(params)

    def skill_import(self, params: dict[str, Any]) -> dict[str, Any]:
        """Import skills from a JSON file, folder, or .zip archive."""
        import json
        import zipfile
        import tempfile
        from pathlib import Path

        source = params.get("filePath", "")
        if not source:
            return {"imported": [], "skipped": [], "errors": [{"name": "", "error": "filePath is required"}]}

        source_path = Path(source)
        if not source_path.exists():
            return {"imported": [], "skipped": [], "errors": [{"name": "", "error": f"Path not found: {source}"}]}

        # Collect all .json files to import
        json_files: list[Path] = []

        if source_path.is_file():
            if source_path.suffix.lower() == ".zip":
                # Extract zip to temp dir and scan for .json
                try:
                    with zipfile.ZipFile(source_path, "r") as zf:
                        json_names = [n for n in zf.namelist() if n.lower().endswith(".json") and not n.startswith("__MACOSX")]
                        if not json_names:
                            return {"imported": [], "skipped": [], "errors": [{"name": "", "error": "ZIP 中未找到 .json 文件"}]}
                        tmp_dir = tempfile.mkdtemp(prefix="skill_import_")
                        zf.extractall(tmp_dir, members=json_names)
                        for name in json_names:
                            extracted = Path(tmp_dir) / name
                            if extracted.is_file():
                                json_files.append(extracted)
                except zipfile.BadZipFile as exc:
                    return {"imported": [], "skipped": [], "errors": [{"name": "", "error": f"无效的 ZIP 文件: {exc}"}]}
                except OSError as exc:
                    return {"imported": [], "skipped": [], "errors": [{"name": "", "error": str(exc)}]}
            else:
                # Single JSON file
                json_files.append(source_path)
        elif source_path.is_dir():
            # Recursively find .json files
            json_files = sorted(source_path.rglob("*.json"))
            if not json_files:
                return {"imported": [], "skipped": [], "errors": [{"name": "", "error": "文件夹中未找到 .json 文件"}]}
        else:
            return {"imported": [], "skipped": [], "errors": [{"name": "", "error": f"不支持的路径类型: {source}"}]}

        imported: list[dict] = []
        skipped: list[str] = []
        errors: list[dict] = []

        for json_path in json_files:
            try:
                raw = json_path.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append({"name": json_path.name, "error": str(exc)})
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append({"name": json_path.name, "error": f"Invalid JSON: {exc}"})
                continue

            # Normalize to list
            if isinstance(data, dict):
                items = [data]
            elif isinstance(data, list):
                items = data
            else:
                errors.append({"name": json_path.name, "error": "Expected JSON object or array"})
                continue

            for item in items:
                name = item.get("name", "")
                if not name:
                    errors.append({"name": "", "error": f"Missing required field: name (in {json_path.name})"})
                    continue

                create_params = {
                    "name": name,
                    "description": item.get("description"),
                    "system_prompt": item.get("system_prompt"),
                    "tool_whitelist": item.get("tool_whitelist"),
                    "parameter_constraints": item.get("parameter_constraints"),
                    "category": item.get("category"),
                }
                create_params = {k: v for k, v in create_params.items() if v is not None}

                try:
                    self._store.create_skill(create_params)
                    imported.append(create_params)
                except Exception:
                    skipped.append(name)

        return {"imported": imported, "skipped": skipped, "errors": errors}

    def _record_skill_usage(
        self, *, task_id: str, session_id: str, skill_id: str | None,
    ) -> None:
        if skill_id and hasattr(self._store, "record_skill_usage"):
            self._store.record_skill_usage(
                task_id=task_id, session_id=session_id, skill_id=skill_id,
            )

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



    def _event_context_summary(
        self,
        context: dict[str, Any],
        messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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
            message_tokens = (
                sum(estimate_tokens(message.get("content", "")) for message in messages)
                if messages is not None
                else budget_stats.get("messageTokens")
            )
            tool_schema_tokens = budget_stats.get("toolSchemaTokens") or 0
            estimated_input_tokens = (
                message_tokens + tool_schema_tokens
                if isinstance(message_tokens, (int, float)) and isinstance(tool_schema_tokens, (int, float))
                else budget_stats.get("estimatedInputTokens")
            )
            summary["budgetStats"] = {
                "estimatedTokens": estimated_input_tokens,
                "estimatedInputTokens": estimated_input_tokens,
                "messageTokens": message_tokens,
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

    def _publish_context_update(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        context: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> None:
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.updated",
            payload={
                "status": task.get("status"),
                "currentStep": task.get("currentStep"),
                "context": self._event_context_summary(context, messages=messages),
            },
        )

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

    def _find_supplement_target_task(self, *, session_id: str, task_id: str | None, strict: bool) -> dict[str, Any] | None:
        if not task_id:
            return self._find_open_session_task(session_id)
        try:
            task = self._store.get_task({"taskId": task_id})["task"]
        except Exception:  # noqa: BLE001
            if strict:
                raise ValueError(f"Cannot supplement missing task: {task_id}")
            return None
        if task.get("sessionId") != session_id:
            if strict:
                raise ValueError(f"Cannot supplement task outside session: {task_id}")
            return None
        if task.get("status") not in {"running", "planning", "verifying", "waiting_approval", "queued", "paused"}:
            if strict:
                raise ValueError(f"Cannot supplement task that is not active: {task_id}")
            return None
        return task

    def _attach_supplemental_message(self, *, session_id: str, task: dict[str, Any], content: str) -> dict[str, Any]:
        # Create user message for chat history
        user_msg = self._store.create_message(
            session_id=session_id,
            task_id=task["id"],
            role="user",
            content=content,
            kind="supplement",
        )
        # Write to task inbox so the running loop can consume it
        inbox_entry = self._store.create_inbox_entry(
            task_id=task["id"],
            session_id=session_id,
            content=content,
            message_id=user_msg["id"],
        )
        updated_task = self._store.update_task(
            task_id=task["id"],
            status=task["status"],
            plan=task.get("plan") or [],
            current_step=task.get("currentStep"),
        )
        runtime_task = {**updated_task, "plan": updated_task.get("plan") or task.get("plan") or []}
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.updated",
            payload={
                "status": runtime_task["status"],
                "plan": runtime_task.get("plan") or [],
                "currentStep": runtime_task.get("currentStep"),
                "detail": "Supplemental user message attached to the active task.",
            },
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="task.supplement.received",
            payload={
                "inboxEntryId": inbox_entry["id"],
                "messageId": user_msg["id"],
                "content": content,
            },
        )
        acknowledgement = "\u5df2\u8865\u5145\u5230\u5f53\u524d\u672a\u5b8c\u6210\u4efb\u52a1\uff0c\u7ee7\u7eed\u6cbf\u7528\u539f\u4efb\u52a1\u8ba1\u5212\u3002"
        self._store.create_message(
            session_id=session_id,
            task_id=runtime_task["id"],
            role="assistant",
            content=acknowledgement,
        )
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="assistant.message.completed",
            payload={"content": acknowledgement, "supplemental": True},
        )
        # Route supplement to child tasks if applicable
        routing_result = self._route_supplement_to_children(
            session_id=session_id,
            root_task=runtime_task,
            content=content,
            message_id=user_msg["id"],
        )
        result = {"task": runtime_task, "acceptedMode": "supplement"}
        if routing_result:
            result["supplementRouting"] = routing_result
        return result

    @staticmethod
    def _extract_file_paths(text: str) -> list[str]:
        """Extract plausible file paths from free-form text.

        Matches patterns like `src/foo.py`, `dir/bar.tsx`, `path/to/file.js`, etc.
        """
        pattern = r'(?:^|[\s`"\'(])([\w./\\-]+\.(?:py|ts|tsx|js|jsx|json|yaml|yml|toml|md|rs|go|java|c|cpp|h|hpp|cs|rb|php|sh|bash|sql|html|css|scss|vue|svelte|graphql|proto))(?=[\s`"\')\],;]|$)'
        matches = re.findall(pattern, text)
        return [m for m in matches if len(m) >= 3]

    @staticmethod
    def _assess_supplement_impact(
        content: str,
        children: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Assess which files, modules, and child tasks are affected by a supplement.

        Returns a structured impact scope:
        - affectedFiles: file paths extracted from the supplement
        - affectedChildTasks: list of {childTaskId, matchedFiles, matchType}
        - summary: brief human-readable summary
        """
        affected_files = Orchestrator._extract_file_paths(content)

        # Build a mapping: file path -> [child tasks that touch that file]
        file_to_children: dict[str, list[str]] = {}
        child_id_to_title: dict[str, str] = {}

        for child in children:
            child_id = child.get("id", "")
            child_id_to_title[child_id] = child.get("goal") or child.get("title") or child_id

            # Collect all file paths associated with this child
            child_files: set[str] = set()
            for cf in child.get("changedFiles") or []:
                if isinstance(cf, dict):
                    child_files.add(cf.get("path", ""))
                elif isinstance(cf, str):
                    child_files.add(cf)

            # Also check goal text for file references
            goal = child.get("goal") or ""
            child_files.update(Orchestrator._extract_file_paths(goal))

            for f in child_files:
                if f:
                    file_to_children.setdefault(f, []).append(child_id)

        # Match supplement files to child tasks
        affected_child_tasks: list[dict[str, Any]] = []
        matched_child_ids: set[str] = set()

        for sf in affected_files:
            # Direct match
            if sf in file_to_children:
                for cid in file_to_children[sf]:
                    if cid not in matched_child_ids:
                        matched_child_ids.add(cid)
                        affected_child_tasks.append({
                            "childTaskId": cid,
                            "matchedFiles": [sf],
                            "matchType": "direct",
                        })
                    else:
                        # Append to existing entry
                        for entry in affected_child_tasks:
                            if entry["childTaskId"] == cid and sf not in entry["matchedFiles"]:
                                entry["matchedFiles"].append(sf)

            # Directory prefix match (supplement file is under a child's scope dir)
            for child_file, cids in file_to_children.items():
                child_dir = "/".join(child_file.split("/")[:-1])
                if child_dir and sf.startswith(child_dir + "/") and sf != child_file:
                    for cid in cids:
                        if cid not in matched_child_ids:
                            matched_child_ids.add(cid)
                            affected_child_tasks.append({
                                "childTaskId": cid,
                                "matchedFiles": [sf],
                                "matchType": "directory_prefix",
                            })
                        else:
                            for entry in affected_child_tasks:
                                if entry["childTaskId"] == cid and sf not in entry["matchedFiles"]:
                                    entry["matchedFiles"].append(sf)

        summary_parts: list[str] = []
        if affected_files:
            summary_parts.append(f"Supplement references {len(affected_files)} file(s): {', '.join(affected_files[:5])}")
        if affected_child_tasks:
            summary_parts.append(f"Impacts {len(affected_child_tasks)} child task(s)")

        return {
            "affectedFiles": affected_files,
            "affectedChildTasks": affected_child_tasks,
            "summary": "; ".join(summary_parts) if summary_parts else "No specific file or task impact detected",
        }

    def _route_supplement_to_children(
        self,
        *,
        session_id: str,
        root_task: dict[str, Any],
        content: str,
        message_id: str,
    ) -> dict[str, Any] | None:
        """Route supplement to child tasks based on goal/scope/file relevance.

        Returns routing info dict if any children were matched, None otherwise.
        """
        # Find child tasks under this root
        all_tasks = self._store.list_tasks({"sessionId": session_id}).get("tasks", [])
        root_id = root_task.get("id")
        children = [
            t for t in all_tasks
            if t.get("rootTaskId") == root_id and t.get("id") != root_id
        ]
        if not children:
            return None

        # Assess impact scope
        impact = self._assess_supplement_impact(content, children)

        # Build quick lookup: child_id -> match info from impact assessment
        impact_child_ids: set[str] = set()
        for entry in impact.get("affectedChildTasks", []):
            impact_child_ids.add(entry["childTaskId"])

        # Extract keywords from supplement content for matching
        content_lower = content.lower()
        content_words = set(content_lower.split())

        routed_to: list[dict[str, Any]] = []
        follow_ups: list[dict[str, Any]] = []

        for child in children:
            child_id = child.get("id", "")
            # Score relevance by matching keywords against child goal/scope
            goal = (child.get("goal") or "").lower()
            changed_files = child.get("changedFiles") or []
            scope_text = " ".join(str(f) for f in changed_files).lower()

            # Keyword overlap scoring
            goal_words = set(goal.split())
            scope_words = set(scope_text.split()) if scope_text else set()
            keyword_overlap = len(content_words & (goal_words | scope_words))

            # File-path-based match from impact assessment
            file_match = child_id in impact_child_ids

            # Must have at least one keyword overlap OR a file-path match to route
            if keyword_overlap == 0 and not file_match:
                continue

            # Determine match reason
            match_reasons: list[str] = []
            if file_match:
                match_reasons.append("file_path_match")
            if keyword_overlap > 0:
                match_reasons.append("keyword_overlap")

            child_status = child.get("status", "")
            is_running = child_status in {"running", "planning", "verifying", "waiting_approval", "paused"}
            is_completed = child_status in {"completed", "failed"}

            if is_running:
                # Forward supplement to child's inbox
                inbox_entry = self._store.create_inbox_entry(
                    task_id=child["id"],
                    session_id=session_id,
                    content=content,
                    message_id=message_id,
                )
                routed_to.append({
                    "childTaskId": child["id"],
                    "action": "forwarded",
                    "inboxEntryId": inbox_entry["id"],
                    "matchReasons": match_reasons,
                })
            elif is_completed:
                # Mark for potential follow-up
                follow_ups.append({
                    "childTaskId": child["id"],
                    "action": "follow_up_recommended",
                    "childStatus": child_status,
                    "matchReasons": match_reasons,
                })

        if not routed_to and not follow_ups:
            return None

        routing_info: dict[str, Any] = {
            "routedTo": routed_to,
            "followUps": follow_ups,
            "impactScope": impact,
        }

        # Publish routed event
        self._publish(
            session_id=session_id,
            task=root_task,
            event_type="task.supplement.routed",
            payload={
                "messageId": message_id,
                "content": content,
                "routedToCount": len(routed_to),
                "followUpCount": len(follow_ups),
                "routedTo": routed_to,
                "followUps": follow_ups,
                "impactScope": impact,
            },
        )

        return routing_info

    def _context_with_task_focus(self, context: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
        focused_context = {**context}
        task_focus = {
            "taskId": task.get("id"),
            "goal": task.get("goal"),
            "status": task.get("status"),
            "currentStep": task.get("currentStep"),
            "acceptanceCriteria": list(task.get("acceptanceCriteria") or []),
            "outOfScope": list(task.get("outOfScope") or []),
        }
        focused_context["task_focus"] = task_focus

        messages = list(focused_context.get("messages") or [])
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
        self._validate_task_transition(task["status"], "cancelled", task["id"])
        cancel_background_commands(database_path=self._store.database_path, task_id=task["id"])
        task = self._store.update_task(task_id=params["taskId"], status="cancelled")
        self._clear_pending_react_state(task["id"])
        self._publish(
            session_id=task["sessionId"],
            task=task,
            event_type="task.cancelled",
            payload={"status": task["status"]},
        )
        self._fire_hooks("on_task_cancel", task["sessionId"], task)
        return {"task": task}

    def pause_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task = self._store.get_task({"taskId": params["taskId"]})["task"]
        self._validate_task_transition(task["status"], "paused", task["id"])
        span = self._tracer.start_span("task_pause", trace_id=task.get("id", ""))
        paused_task = self._store.update_task(task_id=task["id"], status="paused")
        self._publish(
            session_id=paused_task["sessionId"],
            task=paused_task,
            event_type="task.paused",
            payload={"status": paused_task["status"], "previousStatus": task["status"]},
        )
        self._fire_hooks("on_task_paused", paused_task["sessionId"], paused_task)
        return {"task": paused_task}

    def _maybe_publish_tool_filter(self, context: dict[str, Any], skill_id: str | None) -> None:
        """Publish skill.tools.filtered event when a skill filtered the available tools."""
        if not skill_id:
            return
        snapshot_meta = context.get("snapshot_metadata") or {}
        filtered_names = snapshot_meta.get("filtered_tool_names")
        if filtered_names is None:
            return
        task_id = context.get("task_id") or ""
        session_id = context.get("session_id", "")
        task = {"id": task_id, "goal": context.get("goal", "")}
        original_names = snapshot_meta.get("original_tool_names") or [t.get("name", "") for t in context.get("tools", [])]
        self._publish(
            session_id=session_id,
            task=task,
            event_type="skill.tools.filtered",
            payload={
                "skillId": skill_id,
                "allowedTools": filtered_names,
                "filteredOut": [n for n in original_names if n not in filtered_names],
                "policy": (context.get("routing") or {}).get("tool_policy", "strict_whitelist"),
            },
        )

    def _runtime_profile_snapshot(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Capture active behavior/prompt profiles for task-level audit."""
        config = context.get("config") if isinstance(context, dict) else None
        if not isinstance(config, dict):
            config = self._store.get_config({})["config"]
        snapshot_meta = context.get("snapshot_metadata") if isinstance(context, dict) else {}
        if not isinstance(snapshot_meta, dict):
            snapshot_meta = {}
        return {
            "autonomyProfile": self._active_config_profile(config, "autonomy"),
            "agentSoulProfile": self._active_config_profile(config, "agentSoul"),
            "promptLayers": snapshot_meta.get("prompt_layers") or [],
        }

    @staticmethod
    def _active_config_profile(config: dict[str, Any], key: str) -> dict[str, Any] | None:
        section = config.get(key)
        if not isinstance(section, dict):
            return None
        profiles = section.get("profiles")
        if not isinstance(profiles, list):
            return None
        active_id = section.get("activeProfileId")
        for profile in profiles:
            if isinstance(profile, dict) and profile.get("id") == active_id:
                return deepcopy(profile)
        for profile in profiles:
            if isinstance(profile, dict):
                return deepcopy(profile)
        return None

    @staticmethod
    def _infer_event_visibility(event_type: str, task: dict[str, Any]) -> str:
        """Determine event visibility based on event type and task role.

        - "chat": root-level user-facing output (messages, task status changes)
        - "panel": child/worker progress visible in task panel
        - "trace": fine-grained token/tool details for debugging
        """
        task_role = task.get("role", "root")
        # Root streaming deltas are user-facing chat output; child deltas stay in trace.
        if event_type in {"assistant.token", "message.delta"}:
            return "chat" if task_role == "root" else "trace"
        # Trace-level: tool call details
        if event_type in {"tool.call.started", "tool.call.completed", "tool.call.failed"}:
            return "trace"
        # Panel-level: child task lifecycle events
        if event_type.startswith("collab."):
            return "panel"
        # Panel-level: child task events detected via role
        if task_role != "root":
            if event_type.startswith(("task.", "message.")):
                return "panel"
            return "trace"
        # Chat-level: everything else for root tasks
        return "chat"

    def _publish(self, session_id: str, task: dict[str, Any], event_type: str, payload: dict[str, Any], *, visibility: str | None = None) -> None:
        if event_type.startswith("task."):
            payload = dict(payload)
            payload.setdefault("goal", task.get("goal"))
            payload.setdefault("acceptanceCriteria", list(task.get("acceptanceCriteria") or []))
            payload.setdefault("outOfScope", list(task.get("outOfScope") or []))
            payload.setdefault("currentStep", task.get("currentStep"))
        effective_visibility = visibility or self._infer_event_visibility(event_type, task)
        # Enrich streaming token events with messageId and emit unified message.delta
        if event_type == "assistant.token":
            payload = dict(payload)
            active_msg_id = task.get("activeAssistantMessageId")
            if active_msg_id:
                payload["messageId"] = active_msg_id
            # Emit the new unified event name alongside the legacy one
            delta_payload = {**payload}
            delta_payload.setdefault("messageId", active_msg_id or "")
            delta_event = RuntimeEvent(
                event_id=self._store.new_id("evt"),
                session_id=session_id,
                task_id=task["id"],
                type="message.delta",
                ts=self._store.now(),
                payload=delta_payload,
                visibility=effective_visibility,
            )
            self._event_bus.publish(delta_event)
        event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id=session_id,
            task_id=task["id"],
            type=event_type,
            ts=self._store.now(),
            payload=payload,
            visibility=effective_visibility,
        )
        self._event_bus.publish(event)

    def _fire_hooks(
        self,
        hook_event: str,
        session_id: str,
        task: dict[str, Any],
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fire matching hooks for a lifecycle event. No-op if no hook service."""
        if self._hook_service is None:
            return []
        # Resolve workspaceId from session
        session = self._store.get_session(session_id)
        workspace_id = ""
        if isinstance(session, dict):
            workspace_id = session.get("workspaceId") or session.get("workspace_id") or ""
        context: dict[str, Any] = {
            "workspaceId": workspace_id,
            "sessionId": session_id,
            "taskId": task.get("id", ""),
            "taskStatus": task.get("status", ""),
            "changedFiles": task.get("changedFiles") or [],
        }
        if extra_context:
            context.update(extra_context)
        try:
            return self._hook_service.invoke_hooks(hook_event, context)
        except Exception:
            logger.warning("Hook execution failed for %s", hook_event, exc_info=True)
            return []

    