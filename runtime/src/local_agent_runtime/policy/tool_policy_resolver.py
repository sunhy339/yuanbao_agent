from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .permission_engine import PermissionEngine, PermissionRequest, collect_untrusted_content_signals


RuntimeRole = Literal["root", "worker", "planner", "reviewer", "summarizer"]


READ_ONLY_TOOL_NAMES = (
        "list_dir",
        "search_files",
        "read_file",
        "git_status",
        "git_diff",
        "code_search",
        "web_fetch",
        "browser",
        "memory.recall",
        "scratchpad.read",
)
PLAN_MODE_TOOL_NAMES = (*READ_ONLY_TOOL_NAMES, "exit_plan_mode")
LOCAL_READ_ONLY_TOOL_NAMES = (
    "list_dir",
    "search_files",
    "read_file",
    "git_status",
    "git_diff",
    "code_search",
)
READ_ONLY_TOOLS = frozenset(
    READ_ONLY_TOOL_NAMES
)
WRITE_TOOLS = frozenset({"write_file", "apply_patch", "run_command"})
MEMORY_AND_SCRATCHPAD_TOOLS = frozenset({"memory.recall", "memory.remember", "scratchpad.read", "scratchpad.write"})
CONTROL_FLOW_TOOL_NAMES = frozenset({"ask_user_question", "enter_plan_mode", "exit_plan_mode"})
VERIFICATION_COMMAND_MARKERS = (
    "pytest",
    "unittest",
    "npm test",
    "npm run test",
    "pnpm test",
    "yarn test",
    "cargo test",
    "go test",
    "mvn test",
    "gradle test",
    " tsc",
    "tsc ",
    "npm run build",
    "pnpm build",
    "yarn build",
    "cmake",
    "cmake --build",
    "ctest",
    "ninja",
    "ninja test",
    "make test",
)

TOOL_CAPABILITIES: dict[str, str] = {
    "list_dir": "readFile",
    "search_files": "readFile",
    "read_file": "readFile",
    "git_status": "readFile",
    "git_diff": "readFile",
    "code_search": "readFile",
    "write_file": "writeFile",
    "apply_patch": "writeFile",
    "run_command": "runCommand",
    "web_fetch": "webFetch",
    "browser": "browserAutomation",
    "computer_use": "computerUse",
    "task": "subagents",
    "notebook": "runCommand",
    "memory.remember": "memoryWrite",
    "memory.recall": "readFile",
    "scratchpad.write": "memoryWrite",
    "scratchpad.read": "readFile",
}


@dataclass(slots=True)
class ToolPolicyDecision:
    phase: str
    runtime_role: str
    agent_type: str
    allowed_tools: list[dict[str, Any]]
    allowed_tool_names: list[str]
    denied_tool_names: list[str]
    reasons: dict[str, str]
    decision_details: list[dict[str, Any]]
    role_snapshot: dict[str, Any]
    policy_version: str = "tool-policy-v2"

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "runtimeRole": self.runtime_role,
            "agentType": self.agent_type,
            "allowedToolNames": self.allowed_tool_names,
            "deniedToolNames": self.denied_tool_names,
            "reasons": self.reasons,
            "decisionDetails": self.decision_details,
            "policyVersion": self.policy_version,
        }


