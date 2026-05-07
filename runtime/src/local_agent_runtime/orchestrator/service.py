from __future__ import annotations

import json
import logging
import os
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
from ..skills.registry import SkillRegistry
from ..mcp.client import McpClientManager, McpServerConfig, summarize_mcp_exception
from ..reflection.evaluator import ReflectionEvaluator
from ..reflection.types import ReflectionConfig
from ..planner.decomposer import TaskDecomposer
from ..planner.dag_executor import DAGExecutor
from ..planner.coverage import CoverageEvaluator
from ..orchestration import OrchestrationMode, SupervisorOrchestrator, SwarmOrchestrator
from ..store.sqlite_store import SQLiteStore
from ..tools import build_builtin_tools
from ..tools.registry import BUILTIN_TOOL_SCHEMAS, ToolRegistry
from ..observability.tracer import Tracer


class Orchestrator:
    """Coordinates the first-pass agent loop for Sprint 1."""

    def __init__(
        self,
        store: Any,
        event_bus: EventBus,
        tool_registry: Any,
        provider: Any,
        meta_router: MetaRouter | None = None,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        self._store = store
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._provider = provider
        self._meta_router = meta_router or MetaRouter()
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

    # ------------------------------------------------------------------
    # MCP Server management
    # ------------------------------------------------------------------

    def initialize_mcp_servers(self) -> None:
        """Connect to all enabled MCP servers and register their tools."""
        result = self._store.list_mcp_servers({"enabledOnly": True})
        for server_row in result["servers"]:
            span = self._tracer.start_span(
                "mcp_connect",
                attributes={"server_id": server_row.get("id", ""), "phase": "init"},
            )
            try:
                config = McpServerConfig.from_row(server_row)
                schemas = self._mcp_manager.sync_connect_server(config)
                for schema in schemas:
                    namespaced_name = schema["name"]
                    self._tool_registry.register(
                        namespaced_name,
                        lambda args, _name=namespaced_name: self._mcp_manager.sync_call_tool(_name, args),
                        schema,
                    )
                self._tracer.end_span(span.span_id, status="ok", attributes={"tool_count": len(schemas)})
                self._publish_mcp_event("mcp.server.connected", {
                    "serverId": server_row.get("id", ""),
                    "serverName": server_row.get("name", ""),
                    "toolCount": len(schemas),
                    "toolNames": [s.get("name", "") for s in schemas],
                })
            except Exception as exc:  # noqa: BLE001
                self._record_mcp_connect_failure(span=span, server=server_row, phase="init", exc=exc)

    def compact_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Manually trigger context compaction for a session.

        Params: ``{ sessionId: str, maxTokens?: int }``
        Returns: ``{ tokensBefore, tokensAfter, summary, strategy }``
        """
        session_id = params.get("sessionId") or params.get("session_id")
        if not session_id:
            raise ValueError("sessionId is required")
        if self._compactor is None:
            return {"tokensBefore": 0, "tokensAfter": 0, "summary": None, "strategy": "none"}

        msg_result = self._store.list_messages({"sessionId": session_id, "limit": 500})
        messages = msg_result.get("messages", [])
        if not messages:
            return {"tokensBefore": 0, "tokensAfter": 0, "summary": None, "strategy": "none"}

        max_tokens = params.get("maxTokens") or 6000
        compacted = self._compactor.compact(
            session_id=session_id,
            messages=messages,
            max_tokens=max_tokens,
        )
        return {
            "tokensBefore": compacted.tokens_before,
            "tokensAfter": compacted.tokens_after,
            "summary": compacted.summary,
            "strategy": compacted.strategy,
            "compactionId": compacted.compaction_id,
        }

    def mcp_server_list(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._store.list_mcp_servers(params)
        # Enrich with live connection status and tool counts
        all_mcp_schemas = self._mcp_manager.get_tool_schemas()
        for server in result.get("servers", []):
            sid = server.get("id", "")
            server["connected"] = self._mcp_manager.is_connected(sid)
            server["toolCount"] = sum(1 for s in all_mcp_schemas if s.get("_mcp_server_id") == sid)
        return result

    def mcp_server_create(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._store.create_mcp_server(params)
        server = result["server"]
        if server.get("enabled", True):
            span = self._tracer.start_span(
                "mcp_connect",
                attributes={"server_id": server.get("id", ""), "phase": "create"},
            )
            try:
                config = McpServerConfig.from_row(server)
                schemas = self._mcp_manager.sync_connect_server(config)
                for schema in schemas:
                    namespaced_name = schema["name"]
                    self._tool_registry.register(
                        namespaced_name,
                        lambda args, _name=namespaced_name: self._mcp_manager.sync_call_tool(_name, args),
                        schema,
                    )
                self._tracer.end_span(span.span_id, status="ok", attributes={"tool_count": len(schemas)})
                self._publish_mcp_event("mcp.server.connected", {
                    "serverId": server.get("id", ""),
                    "serverName": server.get("name", ""),
                    "toolCount": len(schemas),
                    "toolNames": [s.get("name", "") for s in schemas],
                })
            except Exception as exc:  # noqa: BLE001
                self._record_mcp_connect_failure(span=span, server=server, phase="create", exc=exc)
        return result

    def mcp_server_update(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("serverId") or params.get("server_id")
        span = self._tracer.start_span(
            "mcp_update",
            attributes={"server_id": server_id or ""},
        )
        # Disconnect old tools if server was connected
        if server_id and self._mcp_manager.is_connected(server_id):
            prefix = f"mcp__{server_id}__"
            self._tool_registry.unregister_prefix(prefix)
            self._mcp_manager.sync_disconnect_server(server_id)
        result = self._store.update_mcp_server(params)
        server = result["server"]
        if server.get("enabled", True):
            try:
                config = McpServerConfig.from_row(server)
                schemas = self._mcp_manager.sync_connect_server(config)
                for schema in schemas:
                    namespaced_name = schema["name"]
                    self._tool_registry.register(
                        namespaced_name,
                        lambda args, _name=namespaced_name: self._mcp_manager.sync_call_tool(_name, args),
                        schema,
                    )
                self._tracer.end_span(span.span_id, status="ok", attributes={"tool_count": len(schemas)})
                self._publish_mcp_event("mcp.server.reconnected", {
                    "serverId": server.get("id", ""),
                    "serverName": server.get("name", ""),
                    "toolCount": len(schemas),
                    "toolNames": [s.get("name", "") for s in schemas],
                })
            except Exception as exc:  # noqa: BLE001
                self._record_mcp_connect_failure(span=span, server=server, phase="update", exc=exc)
        else:
            self._tracer.end_span(span.span_id, status="ok")
        return result

    def mcp_server_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("serverId") or params.get("server_id")
        server_name = ""
        if server_id:
            # Try to get server name before deletion for the event
            existing = self._store.list_mcp_servers({})
            for s in existing.get("servers", []):
                if s.get("id") == server_id:
                    server_name = s.get("name", "")
                    break
            span = self._tracer.start_span(
                "mcp_disconnect",
                attributes={"server_id": server_id, "phase": "delete"},
            )
            prefix = f"mcp__{server_id}__"
            self._tool_registry.unregister_prefix(prefix)
            self._mcp_manager.sync_disconnect_server(server_id)
            self._tracer.end_span(span.span_id, status="ok")
            self._publish_mcp_event("mcp.server.disconnected", {
                "serverId": server_id,
                "serverName": server_name,
            })
        return self._store.delete_mcp_server(params)

    def mcp_tools_refresh(self, params: dict[str, Any]) -> dict[str, Any]:
        server_id = params.get("serverId") or params.get("server_id")
        span = self._tracer.start_span(
            "mcp_refresh",
            attributes={"server_id": server_id or "all"},
        )
        # Remove old tools for the target server(s)
        if server_id:
            prefix = f"mcp__{server_id}__"
            self._tool_registry.unregister_prefix(prefix)
        else:
            for sid in list(self._mcp_manager._connections):
                prefix = f"mcp__{sid}__"
                self._tool_registry.unregister_prefix(prefix)
        try:
            schemas = self._mcp_manager.sync_refresh_tools(server_id)
            for schema in schemas:
                namespaced_name = schema["name"]
                self._tool_registry.register(
                    namespaced_name,
                    lambda args, _name=namespaced_name: self._mcp_manager.sync_call_tool(_name, args),
                    schema,
                )
            self._tracer.end_span(span.span_id, status="ok", attributes={"tool_count": len(schemas)})
            self._publish_mcp_event("mcp.tools.refreshed", {
                "serverId": server_id or "all",
                "toolCount": len(schemas),
                "toolNames": [s.get("name", "") for s in schemas],
            })
        except Exception as exc:  # noqa: BLE001
            self._tracer.end_span(span.span_id, status="error", attributes={"error": str(exc)})
            raise
        return {"refreshed": len(schemas), "tools": [s["name"] for s in schemas]}

    def shutdown_mcp(self) -> None:
        """Clean up all MCP server connections."""
        self._mcp_manager.shutdown()

    def graceful_shutdown(self, timeout: float = 10.0) -> None:
        """Gracefully shut down the orchestrator.

        1. Reject new tasks.
        2. Wait for running tasks to reach a pause point.
        3. Cancel tasks that didn't stop in time.
        4. Cancel all background commands.
        5. Shut down MCP connections.
        """
        import time as _time

        self._shutting_down = True
        logger.info("Graceful shutdown initiated — rejecting new tasks")

        # Wait for in-flight tasks to finish or pause.
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if not hasattr(self._store, "list_tasks_by_status"):
                break
            running = self._store.list_tasks_by_status(["running"])
            if not running:
                break
            logger.debug(
                "Waiting for %d running tasks to complete...", len(running),
            )
            _time.sleep(0.1)

        # Cancel any tasks still running after the deadline.
        if hasattr(self._store, "list_tasks_by_status"):
            for task in self._store.list_tasks_by_status(["running"]):
                logger.info("Force-cancelling task %s after shutdown timeout", task["id"])
                try:
                    self.cancel_task({"taskId": task["id"]})
                except Exception:  # noqa: BLE001
                    pass

        # Cancel all background commands.
        try:
            db_path = getattr(self._store, "database_path", ":memory:")
            service = get_background_command_service(db_path)
            for cmd_id in service.active_command_ids():
                try:
                    service.cancel_command(cmd_id)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

        self.shutdown_mcp()
        logger.info("Graceful shutdown complete")

    def send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._shutting_down:
            raise RuntimeError("服务正在关闭，暂不接受新任务")
        session = self._store.require_session(params["sessionId"])
        goal = params["content"]

        explicit_supplement = params.get("mode") == "supplement"
        explicit_task_id = params.get("taskId") or params.get("task_id")
        should_auto_supplement = params.get("background") is not True and params.get("newTask") is not True
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

        # --- Phase 0: MetaRouter scenario classification ---
        import time as _time
        _route_t0 = _time.monotonic()
        routing_span = self._tracer.start_span("routing_decision", attributes={"goal": goal[:200]})
        try:
            routing = self._meta_router.route(goal)
        except Exception:
            self._tracer.end_span(routing_span.span_id, status="error")
            raise
        _route_latency_ms = int((_time.monotonic() - _route_t0) * 1000)
        routing_dict = {
            "scenario": routing.scenario.value,
            "strategy": routing.strategy.value,
            "confidence": routing.confidence,
            "max_steps": routing.max_steps,
            "enable_reflection": routing.enable_reflection,
            "enable_planning": routing.enable_planning,
            "reasoning": routing.reasoning,
            "skill_id": routing.skill_id,
        }
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
            self._store.create_message(
                session_id=session["id"],
                task_id=runtime_task["id"],
                role="user",
                content=goal,
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
            return {"task": runtime_task}

        context = self._context_builder.build(session_id=session["id"], goal=goal, skill_id=routing.skill_id, lightweight=False)
        # Inject routing decision into context as a plain dict for JSON safety.
        context["routing"] = routing_dict
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
        self._store.create_message(
            session_id=session["id"],
            task_id=runtime_task["id"],
            role="user",
            content=goal,
        )
        context = self._context_with_task_focus(context, runtime_task)

        self._record_skill_usage(
            task_id=runtime_task["id"],
            session_id=session["id"],
            skill_id=routing.skill_id,
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

    def _execute_message_task(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        # --- Planning branch ---
        routing = context.get("routing", {})
        strategy = routing.get("strategy", "react_standard")
        logger.info("Executing strategy=%s for task=%s", strategy, task["id"])
        if routing.get("enable_planning"):
            orch_mode = self._resolve_orchestration_mode(strategy)
            if orch_mode == OrchestrationMode.SUPERVISOR:
                result = self._execute_with_supervisor(
                    session_id=session_id, task=task, goal=goal, context=context,
                )
                if result.get("status") in ("paused", "waiting_approval"):
                    return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
                return result
            if orch_mode == OrchestrationMode.SWARM:
                result = self._execute_with_swarm(
                    session_id=session_id, task=task, goal=goal, context=context,
                )
                if result.get("status") in ("paused", "waiting_approval"):
                    return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
                return result
            result = self._execute_with_planning(
                session_id=session_id, task=task, goal=goal, context=context,
            )
            if result.get("status") in ("paused", "waiting_approval"):
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
            return result
        # --- REACT_FAST: simplified path, skip minimal_loop and reflection ---
        if strategy == "react_fast":
            return self._execute_react_fast(
                session_id=session_id, task=task, goal=goal, context=context,
            )
        # --- Standard ReAct path ---
        try:
            react_result = self._run_react_loop(
                session_id=session_id,
                task=task,
                goal=goal,
                context=context,
            )
            if react_result["status"] == "waiting_approval":
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
            if react_result["status"] == "paused":
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
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
            logger.error("React loop failed for task=%s: %s", task["id"], exc, exc_info=True)
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="LOOP_EXECUTION_FAILED",
                )
            }

    def _execute_react_fast(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Fast ReAct path for simple queries. Skips minimal_loop and reflection."""
        try:
            react_result = self._run_react_loop(
                session_id=session_id,
                task=task,
                goal=goal,
                context=context,
            )
            if react_result["status"] == "waiting_approval":
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
            if react_result["status"] == "paused":
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}
            summary = react_result.get("summary") or self._provider.summarize_findings(
                goal=goal,
                context=context,
                tool_results=react_result.get("tool_results", []),
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
                    tool_results=react_result.get("tool_results", []),
                    skip_reflection=True,
                )
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Fast react loop failed for task=%s: %s", task["id"], exc, exc_info=True)
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="FAST_LOOP_FAILED",
                )
            }

    def _execute_with_planning(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Planning mode: decompose goal → execute subtasks → synthesize."""
        plan_span = self._tracer.start_span(
            "planning",
            trace_id=getattr(self, "_active_trace_id", None),
            attributes={"goal": goal[:200]},
        )
        try:
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.started",
                payload={"goal": goal},
            )

            # 1. Decompose
            plan_context = json.dumps(
                context.get("tool_results", []), ensure_ascii=False,
            )[:2000]
            decomp_span = self._tracer.start_span(
                "plan_decomposition",
                trace_id=getattr(self, "_active_trace_id", None),
                attributes={"goal": goal[:200]},
            )
            plan = self._decomposer.decompose(goal=goal, context=plan_context)
            self._tracer.end_span(
                decomp_span.span_id, status="ok",
                attributes={"subtask_count": len(plan.subtasks), "execution_order": plan.execution_order},
            )

            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.decomposed",
                payload={
                    "subtaskCount": len(plan.subtasks),
                    "executionOrder": plan.execution_order,
                },
            )

            # 1b. Plan approval gate (strict mode)
            config = self._store.get_config({})["config"]
            approval_mode = config.get("policy", {}).get("approvalMode", "on_write_or_command")
            if approval_mode == "strict":
                plan_summary = [f"- {s.id}: {s.title}" for s in plan.subtasks]
                approval = self._store.create_approval(
                    task_id=task["id"],
                    kind="plan",
                    request={
                        "goal": goal,
                        "subtaskCount": len(plan.subtasks),
                        "subtasks": plan_summary,
                        "executionOrder": plan.execution_order,
                    },
                )
                plan_data = {
                    "subtasks": [
                        {"id": s.id, "title": s.title, "description": s.description,
                         "dependencies": s.dependencies, "status": s.status, "result": s.result}
                        for s in plan.subtasks
                    ],
                    "dag": plan.dag,
                    "execution_order": plan.execution_order,
                }
                if hasattr(self._store, "upsert_pending_dag_state"):
                    self._store.upsert_pending_dag_state(
                        task_id=task["id"],
                        session_id=session_id,
                        goal=goal,
                        context=context,
                        plan_json=json.dumps(plan_data, ensure_ascii=False),
                        completed_ids=[],
                        failed_ids=[],
                        results={},
                    )
                self._store.update_task_status(task_id=task["id"], status="waiting_approval")
                self._publish(
                    session_id=session_id, task=task,
                    event_type="approval.requested",
                    payload={
                        "approvalId": approval["id"],
                        "taskId": task["id"],
                        "kind": "plan",
                        "request": {
                            "goal": goal,
                            "subtaskCount": len(plan.subtasks),
                            "subtasks": plan_summary,
                            "executionOrder": plan.execution_order,
                        },
                    },
                )
                self._publish(
                    session_id=session_id, task=task,
                    event_type="task.waiting_approval",
                    payload={"status": "waiting_approval", "detail": "执行前需要先审批计划。"},
                )
                self._tracer.end_span(plan_span.span_id, status="ok", attributes={"status": "waiting_plan_approval"})
                return {"status": "waiting_approval"}

            # 2. Execute subtasks
            def _on_subtask_event(subtask_id: str, event: str, details: dict[str, Any]) -> None:
                event_type = f"task.planning.subtask.{event}"
                self._publish(session_id=session_id, task=task, event_type=event_type, payload=details)

            execution = self._dag_executor.execute(
                plan,
                session_id=session_id,
                parent_task_id=task["id"],
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                tracer=self._tracer,
                on_subtask_callback=_on_subtask_event,
            )

            # Handle DAG cooperative pause
            if execution.get("paused"):
                plan_data = {
                    "subtasks": [
                        {"id": s.id, "title": s.title, "description": s.description,
                         "dependencies": s.dependencies, "status": s.status, "result": s.result}
                        for s in plan.subtasks
                    ],
                    "dag": plan.dag,
                    "execution_order": plan.execution_order,
                }
                if hasattr(self._store, "upsert_pending_dag_state"):
                    self._store.upsert_pending_dag_state(
                        task_id=task["id"],
                        session_id=session_id,
                        goal=goal,
                        context=context,
                        plan_json=json.dumps(plan_data, ensure_ascii=False),
                        completed_ids=execution["completed"],
                        failed_ids=execution["failed"],
                        results=execution.get("results", {}),
                    )
                return {"status": "paused"}

            # 3. Coverage evaluation
            coverage = self._coverage_evaluator.evaluate(
                goal, execution["subtasks"],
            )

            # 4. Auto-supplement if coverage is insufficient
            routing = context.get("routing", {})
            threshold = routing.get("coverage_threshold", 0.7)
            if coverage < threshold:
                gaps = self._coverage_evaluator.find_gaps(goal, execution["subtasks"])
                if gaps:
                    from ..planner.types import Subtask as PlanSubtask
                    supplement = PlanSubtask(
                        id="supplement-0",
                        title="Address uncovered aspects",
                        description=(
                            f"The original goal has uncovered aspects related to: "
                            f"{', '.join(gaps)}. Please address these."
                        ),
                        dependencies=[
                            s.id for s in execution["subtasks"] if s.status == "completed"
                        ],
                    )
                    try:
                        dispatch_result = self._subagent_service.dispatch({
                            "prompt": supplement.description,
                            "title": supplement.title,
                            "sessionId": session_id,
                            "taskId": task["id"],
                            "agentType": "planner",
                        })
                        supplement.status = "completed"
                        supplement.result = dispatch_result.get("summary") or "Completed"
                        execution["subtasks"].append(supplement)
                        # Re-evaluate coverage after supplement
                        coverage = self._coverage_evaluator.evaluate(goal, execution["subtasks"])
                        logger.info(
                            "Supplement subtask executed, coverage: %.2f → %.2f",
                            coverage, coverage,
                        )
                    except Exception as supp_exc:  # noqa: BLE001
                        logger.warning("Supplement subtask failed: %s", supp_exc)

            summary = execution["summary"]

            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={
                    "coverage": coverage,
                    "success": execution["success"],
                },
            )

            self._tracer.end_span(
                plan_span.span_id, status="ok",
                attributes={"subtaskCount": len(execution["subtasks"]), "coverage": coverage},
            )

            return {
                "task": self._complete_task(
                    session_id=session_id,
                    task=task,
                    summary=summary,
                    context=context,
                ),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Planning execution failed for task=%s: %s", task["id"], exc, exc_info=True)
            self._tracer.end_span(plan_span.span_id, status="error")
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="PLANNING_EXECUTION_FAILED",
                ),
            }

    def _resolve_orchestration_mode(self, strategy: str) -> OrchestrationMode:
        """Map an ExecutionStrategy string to an OrchestrationMode."""
        if strategy == "plan_supervise":
            return OrchestrationMode.SUPERVISOR
        if strategy == "plan_swarm":
            return OrchestrationMode.SWARM
        return OrchestrationMode.DAG

    def _check_plan_approval(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        orchestration_mode: str,
        plan: Any,
        span: Any,
    ) -> dict[str, Any] | None:
        """Check if plan approval is required. Returns response dict if waiting, None if okay to proceed."""
        config = self._store.get_config({})["config"]
        approval_mode = config.get("policy", {}).get("approvalMode", "on_write_or_command")
        if approval_mode != "strict":
            return None

        plan_summary = [f"- {s.id}: {s.title}" for s in plan.subtasks]
        approval = self._store.create_approval(
            task_id=task["id"],
            kind="plan",
            request={
                "goal": goal,
                "subtaskCount": len(plan.subtasks),
                "subtasks": plan_summary,
                "executionOrder": plan.execution_order,
                "orchestrationMode": orchestration_mode,
            },
        )
        plan_data = {
            "subtasks": [
                {"id": s.id, "title": s.title, "description": s.description,
                 "dependencies": s.dependencies, "status": s.status, "result": s.result}
                for s in plan.subtasks
            ],
            "dag": plan.dag,
            "execution_order": plan.execution_order,
        }
        context_with_mode = {**context, "orchestration_mode": orchestration_mode}
        if hasattr(self._store, "upsert_pending_dag_state"):
            self._store.upsert_pending_dag_state(
                task_id=task["id"],
                session_id=session_id,
                goal=goal,
                context=context_with_mode,
                plan_json=json.dumps(plan_data, ensure_ascii=False),
                completed_ids=[],
                failed_ids=[],
                results={},
            )
        self._store.update_task_status(task_id=task["id"], status="waiting_approval")
        self._publish(
            session_id=session_id, task=task,
            event_type="approval.requested",
            payload={
                "approvalId": approval["id"],
                "taskId": task["id"],
                "kind": "plan",
                "request": {
                    "goal": goal,
                    "subtaskCount": len(plan.subtasks),
                    "subtasks": plan_summary,
                    "executionOrder": plan.execution_order,
                    "orchestrationMode": orchestration_mode,
                },
            },
        )
        self._publish(
            session_id=session_id, task=task,
            event_type="task.waiting_approval",
            payload={"status": "waiting_approval", "detail": "执行前需要先审批计划。"},
        )
        self._tracer.end_span(span.span_id, status="ok", attributes={"status": "waiting_plan_approval"})
        return {"status": "waiting_approval"}

    def _execute_with_supervisor(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Supervisor mode: decompose → execute with review → synthesize."""
        span = self._tracer.start_span(
            "supervisor_execute",
            trace_id=task.get("id", ""),
            attributes={"goal": goal[:200]},
        )
        try:
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.started",
                payload={"goal": goal, "mode": "supervisor"},
            )

            # Plan approval gate (strict mode)
            plan_context = json.dumps(
                context.get("tool_results", []), ensure_ascii=False,
            )[:2000]
            plan = self._decomposer.decompose(goal=goal, context=plan_context)
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.decomposed",
                payload={
                    "subtaskCount": len(plan.subtasks),
                    "executionOrder": plan.execution_order,
                    "mode": "supervisor",
                },
            )
            approval_response = self._check_plan_approval(
                session_id=session_id, task=task, goal=goal, context=context,
                orchestration_mode="supervisor", plan=plan, span=span,
            )
            if approval_response is not None:
                return approval_response

            result = self._supervisor.execute(
                goal, context,
                session_id=session_id, task=task,
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
            )

            if result.paused:
                self._tracer.end_span(span.span_id, status="ok", attributes={"paused": True})
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}

            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={"mode": "supervisor", "reviews": result.review_count},
            )

            self._tracer.end_span(span.span_id, status="ok", attributes={"reviews": result.review_count})
            return {
                "task": self._complete_task(
                    session_id=session_id,
                    task=task,
                    summary=result.summary,
                    context=context,
                ),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Supervisor execution failed for task=%s: %s", task["id"], exc, exc_info=True)
            self._tracer.end_span(span.span_id, status="error", attributes={"error": str(exc)})
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="SUPERVISOR_EXECUTION_FAILED",
                ),
            }

    def _execute_with_swarm(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Swarm mode: decompose → execute with handoff → synthesize."""
        span = self._tracer.start_span(
            "swarm_execute",
            trace_id=task.get("id", ""),
            attributes={"goal": goal[:200]},
        )
        try:
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.started",
                payload={"goal": goal, "mode": "swarm"},
            )

            # Plan approval gate (strict mode)
            plan_context = json.dumps(
                context.get("tool_results", []), ensure_ascii=False,
            )[:2000]
            plan = self._decomposer.decompose(goal=goal, context=plan_context)
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.decomposed",
                payload={
                    "subtaskCount": len(plan.subtasks),
                    "executionOrder": plan.execution_order,
                    "mode": "swarm",
                },
            )
            approval_response = self._check_plan_approval(
                session_id=session_id, task=task, goal=goal, context=context,
                orchestration_mode="swarm", plan=plan, span=span,
            )
            if approval_response is not None:
                return approval_response

            result = self._swarm.execute(
                goal, context,
                session_id=session_id, task=task,
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
            )

            if result.paused:
                self._tracer.end_span(span.span_id, status="ok", attributes={"paused": True})
                return {"task": self._store.get_task({"taskId": task["id"]})["task"]}

            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={"mode": "swarm", "handoffs": result.handoff_count},
            )

            self._tracer.end_span(span.span_id, status="ok", attributes={"handoffs": result.handoff_count})
            return {
                "task": self._complete_task(
                    session_id=session_id,
                    task=task,
                    summary=result.summary,
                    context=context,
                ),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("Swarm execution failed for task=%s: %s", task["id"], exc, exc_info=True)
            self._tracer.end_span(span.span_id, status="error", attributes={"error": str(exc)})
            return {
                "task": self._fail_task(
                    session_id=session_id,
                    task=task,
                    summary=str(exc),
                    error_code="SWARM_EXECUTION_FAILED",
                ),
            }

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

    def _run_background_message(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any] | None,
        routing: dict[str, Any] | None = None,
        skill_id: str | None = None,
    ) -> None:
        background_store: SQLiteStore | None = None
        worker = self
        try:
            logger.info(
                "Background message execution started for task=%s session=%s routing=%s",
                task["id"], session_id, routing,
            )
            worker, background_store = self._background_worker_orchestrator()
            if context is None:
                context = worker._context_builder.build(
                    session_id=session_id, goal=goal, skill_id=skill_id, lightweight=False,
                )
                if routing is not None:
                    context["routing"] = routing
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
            _provider_mode = (context.get("config") or {}).get("provider", {}).get("mode", "unknown")
            _will_stream = worker._should_stream_provider({**context, "messages": [], "tools": [], "step": 1})
            logger.info(
                "Background worker executing task=%s strategy=%s provider_mode=%s will_stream=%s",
                task["id"], (context.get("routing") or {}).get("strategy"), _provider_mode, _will_stream,
            )
            worker._execute_message_task(
                session_id=session_id,
                task=task,
                goal=goal,
                context=context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Background message execution failed: %s", exc, exc_info=True)
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
        from ..memory import MemoryManager, MemoryRetriever, MemoryStore
        from ..context.scratchpad import Scratchpad
        bg_memory_manager = MemoryManager(
            store=MemoryStore(store),
            retriever=MemoryRetriever(MemoryStore(store)),
        )
        bg_scratchpad = Scratchpad(store)
        tool_registry = ToolRegistry(
            build_builtin_tools(
                policy_guard=policy_guard,
                store=store,
                subagent_service=subagent_service,
                memory_manager=bg_memory_manager,
                scratchpad=bg_scratchpad,
            )
        )
        return (
            Orchestrator(
                store=store,
                event_bus=self._event_bus,
                tool_registry=tool_registry,
                provider=ProviderAdapter(),
                memory_manager=bg_memory_manager,
            ),
            store,
        )

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
        self._store.create_message(
            session_id=session_id,
            task_id=task["id"],
            role="user",
            content=content,
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
        return {"task": runtime_task}

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
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("sessionId is required")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt is required")

        span = self._tracer.start_span(
            "child_task",
            attributes={"prompt": prompt[:200]},
        )

        session = self._store.require_session(session_id)
        budget = WorkerBudget.from_metadata(params.get("budget"), params)
        context = self._context_builder.build(session_id=session["id"], goal=prompt.strip(), lightweight=False)
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
        skip_reflection: bool = False,
    ) -> dict[str, Any]:
        validation = self._run_post_task_validation(
            session_id=session_id,
            task=task,
            context=context or {},
            tool_results=tool_results or [],
        )
        final_summary = self._merge_completion_summary(summary=summary, validation=validation)

        # --- Reflection phase ---
        reflection_data = None
        reflection_result = None if skip_reflection else self._reflect_on_result(
            session_id=session_id,
            task=task,
            goal=task.get("goal", ""),
            summary=final_summary,
            context=context or {},
        )
        if reflection_result is not None:
            reflection_data = self._reflector.to_dict(reflection_result)
            if reflection_result.improved_summary:
                final_summary = reflection_result.improved_summary
        # --- End reflection ---

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
            reflection=reflection_data,
        )
        runtime_task = {
            **completed_task,
            "plan": task["plan"],
            "resultSummary": final_summary,
        }
        logger.info("Task %s completed: summary_len=%d", task["id"], len(final_summary))
        self._store.create_message(
            session_id=session_id,
            task_id=runtime_task["id"],
            role="assistant",
            content=final_summary,
        )
        self._remember_task_result(session_id=session_id, task=runtime_task)
        self._promote_scratchpad_to_memory(session_id)
        self._consolidate_working_memories(session_id)
        self._clear_pending_react_state(task["id"])
        self._record_task_metrics(session_id=session_id, task=runtime_task, tool_results=tool_results, task_status="completed")
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
                "reflection": reflection_data,
                "summary": final_summary,
                "resultSummary": final_summary,
                "detail": final_summary,
            },
        )
        return runtime_task

    def _reflect_on_result(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        summary: str,
        context: dict[str, Any],
    ) -> ReflectionResult | None:
        """Conditionally trigger reflection: routing decision + global config enabled."""
        if self._reflector is None:
            return None
        routing = context.get("routing", {})
        if not routing.get("enable_reflection"):
            return None

        self._store.update_task_status(task["id"], "verifying")
        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.reflection.started",
            payload={"goal": goal},
        )

        refl_span = self._tracer.start_span(
            "reflection",
            trace_id=getattr(self, "_active_trace_id", None),
            attributes={"taskId": task["id"]},
        )

        tool_output = json.dumps(
            context.get("tool_results", []), ensure_ascii=False,
        )[:2000]

        # Construct retry_fn so the reflection loop can re-generate improved output
        def _retry_fn(feedback: str) -> str:
            retry_prompt = (
                f"你之前的回答存在以下问题:\n{feedback}\n\n"
                f"原始目标: {goal}\n"
                f"请基于以上反馈，重新生成一个改进版的回答。"
            )
            try:
                retry_response = self._provider.generate(
                    retry_prompt,
                    {"messages": [{"role": "user", "content": retry_prompt}]},
                )
                return retry_response.get("message") or retry_response.get("final_answer") or summary
            except Exception:  # noqa: BLE001
                return summary

        result = self._reflector.reflect(
            goal=goal,
            output=summary,
            context=tool_output,
            retry_fn=_retry_fn,
        )

        self._publish(
            session_id=session_id,
            task=task,
            event_type="task.reflection.completed",
            payload={
                "accepted": result.accepted,
                "finalScore": result.final_score,
                "iterations": len(result.iterations),
            },
        )
        self._tracer.end_span(
            refl_span.span_id, status="ok",
            attributes={"accepted": result.accepted, "iterations": len(result.iterations)},
        )
        return result

    def _fail_task(
        self,
        session_id: str,
        task: dict[str, Any],
        summary: str,
        error_code: str,
    ) -> dict[str, Any]:
        logger.warning("Task %s failed: error_code=%s summary=%s", task["id"], error_code, summary[:200])
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
        self._promote_scratchpad_to_memory(session_id)
        self._clear_pending_react_state(task["id"])
        self._record_task_metrics(session_id=session_id, task=runtime_task, task_status="failed")
        self._publish(
            session_id=session_id,
            task=runtime_task,
            event_type="assistant.message.completed",
            payload={"content": summary},
        )
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

    def _record_task_metrics(
        self,
        *,
        session_id: str,
        task: dict[str, Any],
        tool_results: list[dict[str, Any]] | None = None,
        task_status: str = "completed",
    ) -> None:
        tool_results = tool_results or []
        duration_ms = (task.get("updated_at") or 0) - (task.get("created_at") or 0)
        if duration_ms < 0:
            duration_ms = 0
        command_count = 0
        patch_count = 0
        command_success = 0
        command_failure = 0
        patch_success = 0
        patch_failure = 0
        for tr in tool_results:
            name = tr.get("name", "")
            result = tr.get("result", {})
            if name == "run_command":
                command_count += 1
                status = result.get("status") or result.get("exitCode")
                if status in ("completed", 0):
                    command_success += 1
                else:
                    command_failure += 1
            elif name == "apply_patch":
                patch_count += 1
                if result.get("patch_id") or result.get("applied"):
                    patch_success += 1
                else:
                    patch_failure += 1
        self._store.record_task_metrics({
            "taskId": task["id"],
            "sessionId": session_id,
            "durationMs": duration_ms,
            "toolCallCount": len(tool_results),
            "commandCount": command_count,
            "patchCount": patch_count,
            "commandSuccessCount": command_success,
            "commandFailureCount": command_failure,
            "patchSuccessCount": patch_success,
            "patchFailureCount": patch_failure,
            "taskStatus": task_status,
            "wasCancelled": task_status == "cancelled",
        })

    def _promote_scratchpad_to_memory(self, session_id: str) -> None:
        """Promote scratchpad entries to session memory after task completion."""
        if self._memory_manager is None or self._scratchpad is None:
            return
        from ..memory.types import MemoryKind
        entries = self._scratchpad.list_entries(session_id)
        for entry in entries:
            self._memory_manager.remember(
                content=f"[{entry.key}] {entry.value}",
                session_id=session_id,
                kind=MemoryKind.SESSION,
            )
        if entries:
            self._scratchpad.clear(session_id)

    def _consolidate_working_memories(self, session_id: str) -> None:
        """Promote WORKING memories to SESSION after task completion."""
        if self._memory_manager is None:
            return
        span = self._tracer.start_span(
            "memory_consolidate",
            trace_id=getattr(self, "_active_trace_id", None),
            attributes={"sessionId": session_id},
        )
        try:
            count = self._memory_manager.consolidate(session_id)
            if count > 0:
                logger.info("Consolidated %d working memories for session %s", count, session_id)
            self._tracer.end_span(span.span_id, status="ok", attributes={"count": count})
        except Exception:  # noqa: BLE001
            logger.debug("Memory consolidation failed for session %s", session_id, exc_info=True)
            self._tracer.end_span(span.span_id, status="error")

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

        # Store in MemoryManager for structured recall
        if self._memory_manager is not None:
            memory_content = self._task_memory_entry(task)
            workspace_id = current_session.get("workspaceId")
            from ..memory.types import MemoryKind
            self._memory_manager.remember(
                session_id=session_id,
                workspace_id=workspace_id,
                content=memory_content,
                kind=MemoryKind.WORKING,
            )

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
        span = self._tracer.start_span("task_pause", trace_id=task.get("id", ""))
        paused_task = self._store.update_task(task_id=task["id"], status="paused")
        self._publish(
            session_id=paused_task["sessionId"],
            task=paused_task,
            event_type="task.paused",
            payload={"status": paused_task["status"], "previousStatus": task["status"]},
        )
        self._tracer.end_span(span.span_id, status="ok")
        return {"task": paused_task}

    def resume_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task = self._store.get_task({"taskId": params["taskId"]})["task"]
        if task["status"] != "paused":
            return {"task": task}

        span = self._tracer.start_span("task_resume", trace_id=task.get("id", ""))

        # Check DAG paused state first
        dag_state = self._load_pending_dag_state(task["id"])
        if dag_state is not None:
            running_task = self._store.update_task_status(task_id=task["id"], status="running")
            self._publish(
                session_id=running_task["sessionId"],
                task=running_task,
                event_type="task.resumed",
                payload={"status": "running", "detail": "Resuming paused DAG execution."},
            )
            resumed_task = self._resume_dag_execution(task=running_task, state=dag_state)
            self._tracer.end_span(span.span_id, status="ok", attributes={"path": "dag"})
            return {"task": resumed_task}

        pending_state = self._load_pending_react_state(task["id"])
        if pending_state is not None:
            # Cooperative pause: no pending tool call → resume ReAct loop directly
            is_cooperative = not pending_state.get("pending_tool_call")
            if is_cooperative:
                running_task = self._store.update_task_status(task_id=task["id"], status="running")
                self._publish(
                    session_id=running_task["sessionId"],
                    task=running_task,
                    event_type="task.resumed",
                    payload={"status": "running", "detail": "Resuming cooperative paused ReAct task."},
                )
                resumed_task = self._resume_cooperative_react(task=running_task, state=pending_state)
                self._tracer.end_span(span.span_id, status="ok", attributes={"path": "react_cooperative"})
                return {"task": resumed_task}

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
                self._tracer.end_span(span.span_id, status="ok", attributes={"path": "react_approved"})
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
                self._tracer.end_span(span.span_id, status="ok", attributes={"path": "react_rejected"})
                return {"task": failed_task}

            waiting_task = self._store.update_task_status(task_id=task["id"], status="waiting_approval")
            self._publish(
                session_id=waiting_task["sessionId"],
                task=waiting_task,
                event_type="task.resumed",
                payload={"status": "waiting_approval", "detail": "Pending ReAct task is waiting for approval."},
            )
            self._tracer.end_span(span.span_id, status="ok", attributes={"path": "react_pending_approval"})
            return {"task": waiting_task}

        running_task = self._store.update_task_status(task_id=task["id"], status="running")
        self._publish(
            session_id=running_task["sessionId"],
            task=running_task,
            event_type="task.resumed",
            payload={"status": running_task["status"]},
        )
        self._tracer.end_span(span.span_id, status="ok", attributes={"path": "fallback"})
        return {"task": running_task}

    def _resume_cooperative_react(self, task: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Resume a cooperatively paused ReAct loop from its saved checkpoint."""
        try:
            react_result = self._run_react_loop(
                session_id=state["session_id"],
                task=task,
                goal=state["goal"],
                context=state["context"],
                state=state,
            )
            if react_result["status"] == "paused":
                return task  # paused again
            if react_result["status"] == "waiting_approval":
                return task
            summary = react_result.get("summary") or self._provider.summarize_findings(
                goal=state["goal"],
                context=state["context"],
                tool_results=react_result.get("tool_results", []),
            )
            return self._complete_task(
                session_id=state["session_id"],
                task=task,
                summary=summary,
                context=state["context"],
                tool_results=react_result.get("tool_results", []),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Cooperative resume failed for task=%s: %s", task["id"], exc, exc_info=True)
            return self._fail_task(
                session_id=state["session_id"],
                task=task,
                summary=str(exc),
                error_code="RESUME_FAILED",
            )

    # ------------------------------------------------------------------
    # DAG pause/resume helpers
    # ------------------------------------------------------------------

    def _load_pending_dag_state(self, task_id: str) -> dict[str, Any] | None:
        if hasattr(self._store, "get_pending_dag_state"):
            return self._store.get_pending_dag_state(task_id)
        return None

    def _clear_pending_dag_state(self, task_id: str) -> None:
        if hasattr(self._store, "delete_pending_dag_state"):
            self._store.delete_pending_dag_state(task_id)

    def _resume_dag_execution(self, task: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Resume a paused DAG execution from its saved checkpoint."""
        try:
            from ..planner.types import PlanResult, Subtask as PlanSubtask

            plan_data = state["plan"]
            subtasks = [
                PlanSubtask(
                    id=s["id"], title=s["title"], description=s["description"],
                    dependencies=s["dependencies"], status=s.get("status", "pending"),
                    result=s.get("result"),
                )
                for s in plan_data["subtasks"]
            ]
            plan = PlanResult(
                subtasks=subtasks,
                dag=plan_data["dag"],
                execution_order=plan_data["execution_order"],
            )

            execution = self._dag_executor.execute(
                plan,
                session_id=state["session_id"],
                parent_task_id=task["id"],
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                completed_ids=set(state["completed"]),
                failed_ids=set(state["failed"]),
                prior_results=state["results"],
                tracer=self._tracer,
            )

            if execution.get("paused"):
                # Paused again — update persisted state
                updated_plan_data = {
                    "subtasks": [
                        {"id": s.id, "title": s.title, "description": s.description,
                         "dependencies": s.dependencies, "status": s.status, "result": s.result}
                        for s in plan.subtasks
                    ],
                    "dag": plan.dag,
                    "execution_order": plan.execution_order,
                }
                if hasattr(self._store, "upsert_pending_dag_state"):
                    self._store.upsert_pending_dag_state(
                        task_id=task["id"],
                        session_id=state["session_id"],
                        goal=state["goal"],
                        context=state["context"],
                        plan_json=json.dumps(updated_plan_data, ensure_ascii=False),
                        completed_ids=execution["completed"],
                        failed_ids=execution["failed"],
                        results=execution.get("results", {}),
                    )
                return task

            # Completed — clean up and finalize
            self._clear_pending_dag_state(task["id"])

            coverage = self._coverage_evaluator.evaluate(state["goal"], execution["subtasks"])
            summary = execution["summary"]

            self._publish(
                session_id=state["session_id"], task=task,
                event_type="task.planning.completed",
                payload={"coverage": coverage, "success": execution["success"]},
            )

            return self._complete_task(
                session_id=state["session_id"],
                task=task,
                summary=summary,
                context=state["context"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("DAG resume failed for task=%s: %s", task["id"], exc, exc_info=True)
            self._clear_pending_dag_state(task["id"])
            return self._fail_task(
                session_id=state["session_id"],
                task=task,
                summary=str(exc),
                error_code="DAG_RESUME_FAILED",
            )

    def _cleanup_orphan_tasks(self) -> None:
        """Reset tasks stuck in running state from a crashed previous process.

        Paused tasks are preserved — they hold persisted state that allows
        resumption after a process restart.
        """
        if not hasattr(self._store, "list_tasks_by_status"):
            return
        # Only clean up running tasks; paused tasks retain their persisted state
        orphaned = self._store.list_tasks_by_status(["running"])
        for task in orphaned:
            self._store.update_task(
                task_id=task["id"],
                status="failed",
                summary="任务因进程重启而中断",
                error_code="ORPHAN_CLEANUP",
            )
            self._publish(
                session_id=task.get("sessionId", ""),
                task=task,
                event_type="task.orphaned",
                payload={"previousStatus": task["status"], "reason": "Process restart"},
            )
        if orphaned:
            logger.info("Cleaned up %d orphan tasks", len(orphaned))

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
        if approval["decision"] == "approved" and approval["kind"] == "plan":
            task = self._resume_approved_plan(task=task, approval=approval)
        if approval["decision"] == "approved" and approval["kind"] == "run_command":
            task = self._resume_approved_command(task=task, approval=approval)
        if approval["decision"] == "approved" and approval["kind"] == "apply_patch":
            task = self._resume_approved_patch(task=task, approval=approval)
        if approval["decision"] == "rejected" and approval["kind"] == "plan":
            task = self._fail_task(
                session_id=task["sessionId"],
                task=task,
                summary="Plan was rejected by the user.",
                error_code="PLAN_REJECTED",
            )
        return {"approval": approval}

    def _should_resume_child_approval_in_process(self, params: dict[str, Any], child_task: dict[str, Any]) -> bool:
        if params.get("_childWorkerApprovalResume") is True:
            return False
        metadata = child_task.get("metadata") if isinstance(child_task.get("metadata"), dict) else {}
        return metadata.get("executionMode") == "process-rpc"

    def _resume_approved_plan(self, task: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        """Resume plan execution after approval, dispatching by orchestration mode."""
        state = self._load_pending_dag_state(task["id"])
        if state is None:
            logger.warning("No pending DAG state for approved plan task=%s", task["id"])
            return task
        mode = state.get("context", {}).get("orchestration_mode", "dag")
        if mode == "supervisor":
            return self._resume_supervisor_execution(task, state)
        if mode == "swarm":
            return self._resume_swarm_execution(task, state)
        return self._resume_dag_execution(task, state)

    def _resume_supervisor_execution(self, task: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Resume supervisor execution after plan approval."""
        session_id = state["session_id"]
        try:
            result = self._supervisor.execute(
                state["goal"], state["context"],
                session_id=session_id, task=task,
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                completed_ids=set(state["completed"]),
                failed_ids=set(state["failed"]),
                prior_results=state["results"],
            )

            if result.paused:
                if hasattr(self._store, "upsert_pending_dag_state"):
                    self._store.upsert_pending_dag_state(
                        task_id=task["id"],
                        session_id=session_id,
                        goal=state["goal"],
                        context=state["context"],
                        plan_json=json.dumps({"subtasks": [], "dag": {}, "execution_order": []}, ensure_ascii=False),
                        completed_ids=list(state["completed"]),
                        failed_ids=list(state["failed"]),
                        results=state["results"],
                    )
                return task

            self._clear_pending_dag_state(task["id"])
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={"mode": "supervisor", "reviews": result.review_count},
            )
            return self._complete_task(
                session_id=session_id, task=task,
                summary=result.summary, context=state["context"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Supervisor resume failed for task=%s: %s", task["id"], exc, exc_info=True)
            self._clear_pending_dag_state(task["id"])
            return self._fail_task(
                session_id=session_id, task=task,
                summary=str(exc), error_code="SUPERVISOR_RESUME_FAILED",
            )

    def _resume_swarm_execution(self, task: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Resume swarm execution after plan approval."""
        session_id = state["session_id"]
        try:
            result = self._swarm.execute(
                state["goal"], state["context"],
                session_id=session_id, task=task,
                is_paused_fn=lambda: self._store.get_task({"taskId": task["id"]})["task"]["status"] == "paused",
                completed_ids=set(state["completed"]),
                failed_ids=set(state["failed"]),
                prior_results=state["results"],
            )

            if result.paused:
                if hasattr(self._store, "upsert_pending_dag_state"):
                    self._store.upsert_pending_dag_state(
                        task_id=task["id"],
                        session_id=session_id,
                        goal=state["goal"],
                        context=state["context"],
                        plan_json=json.dumps({"subtasks": [], "dag": {}, "execution_order": []}, ensure_ascii=False),
                        completed_ids=list(state["completed"]),
                        failed_ids=list(state["failed"]),
                        results=state["results"],
                    )
                return task

            self._clear_pending_dag_state(task["id"])
            self._publish(
                session_id=session_id, task=task,
                event_type="task.planning.completed",
                payload={"mode": "swarm", "handoffs": result.handoff_count},
            )
            return self._complete_task(
                session_id=session_id, task=task,
                summary=result.summary, context=state["context"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Swarm resume failed for task=%s: %s", task["id"], exc, exc_info=True)
            self._clear_pending_dag_state(task["id"])
            return self._fail_task(
                session_id=session_id, task=task,
                summary=str(exc), error_code="SWARM_RESUME_FAILED",
            )

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
            cmd_status = command_result.get("status")
            exit_code = command_result.get("exitCode")

            if cmd_status == "failed":
                summary = f"Command failed with status {cmd_status} and exit code {exit_code}."
                runtime_task["plan"] = self._planner.advance(
                    runtime_task["plan"],
                    "run-command",
                    final_status="failed",
                )
                runtime_task["plan"] = self._planner.advance(
                    runtime_task["plan"],
                    "summarize-findings",
                    final_status="failed",
                )
                failed_task = self._store.update_task(
                    task_id=task["id"],
                    status="failed",
                    plan=runtime_task["plan"],
                    result_summary=summary,
                    error_code="COMMAND_EXECUTION_FAILED",
                )
                runtime_task = {
                    **failed_task,
                    "plan": runtime_task["plan"],
                    "resultSummary": summary,
                }
                self._store.create_message(
                    session_id=task["sessionId"],
                    task_id=runtime_task["id"],
                    role="assistant",
                    content=summary,
                )
                self._publish(
                    session_id=task["sessionId"],
                    task=runtime_task,
                    event_type="task.failed",
                    payload={
                        "status": "failed",
                        "plan": runtime_task["plan"],
                        "detail": summary,
                        "error_code": "COMMAND_EXECUTION_FAILED",
                        "commandLogId": command_log.get("id"),
                    },
                )
                return runtime_task

            summary = (
                f"Approved command finished with status {cmd_status} "
                f"and exit code {exit_code}."
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
        if event_type.startswith("task."):
            payload = dict(payload)
            payload.setdefault("goal", task.get("goal"))
            payload.setdefault("acceptanceCriteria", list(task.get("acceptanceCriteria") or []))
            payload.setdefault("outOfScope", list(task.get("outOfScope") or []))
            payload.setdefault("currentStep", task.get("currentStep"))
        event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id=session_id,
            task_id=task["id"],
            type=event_type,
            ts=self._store.now(),
            payload=payload,
        )
        self._event_bus.publish(event)

    def _publish_mcp_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish an MCP lifecycle event (no task/session context)."""
        event = RuntimeEvent(
            event_id=self._store.new_id("evt"),
            session_id="",
            task_id="",
            type=event_type,
            ts=self._store.now(),
            payload=payload,
        )
        self._event_bus.publish(event)

    def _record_mcp_connect_failure(
        self,
        *,
        span: Any,
        server: dict[str, Any],
        phase: str,
        exc: BaseException,
    ) -> str:
        summary = summarize_mcp_exception(exc)
        server_id = server.get("id", "")
        server_name = server.get("name", "")
        self._tracer.end_span(span.span_id, status="error", attributes={"error": summary})
        logger.warning("Failed to connect MCP server %s: %s", server_id, summary)
        logger.debug("MCP server %s connection traceback", server_id, exc_info=True)
        self._publish_mcp_event(
            "mcp.server.failed",
            {
                "serverId": server_id,
                "serverName": server_name,
                "phase": phase,
                "error": summary,
                "transport": server.get("transport"),
                "command": server.get("command"),
                "url": server.get("url"),
            },
        )
        return summary

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

        root_span = self._tracer.start_span(
            "react_loop",
            attributes={"taskId": task["id"], "goal": goal[:200]},
        )
        self._active_trace_id = root_span.trace_id
        self._active_parent_span_id = root_span.span_id

        try:
            return self._run_react_loop_inner(
                session_id, task, goal, context, state, budget,
                messages, tool_results, steps, max_steps, react_started, patch_repair_attempts,
            )
        except Exception:
            self._tracer.end_span(root_span.span_id, status="error")
            raise
        finally:
            if root_span.status != "error":
                self._tracer.end_span(root_span.span_id, status="ok")
            self._active_trace_id = None
            self._active_parent_span_id = None

    def _run_react_loop_inner(
        self,
        session_id: str,
        task: dict[str, Any],
        goal: str,
        context: dict[str, Any],
        state: dict[str, Any] | None,
        budget: WorkerBudget | None,
        messages: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        steps: int,
        max_steps: int,
        react_started: bool,
        patch_repair_attempts: int,
    ) -> dict[str, Any]:

        # Cache provider tools list — it does not change within the loop
        cached_provider_tools: list[dict[str, Any]] | None = None
        read_file_cache: dict[str, dict[str, Any]] = {}
        # Incremental token tracking — avoids re-estimating all messages every turn
        _msg_token_total: int = sum(estimate_tokens(m.get("content", "")) for m in messages)
        _msg_count_at_last_check: int = len(messages)

        while True:
            # Refresh task status to detect external pause
            task = self._store.get_task({"taskId": task["id"]})["task"]
            if task["status"] == "paused":
                self._pending_react_tasks[task["id"]] = {
                    "session_id": session_id,
                    "goal": goal,
                    "context": context,
                    "messages": messages,
                    "tool_results": tool_results,
                    "steps": steps,
                    "react_started": react_started,
                    "patch_repair_attempts": patch_repair_attempts,
                    "pending_tool_call": None,
                    "pending_tool_spec": None,
                    "remaining_tool_calls": [],
                }
                self._save_pending_react_state(task["id"], self._pending_react_tasks[task["id"]])
                return {"status": "paused"}

            if steps >= max_steps:
                raise RuntimeError(f"Reached maxTaskSteps ({max_steps}) before the provider returned a final answer.")

            # Inject recalled memories into context on first step
            if steps == 0 and self._memory_manager is not None:
                workspace_id = context.get("workspace_id")
                mem_span = self._tracer.start_span(
                    "memory_recall",
                    trace_id=getattr(self, "_active_trace_id", None),
                    parent_span_id=getattr(self, "_active_parent_span_id", None),
                    attributes={"query": goal[:200]},
                )
                try:
                    recalled = self._memory_manager.recall(
                        workspace_id=workspace_id,
                        session_id=session_id,
                        query=goal,
                        limit=5,
                    )
                    if recalled:
                        mem_lines = [f"- {e.content[:200]}" for e in recalled]
                        mem_hint = "[Relevant memories]\n" + "\n".join(mem_lines)
                        messages.append({"role": "system", "content": mem_hint})
                    self._tracer.end_span(mem_span.span_id, status="ok", attributes={"count": len(recalled) if recalled else 0})
                except Exception:  # noqa: BLE001
                    self._tracer.end_span(mem_span.span_id, status="error")

            if cached_provider_tools is None:
                cached_provider_tools = self._provider_tools(context)
            provider_context = {
                **context,
                "messages": messages,
                "tools": cached_provider_tools,
                "openai_tools": context.get("openai_tools") or cached_provider_tools,
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
                    "tool_results": tool_results,
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
                cache_key = self._read_file_cache_key(tool_spec)
                cached_tool_result = read_file_cache.get(cache_key) if cache_key else None
                if cached_tool_result is not None:
                    tool_result = self._clone_cached_tool_result(tool_spec, cached_tool_result)
                else:
                    tool_result = self._execute_tool(
                        session_id=session_id,
                        task=task,
                        tool_spec=tool_spec,
                        budget=budget,
                    )
                    if cache_key and not self._tool_failed(tool_spec["name"], tool_result["result"]):
                        read_file_cache[cache_key] = deepcopy(tool_result)
                    elif self._invalidates_read_file_cache(tool_spec["name"]):
                        read_file_cache.clear()
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
                # Compact messages if token budget is exceeded
                if self._compactor is not None:
                    # Incrementally update token count for new messages only
                    for m in messages[_msg_count_at_last_check:]:
                        _msg_token_total += estimate_tokens(m.get("content", ""))
                    _msg_count_at_last_check = len(messages)
                    if _msg_token_total > 6000:
                        compacted = self._compactor.compact(
                            session_id=session_id,
                            messages=messages,
                            max_tokens=6000,
                        )
                        messages = compacted.kept_messages
                        _msg_token_total = compacted.tokens_after
                        _msg_count_at_last_check = len(messages)
                    context["messages"] = messages
                # Refresh volatile context sections (git status, directory
                # listing) after state-mutating tools so the model sees the
                # current workspace state on subsequent turns.
                if self._context_builder.should_refresh(tool_spec["name"]):
                    context = self._context_builder.refresh_context(
                        context,
                        tool_name=tool_spec["name"],
                        tool_result=tool_result.get("result"),
                    )
                    # Keep the messages list in sync after refresh.
                    context["messages"] = messages
                self._publish_context_update(
                    session_id=session_id,
                    task=task,
                    context=context,
                    messages=messages,
                )
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

            # After all tool calls in this step, check for cooperative pause
            task = self._store.get_task({"taskId": task["id"]})["task"]
            if task["status"] == "paused":
                self._pending_react_tasks[task["id"]] = {
                    "session_id": session_id,
                    "goal": goal,
                    "context": context,
                    "messages": messages,
                    "tool_results": tool_results,
                    "steps": steps,
                    "react_started": react_started,
                    "patch_repair_attempts": patch_repair_attempts,
                    "pending_tool_call": None,
                    "pending_tool_spec": None,
                    "remaining_tool_calls": [],
                }
                self._save_pending_react_state(task["id"], self._pending_react_tasks[task["id"]])
                return {"status": "paused"}

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
            span = self._tracer.start_span(
                "llm_generate",
                trace_id=getattr(self, "_active_trace_id", None),
                parent_span_id=getattr(self, "_active_parent_span_id", None),
            )
            try:
                response = self._provider.generate(goal, provider_context)
            except Exception:
                self._tracer.end_span(span.span_id, status="error")
                raise
            self._tracer.end_span(span.span_id, status="ok")
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
        logger.info(
            "Streaming provider response for task=%s step=%s",
            task["id"], provider_context.get("step"),
        )
        final_response: dict[str, Any] | None = None
        streamed_content = False
        _max_stream_retries = 1
        _stream_text_parts: list[str] = []
        _delta_count = 0
        for _stream_attempt in range(_max_stream_retries + 1):
            final_response = None
            streamed_content = False
            _stream_text_parts = []
            try:
                for event in self._provider.stream(goal, provider_context):
                    event_type = event.get("type")
                    if event_type == "content_delta":
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            streamed_content = True
                            _delta_count += 1
                            if _delta_count <= 3 or _delta_count % 50 == 0:
                                logger.debug(
                                    "Stream delta #%d for task=%s: %r",
                                    _delta_count, task["id"], delta[:80],
                                )
                            # Repetition detection: if the same phrase appears
                            # 3+ times in accumulated output, truncate.
                            _stream_text_parts.append(delta)
                            if self._detect_stream_repetition(_stream_text_parts):
                                logger.warning(
                                    "Stream repetition detected for task=%s, truncating after %d chars",
                                    task["id"],
                                    sum(len(p) for p in _stream_text_parts),
                                )
                                break
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
                logger.info(
                    "Stream completed for task=%s: deltas=%d streamed=%s has_final=%s",
                    task["id"], _delta_count, streamed_content, final_response is not None,
                )
                break  # stream completed successfully
            except Exception as stream_exc:
                from ..provider.openai_compatible import ProviderAdapterError
                is_retryable = isinstance(stream_exc, ProviderAdapterError) and "timed out" in str(stream_exc).lower()
                if is_retryable and _stream_attempt < _max_stream_retries:
                    logger.warning(
                        "Provider stream timed out (attempt %d/%d), retrying: %s",
                        _stream_attempt + 1, _max_stream_retries + 1, stream_exc,
                    )
                    self._append_provider_trace(
                        task=task,
                        event_type="provider.stream.retry",
                        payload={"attempt": _stream_attempt + 1, "error": str(stream_exc)},
                    )
                    continue
                raise

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
            # Use streamed text when the final event's content is empty but
            # tokens were already sent to the frontend via assistant.token.
            final_text = response["message"]
            if not final_text.strip() and streamed_content and _stream_text_parts:
                final_text = "".join(_stream_text_parts)
            response["final"] = final_text
            response["final_answer"] = final_text
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
        if self._streaming_mode_cache is not None:
            return self._streaming_mode_cache
        if not hasattr(self._provider, "stream"):
            self._streaming_mode_cache = False
            return False
        config = provider_context.get("config") or {}
        provider_config = config.get("provider") if isinstance(config, dict) else {}
        if not isinstance(provider_config, dict):
            self._streaming_mode_cache = False
            return False
        mode = str(provider_config.get("mode") or provider_config.get("providerMode") or "").strip().lower()
        result = mode in {"openai", "openai-compatible", "openai_compatible", "openai-compatible-chat"}
        self._streaming_mode_cache = result
        return result

    @staticmethod
    def _detect_stream_repetition(parts: list[str], min_chunk: int = 40, max_occurrences: int = 3) -> bool:
        """Return True when accumulated stream parts show clear repetition loops.

        Scans the full concatenated text for any substring of at least
        *min_chunk* characters that appears *max_occurrences* or more times.
        """
        if len(parts) < max_occurrences:
            return False
        text = "".join(parts)
        length = len(text)
        if length < min_chunk * max_occurrences:
            return False
        # Only check the last portion to keep it O(n) — the repetition
        # pattern, if present, will show up near the tail.
        tail = text[-min(length, 4000):]
        seen: dict[str, int] = {}
        chunk_len = min_chunk
        step = max(chunk_len // 2, 20)
        for start in range(0, len(tail) - chunk_len + 1, step):
            chunk = tail[start : start + chunk_len]
            if not chunk.strip():
                continue
            count = seen.get(chunk, 0) + 1
            seen[chunk] = count
            if count >= max_occurrences:
                return True
        return False

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
            logger.error("Resume after approval failed for task=%s: %s", task["id"], exc, exc_info=True)
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
            return list(messages)
        return [{"role": "user", "content": goal}]

    # Strategies that are allowed to create child tasks via the `task` tool.
    _TASK_TOOL_STRATEGIES: frozenset[str] = frozenset({
        "plan_execute", "plan_supervise", "plan_swarm",
    })

    def _provider_tools(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        tools_by_name: dict[str, dict[str, Any]] = {}
        # 1. Start with context-level tools (built by ContextBuilder)
        openai_tools = context.get("openai_tools")
        if isinstance(openai_tools, list):
            for schema in openai_tools:
                if isinstance(schema, dict) and isinstance(schema.get("name"), str):
                    tools_by_name[schema["name"]] = schema
        for schema in context.get("tools") or []:
            if isinstance(schema, dict) and isinstance(schema.get("name"), str):
                tools_by_name.setdefault(schema["name"], schema)
        # 2. Always merge MCP / dynamically registered tools
        for schema in self._tool_registry.schemas:
            if isinstance(schema, dict) and isinstance(schema.get("name"), str):
                tools_by_name.setdefault(schema["name"], schema)
        # 3. Remove the `task` tool when the routing strategy does not need it,
        #    so that the LLM cannot spontaneously create child tasks for simple queries.
        routing = context.get("routing")
        if isinstance(routing, dict):
            strategy = routing.get("strategy", "")
            if strategy not in self._TASK_TOOL_STRATEGIES:
                tools_by_name.pop("task", None)
        return list(tools_by_name.values())

    def _max_task_steps(self, context: dict[str, Any]) -> int:
        # Prefer routing-level max_steps (scenario-aware) over global config
        routing = context.get("routing")
        if isinstance(routing, dict):
            routing_steps = routing.get("max_steps")
            if routing_steps is not None:
                try:
                    return max(1, int(routing_steps))
                except (TypeError, ValueError):
                    pass
        # Fallback to global config
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
            "write_file",
            "code_search",
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

    def _read_file_cache_key(self, tool_spec: dict[str, Any]) -> str | None:
        if tool_spec.get("name") != "read_file":
            return None
        arguments = tool_spec.get("arguments")
        if not isinstance(arguments, dict):
            return None
        relevant_arguments = {
            key: arguments.get(key)
            for key in ("workspaceRoot", "workspace_root", "path", "encoding", "max_bytes")
            if key in arguments
        }
        return json.dumps(relevant_arguments, sort_keys=True, ensure_ascii=False, default=str)

    def _clone_cached_tool_result(self, tool_spec: dict[str, Any], cached_tool_result: dict[str, Any]) -> dict[str, Any]:
        tool_result = deepcopy(cached_tool_result)
        tool_result["id"] = tool_spec.get("id") or self._store.new_id("tc")
        tool_result["arguments"] = deepcopy(tool_spec.get("arguments", {}))
        result = tool_result.get("result")
        if isinstance(result, dict):
            result["cached"] = True
        return tool_result

    def _invalidates_read_file_cache(self, tool_name: str) -> bool:
        return tool_name in {"apply_patch", "write_file", "run_command", "task"}

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
        tool_span = self._tracer.start_span(
            "tool_call",
            trace_id=getattr(self, "_active_trace_id", None),
            parent_span_id=getattr(self, "_active_parent_span_id", None),
            attributes={"toolName": tool_spec["name"]},
        )
        try:
            if tool_spec["name"] == "task":
                result = self._subagent_service.dispatch(tool_arguments)
            else:
                result = self._tool_registry.execute(tool_spec["name"], tool_arguments, session_id=session_id)
            self._tracer.end_span(tool_span.span_id, status="ok")
        except Exception as exc:  # noqa: BLE001
            result = {
                "status": "failed",
                "ok": False,
                "error": str(exc),
                "summary": f"Tool {tool_spec['name']} raised an exception: {exc}",
            }
            self._tracer.end_span(tool_span.span_id, status="error")
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
