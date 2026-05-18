"""React Tool Helpers Mixin — extracted from ReactRunnerMixin.

Handles tool failure detection, tool spec construction, defaults,
cache management, and tool result formatting.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


class ReactToolHelpersMixin:
    """Mixin providing tool-related helper methods for the ReAct loop."""

    def _tool_failed(self, tool_name: str, result: dict[str, Any]) -> bool:
        status = result.get("status")
        return status in {"failed", "error", "timeout", "killed", "partial"} or result.get("ok") is False

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
        if tool_name in workspace_tools:
            arguments.pop("workspace_root", None)
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
            timeout_ms_getter = getattr(self, "_child_subtask_timeout_ms", None)
            if callable(timeout_ms_getter) and "timeoutMs" not in arguments:
                timeout_ms = timeout_ms_getter(context)
                if timeout_ms is not None:
                    arguments["timeoutMs"] = timeout_ms

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

    def _tool_result_message(self, tool_call: dict[str, Any], tool_result: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call.get("id") or tool_result["id"],
            "name": tool_result["name"],
            "content": json.dumps(tool_result["result"], ensure_ascii=False),
        }
