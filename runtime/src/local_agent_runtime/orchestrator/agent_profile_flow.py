"""Agent Profile Flow Mixin — RPC methods for agent profile management."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AgentProfileFlowMixin:
    """Mixin providing agent profile management RPC methods."""

    def agent_profile_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.list_agent_profiles(params)

    def agent_profile_create(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.create_agent_profile(params)

    def agent_profile_update(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.update_agent_profile(params)

    def agent_profile_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._store.delete_agent_profile(params)

    def agent_profile_validate(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate agent profile fields without persisting."""
        errors: list[str] = []
        name = params.get("name")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            errors.append("name must be a non-empty string")

        role = params.get("role")
        valid_roles = {"planner", "builder", "reviewer", "researcher", "custom"}
        if role is not None and role not in valid_roles:
            errors.append(f"Invalid role: {role}. Valid: {', '.join(sorted(valid_roles))}")

        tool_policy = params.get("toolPolicy")
        if tool_policy is not None:
            if not isinstance(tool_policy, dict):
                errors.append("toolPolicy must be an object")
            else:
                for key in ("allowedTools", "deniedTools", "requiresApproval"):
                    val = tool_policy.get(key)
                    if val is not None and not isinstance(val, list):
                        errors.append(f"toolPolicy.{key} must be an array")

        return {"valid": len(errors) == 0, "errors": errors}

    def agent_profile_preview_tools(self, params: dict[str, Any]) -> dict[str, Any]:
        """Preview which tools would be allowed/denied for a given profile config."""
        from ..policy.tool_policy_resolver import ToolPolicyResolver

        resolver = ToolPolicyResolver()
        role = params.get("role", "worker")
        permission_mode = params.get("permissionMode")

        # Build a synthetic context and task to resolve tool policy
        context: dict[str, Any] = {"runtimeRole": role}
        if permission_mode:
            context["permissionMode"] = permission_mode

        tool_policy = params.get("toolPolicy")
        skill_policy = self._skill_policy_from_profile_tool_policy(tool_policy)
        if skill_policy:
            context["skillPolicy"] = skill_policy

        # Get registered tools from tool registry
        registered_tools: list[dict[str, Any]] = []
        if hasattr(self, "_tool_registry") and self._tool_registry is not None:
            if hasattr(self._tool_registry, "get_tool_schemas"):
                schemas = self._tool_registry.get_tool_schemas()
            else:
                schemas = getattr(self._tool_registry, "schemas", [])
            if isinstance(schemas, list):
                registered_tools = schemas

        task: dict[str, Any] = {
            "id": "_preview",
            "status": "running",
            "role": role,
            "routing": {},
        }

        decision = resolver.resolve(
            task=task,
            context=context,
            tool_results=[],
            registered_tools=registered_tools,
        )
        allowed_tools, denied_tools = self._apply_profile_tool_policy(
            decision.allowed_tool_names,
            decision.denied_tool_names,
            tool_policy,
        )

        return {
            "allowedTools": allowed_tools,
            "deniedTools": denied_tools,
        }

    def _skill_policy_from_profile_tool_policy(self, tool_policy: Any) -> dict[str, Any] | None:
        if not isinstance(tool_policy, dict):
            return None
        if "toolWhitelist" in tool_policy or "tool_whitelist" in tool_policy:
            return tool_policy
        allowed_tools = tool_policy.get("allowedTools") or tool_policy.get("allowed_tools")
        if isinstance(allowed_tools, list):
            return {
                "skillId": "agent_profile_preview",
                "toolPolicy": "strict_whitelist",
                "toolWhitelist": [item for item in allowed_tools if isinstance(item, str) and item],
            }
        return None

    def _apply_profile_tool_policy(
        self,
        allowed_tools: list[str],
        denied_tools: list[str],
        tool_policy: Any,
    ) -> tuple[list[str], list[str]]:
        if not isinstance(tool_policy, dict):
            return allowed_tools, denied_tools
        denied = list(dict.fromkeys(denied_tools))
        allowed = list(dict.fromkeys(allowed_tools))
        denied_policy = {
            item
            for item in (tool_policy.get("deniedTools") or tool_policy.get("denied_tools") or [])
            if isinstance(item, str) and item
        }
        if denied_policy:
            allowed = [name for name in allowed if name not in denied_policy]
            denied = list(dict.fromkeys([*denied, *sorted(denied_policy)]))
        return allowed, denied
