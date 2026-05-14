from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .permission_engine import PermissionEngine, PermissionRequest


RuntimeRole = Literal["root", "worker", "planner", "reviewer", "summarizer"]


READ_ONLY_TOOLS = frozenset(
    {
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
    }
)
WRITE_TOOLS = frozenset({"write_file", "apply_patch", "run_command"})
MEMORY_AND_SCRATCHPAD_TOOLS = frozenset({"memory.recall", "memory.remember", "scratchpad.read", "scratchpad.write"})

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
    "task": "subagents",
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
        for tool in registered_tools:
            name = self._tool_name(tool)
            if not name:
                continue
            detail: dict[str, Any] = {
                "toolName": name,
                "source": self._tool_source(tool, name),
                "phaseDecision": "allowed" if allow_all or name in allowed_names else "denied",
            }
            if allow_all or name in allowed_names:
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

                permission = self._permission_decision(permission_engine, name, context)
                if permission is not None:
                    detail["permissionDecision"] = permission.decision
                    detail["capability"] = permission.capability
                    if permission.approval_kind:
                        detail["approvalKind"] = permission.approval_kind
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
        profile = {
            "agentType": agent_type,
            "baseRuntimeRole": runtime_role,
            "toolPolicy": tool_policy,
            "capabilities": [],
            "scopes": [],
            "riskLevel": "low" if tool_policy in {"read_only", "child_allowlist"} else "medium",
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
        if self._last_task_result_ready(context, tool_results):
            return "synthesis"
        if runtime_role == "summarizer":
            return "synthesis"
        if runtime_role == "reviewer":
            return "review"
        if runtime_role == "planner":
            return "planning"
        if not tool_results:
            routing = context.get("routing")
            strategy = routing.get("strategy") if isinstance(routing, dict) else None
            if strategy in self.TASK_TOOL_STRATEGIES:
                return "planning"
        if self._last_tool_failed(tool_results):
            return "recovery"
        return "investigation"

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
            names &= set(child_allowlist or READ_ONLY_TOOLS)
            reasons["*"] = "child worker tools are limited by child allowlist"

        return names, reasons

    def _last_task_result_ready(self, context: dict[str, Any], tool_results: list[dict[str, Any]]) -> bool:
        if context.get("_allow_tools_after_task_results") is True or not tool_results:
            return False
        last_result = tool_results[-1]
        if last_result.get("name") != "task":
            return False
        result = last_result.get("result")
        return not (isinstance(result, dict) and result.get("status") == "waiting_approval")

    def _last_tool_failed(self, tool_results: list[dict[str, Any]]) -> bool:
        if not tool_results:
            return False
        result = tool_results[-1].get("result")
        return isinstance(result, dict) and result.get("status") in {"failed", "error"}

    def _child_allowlist(self, context: dict[str, Any]) -> list[str] | None:
        for key in ("_child_tool_allowlist", "childToolAllowlist", "child_tool_allowlist"):
            value = context.get(key)
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                return [item for item in value if item]
        budget = context.get("_worker_budget")
        if isinstance(budget, dict):
            for key in ("childToolAllowlist", "child_tool_allowlist", "toolAllowlist", "tool_allowlist"):
                value = budget.get(key)
                if isinstance(value, list) and all(isinstance(item, str) for item in value):
                    return [item for item in value if item]
        return None

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
    ) -> Any | None:
        if permission_engine is None:
            return None
        capability = self._tool_capability(tool_name)
        if capability is None:
            return None
        return permission_engine.evaluate(PermissionRequest(
            capability=capability,
            tool_name=tool_name,
            context=context,
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
        if tool_name in MEMORY_AND_SCRATCHPAD_TOOLS:
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