class ToolPolicyResolver:
    """Resolve the tools visible to the model for one provider turn."""

    TASK_TOOL_STRATEGIES = frozenset({"plan_execute", "plan_supervise", "plan_swarm"})
    RUNTIME_ROLES = frozenset({"root", "worker", "planner", "reviewer", "summarizer"})

    def resolve(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
        registered_tools: list[dict[str, Any]],
    ) -> ToolPolicyDecision:
        role_snapshot = self.role_snapshot(task=task, context=context)
        runtime_role = str(role_snapshot["runtimeRole"])
        agent_type = str(role_snapshot["agentType"])
        phase = self.phase(task=task, context=context, tool_results=tool_results, runtime_role=runtime_role)

        allowed_names, reasons = self._allowed_names_for_phase_and_role(
            phase=phase,
            runtime_role=runtime_role,
            context=context,
        )
        allowed_tools: list[dict[str, Any]] = []
        denied_names: list[str] = []
        decision_details: list[dict[str, Any]] = []
        allow_all = "*" in allowed_names
        permission_engine = self._permission_engine(context)
        skill_policy = self._skill_policy(context)
        mcp_policy = self._mcp_policy(context)
        untrusted_content_signals = self.untrusted_content_signals(context=context, tool_results=tool_results)
        for tool in registered_tools:
            name = self._tool_name(tool)
            if not name:
                continue
            phase_allowed = allow_all or name in allowed_names or (name.startswith("mcp__") and "mcp__*" in allowed_names)
            detail: dict[str, Any] = {
                "toolName": name,
                "source": self._tool_source(tool, name),
                "phaseDecision": "allowed" if phase_allowed else "denied",
            }
            if phase_allowed:
                if name == "task":
                    detail["toolContinuationPolicy"] = self.tool_continuation_policy(context)
                continuation_reason = self._task_tool_continuation_block_reason(name, context, tool_results)
                if continuation_reason:
                    denied_names.append(name)
                    reasons[name] = continuation_reason
                    detail["continuationDecision"] = "denied"
                    detail["finalDecision"] = "denied"
                    detail["reason"] = continuation_reason
                    decision_details.append(detail)
                    continue

                skill_allowed, skill_reason = self._skill_allows_tool(name, skill_policy)
                detail["skillDecision"] = "allowed" if skill_allowed else "denied"
                if not skill_allowed:
                    denied_names.append(name)
                    reasons[name] = skill_reason
                    detail["finalDecision"] = "denied"
                    detail["reason"] = skill_reason
                    decision_details.append(detail)
                    continue

                mcp_allowed, mcp_reason = self._mcp_allows_tool(name, mcp_policy)
                detail["mcpDecision"] = "allowed" if mcp_allowed else "denied"
                if not mcp_allowed:
                    denied_names.append(name)
                    reasons[name] = mcp_reason
                    detail["finalDecision"] = "denied"
                    detail["reason"] = mcp_reason
                    decision_details.append(detail)
                    continue

                permission = self._permission_decision(
                    permission_engine,
                    name,
                    context,
                    untrusted_content_signals=untrusted_content_signals,
                )
                if permission is not None:
                    detail["permissionDecision"] = permission.decision
                    detail["capability"] = permission.capability
                    if permission.approval_kind:
                        detail["approvalKind"] = permission.approval_kind
                if untrusted_content_signals:
                    detail["untrustedContentSignals"] = untrusted_content_signals
                if permission is not None and permission.decision == "deny":
                    denied_names.append(name)
                    reason = permission.reason or f"tool {name} denied by PermissionEngine"
                    reasons[name] = reason
                    detail["finalDecision"] = "denied"
                    detail["reason"] = reason
                    decision_details.append(detail)
                    continue

                allowed_tools.append(tool)
                detail["finalDecision"] = "allowed"
                if permission is not None and permission.decision == "approval_required":
                    detail["requiresApproval"] = True
            else:
                denied_names.append(name)
                reason = self._denied_reason(name, phase, runtime_role)
                reasons.setdefault(name, reason)
                detail["finalDecision"] = "denied"
                detail["reason"] = reason
            decision_details.append(detail)

        return ToolPolicyDecision(
            phase=phase,
            runtime_role=runtime_role,
            agent_type=agent_type,
            allowed_tools=allowed_tools,
            allowed_tool_names=[self._tool_name(t) for t in allowed_tools if self._tool_name(t)],
            denied_tool_names=denied_names,
            reasons=reasons,
            decision_details=decision_details,
            role_snapshot=role_snapshot,
        )

    def untrusted_content_signals(self, *, context: dict[str, Any], tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged_context = dict(context)
        merged_context["tool_results"] = tool_results
        return collect_untrusted_content_signals(merged_context)

    def role_snapshot(self, *, task: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        routing = context.get("routing")
        if not isinstance(routing, dict):
            routing = task.get("routing") if isinstance(task.get("routing"), dict) else {}
        existing = routing.get("roleSnapshot") if isinstance(routing, dict) else None
        if isinstance(existing, dict):
            return dict(existing)

        raw_role = (
            context.get("runtimeRole")
            or routing.get("runtimeRole")
            or task.get("role")
            or "root"
        )
        runtime_role = self._normalize_runtime_role(raw_role)
        raw_agent_type = context.get("agentType") or routing.get("agentType") or runtime_role
        agent_type = str(raw_agent_type).strip() or runtime_role
        tool_policy = "read_only" if runtime_role in {"reviewer", "summarizer"} else "default"
        child_allowlist = self._child_allowlist(context)
        if child_allowlist is not None:
            tool_policy = "child_allowlist"
        child_can_write = bool(child_allowlist is not None and set(child_allowlist) & WRITE_TOOLS)
        profile = {
            "agentType": agent_type,
            "baseRuntimeRole": runtime_role,
            "toolPolicy": tool_policy,
            "capabilities": ["workspace_write"] if child_can_write else [],
            "scopes": [],
            "riskLevel": "medium" if child_can_write else ("low" if tool_policy in {"read_only", "child_allowlist"} else "medium"),
            "source": "runtime_default",
            "version": 1,
        }
        if isinstance(routing.get("agentProfile"), dict):
            profile.update(routing["agentProfile"])
        return {
            "runtimeRole": runtime_role,
            "agentType": agent_type,
            "profileId": profile.get("id"),
            "profileVersion": profile.get("version", 1),
            "agentProfile": profile,
            "toolPolicy": tool_policy,
            "scopes": profile.get("scopes", []),
            "riskLevel": profile.get("riskLevel", "medium"),
            "budget": context.get("_worker_budget") or {},
        }

    def phase(
        self,
        *,
        task: dict[str, Any],
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
        runtime_role: str,
    ) -> str:
        if task.get("status") == "waiting_approval":
            return "approval_waiting"
        if context.get("_plan_mode") is True:
            return "plan_mode"
        if self._last_task_result_ready(context, tool_results):
            return "synthesis"
        if self._last_ready_task_result(tool_results) and self._allow_tools_after_task_results(context):
            if self._last_tool_failed(tool_results):
                return "recovery"
            return "post_task_continuation"
        if runtime_role == "summarizer":
            return "synthesis"
        if runtime_role == "reviewer":
            return "review"
        if runtime_role == "planner":
            return "planning"
        if runtime_role == "worker" and self._child_worker_execution_enabled(context):
            if self._child_worker_verified_by_command(tool_results):
                return "synthesis"
            return "execution"
        if not tool_results:
            routing = context.get("routing")
            strategy = routing.get("strategy") if isinstance(routing, dict) else None
            if strategy in self.TASK_TOOL_STRATEGIES:
                return "planning"
        if self._last_tool_failed(tool_results):
            return "recovery"
        return "investigation"

    def _child_worker_execution_enabled(self, context: dict[str, Any]) -> bool:
        if context.get("_child_worker") is not True:
            return False
        child_allowlist = self._child_allowlist(context)
        if child_allowlist is None:
            return False
        return bool(set(child_allowlist) & WRITE_TOOLS)

    def _child_worker_verified_by_command(self, tool_results: list[dict[str, Any]]) -> bool:
        if not tool_results:
            return False
        last_result = tool_results[-1]
        if last_result.get("name") != "run_command":
            return False
        result = last_result.get("result")
        if not isinstance(result, dict):
            return False
        if result.get("status") != "completed" or result.get("exitCode") != 0:
            return False
        command = self._run_command_text(result)
        if not command:
            return False
        command_lower = f" {command.lower()} "
        return any(marker in command_lower for marker in VERIFICATION_COMMAND_MARKERS)

    def _run_command_text(self, result: dict[str, Any]) -> str:
        command = result.get("command")
        if isinstance(command, str):
            return command
        command_log = result.get("commandLog")
        if isinstance(command_log, dict) and isinstance(command_log.get("command"), str):
            return command_log["command"]
        return ""

    def _allowed_names_for_phase_and_role(
        self,
        *,
        phase: str,
        runtime_role: str,
        context: dict[str, Any],
    ) -> tuple[set[str], dict[str, str]]:
        reasons: dict[str, str] = {}
        if phase in {"synthesis", "approval_waiting"}:
            return set(), reasons
        if phase == "plan_mode":
            return set(PLAN_MODE_TOOL_NAMES), reasons

        child_allowlist = self._child_allowlist(context)
        if runtime_role in {"root", "worker"} and child_allowlist is None and context.get("_child_worker") is not True:
            return {"*"}, reasons

        names = set(READ_ONLY_TOOLS)
        routing = context.get("routing")
        strategy = routing.get("strategy") if isinstance(routing, dict) else None
        if phase == "planning" and runtime_role in {"root", "planner"} and strategy in self.TASK_TOOL_STRATEGIES:
            names.add("task")

        if phase in {"execution", "recovery"} and runtime_role in {"root", "worker"}:
            names.update(WRITE_TOOLS)

        if runtime_role in {"reviewer", "summarizer"}:
            names &= READ_ONLY_TOOLS

        if child_allowlist is not None or context.get("_child_worker") is True:
            child_names = set(child_allowlist or READ_ONLY_TOOLS)
            allow_mcp = "mcp__*" in child_names
            names &= {name for name in child_names if name != "mcp__*"}
            if allow_mcp:
                names.add("mcp__*")
            reasons["*"] = "child worker tools are limited by child allowlist"

        return names, reasons

    def _last_task_result_ready(self, context: dict[str, Any], tool_results: list[dict[str, Any]]) -> bool:
        if self._allow_tools_after_task_results(context) or not tool_results:
            return False
        return self._last_ready_task_result(tool_results)

    def _last_ready_task_result(self, tool_results: list[dict[str, Any]]) -> bool:
        if not tool_results:
            return False
        last_result = tool_results[-1]
        if last_result.get("name") != "task":
            return False
        result = last_result.get("result")
        return not (isinstance(result, dict) and result.get("status") == "waiting_approval")

    def allow_tools_after_task_results(self, context: dict[str, Any]) -> bool:
        return self._allow_tools_after_task_results(context)

    def tool_continuation_policy(self, context: dict[str, Any]) -> dict[str, Any]:
        return self._tool_continuation_policy(context)

    def _allow_tools_after_task_results(self, context: dict[str, Any]) -> bool:
        return bool(self._tool_continuation_policy(context).get("allowToolsAfterTaskResults"))

    def _tool_continuation_policy(self, context: dict[str, Any]) -> dict[str, Any]:
        for key in ("_allow_tools_after_task_results", "allowToolsAfterTaskResults", "allow_tools_after_task_results"):
            value = context.get(key)
            if isinstance(value, bool):
                return {
                    "allowToolsAfterTaskResults": value,
                    "source": key,
                }
        routing = context.get("routing")
        if isinstance(routing, dict):
            continuation = routing.get("toolContinuation") or routing.get("tool_continuation")
            if isinstance(continuation, dict):
                for key in ("allowToolsAfterTaskResults", "allow_tools_after_task_results"):
                    value = continuation.get(key)
                    if isinstance(value, bool):
                        return {
                            "allowToolsAfterTaskResults": value,
                            "allowMoreSubtasksAfterTaskResults": (
                                continuation.get("allowMoreSubtasksAfterTaskResults") is True
                                or continuation.get("allow_more_subtasks_after_task_results") is True
                            ),
                            "maxTaskToolCalls": self._max_task_tool_calls(context),
                            "source": str(continuation.get("source") or f"routing.toolContinuation.{key}"),
                            "rationale": continuation.get("rationale"),
                        }
            for key in ("allowToolsAfterTaskResults", "allow_tools_after_task_results"):
                value = routing.get(key)
                if isinstance(value, bool):
                    return {
                        "allowToolsAfterTaskResults": value,
                        "source": f"routing.{key}",
                    }
            strategy = routing.get("strategy")
            if isinstance(strategy, str) and strategy in self.TASK_TOOL_STRATEGIES:
                return {
                    "allowToolsAfterTaskResults": True,
                    "source": "strategy_fallback",
                    "rationale": "legacy planning strategy fallback",
                }
        return {"allowToolsAfterTaskResults": False, "source": "default"}

    def _allow_more_subtasks_after_task_results(self, context: dict[str, Any]) -> bool:
        if context.get("_allow_more_subtasks_after_task_results") is True:
            return True
        if context.get("allowMoreSubtasksAfterTaskResults") is True or context.get("allow_more_subtasks_after_task_results") is True:
            return True
        routing = context.get("routing")
        if isinstance(routing, dict):
            if routing.get("allowMoreSubtasksAfterTaskResults") is True or routing.get("allow_more_subtasks_after_task_results") is True:
                return True
            continuation = routing.get("toolContinuation") or routing.get("tool_continuation")
            if isinstance(continuation, dict):
                return (
                    continuation.get("allowMoreSubtasksAfterTaskResults") is True
                    or continuation.get("allow_more_subtasks_after_task_results") is True
                )
        return False

    def _max_task_tool_calls(self, context: dict[str, Any]) -> int | None:
        raw_values: list[Any] = [
            context.get("_max_task_tool_calls"),
            context.get("maxTaskToolCalls"),
            context.get("max_task_tool_calls"),
        ]
        routing = context.get("routing")
        if isinstance(routing, dict):
            raw_values.extend([
                routing.get("maxTaskToolCalls"),
                routing.get("max_task_tool_calls"),
            ])
            continuation = routing.get("toolContinuation") or routing.get("tool_continuation")
            if isinstance(continuation, dict):
                raw_values.extend([
                    continuation.get("maxTaskToolCalls"),
                    continuation.get("max_task_tool_calls"),
                ])
        for raw in raw_values:
            if raw is None:
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    def _ready_task_result_count(self, tool_results: list[dict[str, Any]]) -> int:
        count = 0
        for tool_result in tool_results:
            if tool_result.get("name") != "task":
                continue
            result = tool_result.get("result")
            if isinstance(result, dict) and result.get("status") == "waiting_approval":
                continue
            count += 1
        return count

    def _task_tool_continuation_block_reason(
        self,
        tool_name: str,
        context: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> str | None:
        if tool_name != "task" or not self._allow_tools_after_task_results(context):
            return None
        completed_task_calls = self._ready_task_result_count(tool_results)
        if completed_task_calls <= 0:
            return None
        max_calls = self._max_task_tool_calls(context)
        if max_calls is not None:
            if completed_task_calls < max_calls:
                return None
            return f"task tool budget exhausted after {completed_task_calls}/{max_calls} completed child task result(s)"
        if self._allow_more_subtasks_after_task_results(context):
            return None
        return "task tool is withheld after a child result; parent may continue with non-task tools"

    def _last_tool_failed(self, tool_results: list[dict[str, Any]]) -> bool:
        if not tool_results:
            return False
        result = tool_results[-1].get("result")
        return isinstance(result, dict) and result.get("status") in {"failed", "error"}

    def _child_allowlist(self, context: dict[str, Any]) -> list[str] | None:
        for key in ("_child_tool_allowlist", "childToolAllowlist", "child_tool_allowlist"):
            value = context.get(key)
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                return self._expand_child_allowlist([item for item in value if item])
        budget = context.get("_worker_budget")
        if isinstance(budget, dict):
            for key in ("childToolAllowlist", "child_tool_allowlist", "toolAllowlist", "tool_allowlist"):
                value = budget.get(key)
                if isinstance(value, list) and all(isinstance(item, str) for item in value):
                    return self._expand_child_allowlist([item for item in value if item])
        return None

    def _expand_child_allowlist(self, names: list[str]) -> list[str]:
        mcp_wildcard = "mcp__*" in names
        base_names = [name for name in names if name != "mcp__*"]
        if not (set(base_names) & WRITE_TOOLS):
            return names
        expanded: list[str] = []
        seen: set[str] = set()
        for name in [*LOCAL_READ_ONLY_TOOL_NAMES, *base_names]:
            if name in seen:
                continue
            seen.add(name)
            expanded.append(name)
        if mcp_wildcard and "mcp__*" not in seen:
            expanded.append("mcp__*")
        return expanded

    def _normalize_runtime_role(self, value: Any) -> str:
        role = str(value).strip().lower() if value is not None else "root"
        if role in self.RUNTIME_ROLES:
            return role
        return "worker"

    def _tool_name(self, tool: dict[str, Any]) -> str | None:
        name = tool.get("name")
        if isinstance(name, str):
            return name
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return function["name"]
        return None

    def _denied_reason(self, name: str, phase: str, runtime_role: str) -> str:
        if phase in {"synthesis", "approval_waiting"}:
            return f"phase={phase} exposes no tools"
        if runtime_role in {"reviewer", "summarizer"} and name not in READ_ONLY_TOOLS:
            return f"runtimeRole={runtime_role} is read-only"
        return f"tool not allowed for phase={phase} runtimeRole={runtime_role}"

    def _permission_engine(self, context: dict[str, Any]) -> PermissionEngine | None:
        config = context.get("config")
        if not isinstance(config, dict):
            return None
        return PermissionEngine(config)

    def _permission_decision(
        self,
        permission_engine: PermissionEngine | None,
        tool_name: str,
        context: dict[str, Any],
        *,
        untrusted_content_signals: list[dict[str, Any]] | None = None,
    ) -> Any | None:
        if permission_engine is None:
            return None
        capability = self._tool_capability(tool_name)
        if capability is None:
            return None
        permission_context = dict(context)
        if untrusted_content_signals:
            permission_context["untrustedContentSignals"] = [dict(item) for item in untrusted_content_signals]
        return permission_engine.evaluate(PermissionRequest(
            capability=capability,
            tool_name=tool_name,
            context=permission_context,
        ))

    def _tool_capability(self, tool_name: str) -> str | None:
        if tool_name.startswith("mcp__"):
            return None
        return TOOL_CAPABILITIES.get(tool_name)

    def _skill_policy(self, context: dict[str, Any]) -> dict[str, Any] | None:
        policy = context.get("skillPolicy") or context.get("skill_policy")
        if isinstance(policy, dict):
            return policy
        meta = (context.get("_build_result") or context).get("snapshot_metadata")
        if isinstance(meta, dict):
            policy = meta.get("skillPolicy") or meta.get("skill_policy")
            if isinstance(policy, dict):
                return policy
        return None

    def _skill_allows_tool(self, tool_name: str, policy: dict[str, Any] | None) -> tuple[bool, str]:
        if not policy:
            return True, ""
        mode = str(policy.get("toolPolicy") or policy.get("tool_policy") or "strict_whitelist")
        whitelist = {
            str(item)
            for item in (policy.get("toolWhitelist") or policy.get("tool_whitelist") or [])
            if isinstance(item, str) and item
        }
        if mode == "inherit_all":
            return True, ""
        if tool_name in MEMORY_AND_SCRATCHPAD_TOOLS or tool_name in CONTROL_FLOW_TOOL_NAMES:
            return True, ""
        if mode == "inherit_mcp" and tool_name.startswith("mcp__"):
            return True, ""
        if tool_name in whitelist:
            return True, ""
        skill_id = policy.get("skillId") or policy.get("skill_id") or "<unknown>"
        return False, f"skill={skill_id} policy={mode} does not allow tool {tool_name}"

    def _mcp_policy(self, context: dict[str, Any]) -> dict[str, Any] | None:
        policy = context.get("mcpPolicy") or context.get("mcp_policy")
        if isinstance(policy, dict):
            return policy
        config = context.get("config")
        if isinstance(config, dict):
            for key in ("mcpPolicy", "mcp_policy"):
                policy = config.get(key)
                if isinstance(policy, dict):
                    return policy
            mcp = config.get("mcp")
            if isinstance(mcp, dict):
                policy = mcp.get("policy") or mcp.get("serverPolicy") or mcp.get("server_policy")
                if isinstance(policy, dict):
                    return policy
        return None

    def _mcp_allows_tool(self, tool_name: str, policy: dict[str, Any] | None) -> tuple[bool, str]:
        if not tool_name.startswith("mcp__") or not policy:
            return True, ""
        mode = str(policy.get("mode") or "allow")
        if mode in {"disabled", "blocked", "deny"}:
            return False, "MCP tools are disabled by MCP policy"
        server_id, raw_tool = self._mcp_parts(tool_name)
        blocked_servers = self._string_set(policy, "blockedServers", "serverDenylist", "blocked_servers", "server_denylist")
        if server_id in blocked_servers:
            return False, f"MCP server {server_id!r} is blocked by MCP policy"
        allowed_servers = self._string_set(policy, "allowedServers", "serverAllowlist", "allowed_servers", "server_allowlist")
        if allowed_servers and server_id not in allowed_servers:
            return False, f"MCP server {server_id!r} is not in MCP server allowlist"
        blocked_tools = self._string_set(policy, "blockedTools", "toolDenylist", "blocked_tools", "tool_denylist")
        if tool_name in blocked_tools or raw_tool in blocked_tools:
            return False, f"MCP tool {tool_name!r} is blocked by MCP policy"
        allowed_tools = self._string_set(policy, "allowedTools", "toolAllowlist", "allowed_tools", "tool_allowlist")
        if allowed_tools and tool_name not in allowed_tools and raw_tool not in allowed_tools:
            return False, f"MCP tool {tool_name!r} is not in MCP tool allowlist"
        return True, ""

    def _mcp_parts(self, tool_name: str) -> tuple[str, str]:
        parts = tool_name.split("__", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
        return "", tool_name

    def _string_set(self, policy: dict[str, Any], *keys: str) -> set[str]:
        values: set[str] = set()
        for key in keys:
            raw = policy.get(key)
            if isinstance(raw, list):
                values.update(str(item) for item in raw if isinstance(item, str) and item)
        return values

    def _tool_source(self, tool: dict[str, Any], tool_name: str) -> str:
        if tool_name.startswith("mcp__") or tool.get("_mcp_server_id"):
            return "mcp"
        return "builtin"
