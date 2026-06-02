"""React Tool Helpers Mixin — extracted from ReactRunnerMixin.

Handles tool failure detection, tool spec construction, defaults,
cache management, and tool result formatting.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..execution.tool_pipeline import is_verification_command


_CONTEXT_DISCOVERY_TOOL_NAMES = frozenset(
    {"search_files", "code_search", "list_dir", "list_directory", "git_status", "git_diff"}
)
_FILE_CHANGE_TOOL_NAMES = frozenset({"apply_patch", "write_file"})
_GIT_STATUS_TOOL_NAME = "git_status"
_GIT_DIFF_TOOL_NAME = "git_diff"
_SCRATCHPAD_WRITE_TOOL_NAME = "scratchpad.write"
_WEB_FETCH_TOOL_NAME = "web_fetch"
_BROWSER_TOOL_NAME = "browser"
_NOTEBOOK_TOOL_NAME = "notebook"
_COMPUTER_USE_TOOL_NAME = "computer_use"
_NOTEBOOK_LIST_ACTION = "list_cells"
_NOTEBOOK_DETAIL_ACTIONS = frozenset({"get_cell", "execute_cell"})
_COMPUTER_USE_BROWSER_OBSERVE_ACTIONS = frozenset({"inspect", "screenshot"})
_COMPUTER_USE_BROWSER_FOLLOW_UP_ACTIONS = frozenset({"click", "type", "key", "scroll"})
_STRUCTURED_TOOL_NAMES = frozenset(
    {
        "read_file",
        "run_command",
        "task",
        "scratchpad.read",
        "memory.remember",
        "memory.recall",
        _WEB_FETCH_TOOL_NAME,
        _BROWSER_TOOL_NAME,
        _NOTEBOOK_TOOL_NAME,
        _COMPUTER_USE_TOOL_NAME,
    }
) | _CONTEXT_DISCOVERY_TOOL_NAMES | _FILE_CHANGE_TOOL_NAMES | frozenset({_SCRATCHPAD_WRITE_TOOL_NAME})
_TOOL_OPERATION_LABELS: dict[str, str] = {
    "context": "读取上下文",
    "file_change": "文件改动",
    "git": "Git 检查",
    "memory": "记忆",
    "mcp": "MCP",
    "notebook": "Notebook",
    "scratchpad": "记忆",
    "verification": "验证",
    "web": "网页读取",
    "computer_use": "桌面操作",
}


def _compact_operation_key(value: Any, limit: int = 80) -> str:
    text = str(value or "").strip().replace("\\", "/").lower()
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


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
        tool_spec = {
            "id": tool_call.get("id"),
            "name": tool_name,
            "arguments": arguments,
            "plan_step_id": self._plan_step_for_tool(tool_name),
            "start_token": f"Running tool: {tool_name}",
        }
        parent_tool_use_id = self._tool_call_parent_id(tool_call)
        if parent_tool_use_id:
            tool_spec["parentToolUseId"] = parent_tool_use_id
        for key in ("toolGroupId", "toolIndex", "toolTotal", "toolOperationId", "toolOperationLabel"):
            if tool_call.get(key) is not None:
                tool_spec[key] = tool_call.get(key)
        return tool_spec

    def _tool_semantic_metadata_for_spec(self, tool_spec: dict[str, Any]) -> dict[str, str]:
        phase_id = str(tool_spec.get("toolPhaseId") or "tool").strip() or "tool"
        phase_label = str(tool_spec.get("toolPhaseLabel") or "tool").strip() or "tool"
        tool_group_id = str(tool_spec.get("toolGroupId") or "").strip()
        semantic_parent_id = f"group:{tool_group_id}:phase:{phase_id}" if tool_group_id else f"phase:{phase_id}"
        return {
            "toolSemanticParentId": semantic_parent_id,
            "toolSemanticParentLabel": phase_label,
        }

    def _annotate_tool_call_batch(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not tool_calls:
            return tool_calls
        tool_group_id = self._store.new_id("tgrp")
        tool_total = len(tool_calls)
        annotated: list[dict[str, Any]] = []
        read_parent_candidates: list[tuple[str, str, set[str]]] = []
        operation_by_tool_id: dict[str, tuple[str, str]] = {}
        file_change_parent_tool_id = ""
        git_status_parent_tool_id = ""
        scratchpad_write_candidates: list[tuple[str, str]] = []
        web_fetch_candidates: list[tuple[str, str]] = []
        notebook_list_candidates: list[tuple[str, str]] = []
        computer_use_browser_candidates: list[tuple[str, str]] = []
        for index, tool_call in enumerate(tool_calls):
            if not isinstance(tool_call, dict):
                annotated.append(tool_call)
                continue
            next_call = dict(tool_call)
            next_call.setdefault("toolGroupId", tool_group_id)
            next_call.setdefault("toolIndex", index)
            next_call.setdefault("toolTotal", tool_total)
            tool_id = self._ensure_tool_call_id(next_call)
            tool_name = self._tool_call_name(next_call)
            self._annotate_tool_operation(next_call)
            if (
                tool_name == "read_file"
                and self._tool_call_parent_id(next_call) is None
            ):
                parent_tool_id = self._read_parent_tool_id_from_candidates(
                    self._tool_reference_paths(next_call),
                    read_parent_candidates,
                )
                if parent_tool_id:
                    next_call["parentToolUseId"] = parent_tool_id
                    self._inherit_batch_tool_operation(next_call, operation_by_tool_id)
            if (
                tool_name == "scratchpad.read"
                and self._tool_call_parent_id(next_call) is None
            ):
                parent_tool_id = self._scratchpad_read_parent_tool_id_from_candidates(
                    self._scratchpad_key(next_call),
                    scratchpad_write_candidates,
                )
                if parent_tool_id:
                    next_call["parentToolUseId"] = parent_tool_id
                    self._inherit_batch_tool_operation(next_call, operation_by_tool_id)
            if (
                tool_name == _BROWSER_TOOL_NAME
                and self._tool_call_parent_id(next_call) is None
            ):
                parent_tool_id = self._matching_value_parent_tool_id_from_candidates(
                    self._tool_url(next_call),
                    web_fetch_candidates,
                )
                if parent_tool_id:
                    next_call["parentToolUseId"] = parent_tool_id
                    self._inherit_batch_tool_operation(next_call, operation_by_tool_id)
            if (
                tool_name == _NOTEBOOK_TOOL_NAME
                and self._notebook_action(next_call) in _NOTEBOOK_DETAIL_ACTIONS
                and self._tool_call_parent_id(next_call) is None
            ):
                parent_tool_id = self._matching_value_parent_tool_id_from_candidates(
                    self._notebook_path(next_call),
                    notebook_list_candidates,
                )
                if parent_tool_id:
                    next_call["parentToolUseId"] = parent_tool_id
                    self._inherit_batch_tool_operation(next_call, operation_by_tool_id)
            if (
                tool_name == _COMPUTER_USE_TOOL_NAME
                and self._computer_use_action(next_call) in _COMPUTER_USE_BROWSER_FOLLOW_UP_ACTIONS
                and self._tool_call_parent_id(next_call) is None
            ):
                parent_tool_id = self._matching_value_parent_tool_id_from_candidates(
                    self._computer_use_browser_key(next_call),
                    computer_use_browser_candidates,
                )
                if parent_tool_id:
                    next_call["parentToolUseId"] = parent_tool_id
                    self._inherit_batch_tool_operation(next_call, operation_by_tool_id)
            if (
                tool_name == "run_command"
                and file_change_parent_tool_id
                and self._tool_call_parent_id(next_call) is None
                and is_verification_command(self._tool_call_arguments(next_call).get("command"))
            ):
                next_call["parentToolUseId"] = file_change_parent_tool_id
                self._inherit_batch_tool_operation(next_call, operation_by_tool_id)
            if (
                tool_name in {_GIT_STATUS_TOOL_NAME, _GIT_DIFF_TOOL_NAME}
                and file_change_parent_tool_id
                and self._tool_call_parent_id(next_call) is None
            ):
                next_call["parentToolUseId"] = file_change_parent_tool_id
                self._inherit_batch_tool_operation(next_call, operation_by_tool_id)
            if (
                tool_name == _GIT_DIFF_TOOL_NAME
                and git_status_parent_tool_id
                and self._tool_call_parent_id(next_call) is None
            ):
                next_call["parentToolUseId"] = git_status_parent_tool_id
                self._inherit_batch_tool_operation(next_call, operation_by_tool_id)
            if self._tool_call_parent_id(next_call) is not None and tool_name not in _FILE_CHANGE_TOOL_NAMES:
                self._inherit_batch_tool_operation(next_call, operation_by_tool_id)
            if tool_name in _CONTEXT_DISCOVERY_TOOL_NAMES:
                if tool_id:
                    read_parent_candidates.append((tool_id, tool_name, set()))
            if tool_name == _GIT_STATUS_TOOL_NAME and tool_id:
                git_status_parent_tool_id = tool_id
            if tool_name in _FILE_CHANGE_TOOL_NAMES:
                if tool_id:
                    file_change_parent_tool_id = tool_id
                    read_parent_candidates.append((tool_id, tool_name, self._tool_reference_paths(next_call)))
            if self._is_generic_reference_source_tool_name(tool_name):
                reference_paths = self._tool_reference_paths(next_call)
                if tool_id and reference_paths:
                    read_parent_candidates.append((tool_id, tool_name, reference_paths))
                reference_urls = self._tool_reference_urls(next_call)
                if tool_id and reference_urls:
                    for reference_url in sorted(reference_urls):
                        web_fetch_candidates.append((tool_id, reference_url))
            if tool_name == _SCRATCHPAD_WRITE_TOOL_NAME:
                key = self._scratchpad_key(next_call)
                if tool_id and key:
                    scratchpad_write_candidates.append((tool_id, key))
            if tool_name == _WEB_FETCH_TOOL_NAME:
                url = self._tool_url(next_call)
                if tool_id and url:
                    web_fetch_candidates.append((tool_id, url))
            if tool_name == _NOTEBOOK_TOOL_NAME and self._notebook_action(next_call) == _NOTEBOOK_LIST_ACTION:
                path = self._notebook_path(next_call)
                if tool_id and path:
                    notebook_list_candidates.append((tool_id, path))
            if tool_name == _COMPUTER_USE_TOOL_NAME and self._computer_use_action(next_call) in _COMPUTER_USE_BROWSER_OBSERVE_ACTIONS:
                key = self._computer_use_browser_key(next_call)
                if tool_id and key:
                    computer_use_browser_candidates.append((tool_id, key))
            self._remember_batch_tool_operation(next_call, operation_by_tool_id)
            annotated.append(next_call)
        return annotated

    def _annotate_tool_call_batch_with_history(
        self,
        tool_calls: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        annotated = self._annotate_tool_call_batch(tool_calls)
        if not tool_results:
            return annotated
        with_history: list[dict[str, Any]] = []
        for tool_call in annotated:
            if not isinstance(tool_call, dict) or self._tool_call_parent_id(tool_call) is not None:
                if isinstance(tool_call, dict):
                    next_call = dict(tool_call)
                    self._inherit_follow_up_tool_operation(next_call, tool_results)
                    with_history.append(next_call)
                else:
                    with_history.append(tool_call)
                continue
            parent_tool_id = self._follow_up_parent_tool_id(tool_call, tool_results)
            if parent_tool_id:
                next_call = dict(tool_call)
                next_call["parentToolUseId"] = parent_tool_id
                self._inherit_follow_up_tool_operation(next_call, tool_results)
                with_history.append(next_call)
            else:
                with_history.append(tool_call)
        return with_history

    def _annotate_tool_spec_batch(self, tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not tool_specs:
            return tool_specs
        tool_group_id = self._store.new_id("tgrp")
        tool_total = len(tool_specs)
        annotated: list[dict[str, Any]] = []
        read_parent_candidates: list[tuple[str, str, set[str]]] = []
        operation_by_tool_id: dict[str, tuple[str, str]] = {}
        file_change_parent_tool_id = ""
        git_status_parent_tool_id = ""
        scratchpad_write_candidates: list[tuple[str, str]] = []
        web_fetch_candidates: list[tuple[str, str]] = []
        notebook_list_candidates: list[tuple[str, str]] = []
        computer_use_browser_candidates: list[tuple[str, str]] = []
        for index, tool_spec in enumerate(tool_specs):
            if not isinstance(tool_spec, dict):
                annotated.append(tool_spec)
                continue
            next_spec = dict(tool_spec)
            next_spec.setdefault("toolGroupId", tool_group_id)
            next_spec.setdefault("toolIndex", index)
            next_spec.setdefault("toolTotal", tool_total)
            tool_id = self._ensure_tool_call_id(next_spec)
            tool_name = str(next_spec.get("name") or "").strip()
            self._annotate_tool_operation(next_spec)
            if (
                tool_name == "read_file"
                and self._tool_call_parent_id(next_spec) is None
            ):
                parent_tool_id = self._read_parent_tool_id_from_candidates(
                    self._tool_reference_paths(next_spec),
                    read_parent_candidates,
                )
                if parent_tool_id:
                    next_spec["parentToolUseId"] = parent_tool_id
                    self._inherit_batch_tool_operation(next_spec, operation_by_tool_id)
            if (
                tool_name == "scratchpad.read"
                and self._tool_call_parent_id(next_spec) is None
            ):
                parent_tool_id = self._scratchpad_read_parent_tool_id_from_candidates(
                    self._scratchpad_key(next_spec),
                    scratchpad_write_candidates,
                )
                if parent_tool_id:
                    next_spec["parentToolUseId"] = parent_tool_id
                    self._inherit_batch_tool_operation(next_spec, operation_by_tool_id)
            if (
                tool_name == _BROWSER_TOOL_NAME
                and self._tool_call_parent_id(next_spec) is None
            ):
                parent_tool_id = self._matching_value_parent_tool_id_from_candidates(
                    self._tool_url(next_spec),
                    web_fetch_candidates,
                )
                if parent_tool_id:
                    next_spec["parentToolUseId"] = parent_tool_id
                    self._inherit_batch_tool_operation(next_spec, operation_by_tool_id)
            if (
                tool_name == _NOTEBOOK_TOOL_NAME
                and self._notebook_action(next_spec) in _NOTEBOOK_DETAIL_ACTIONS
                and self._tool_call_parent_id(next_spec) is None
            ):
                parent_tool_id = self._matching_value_parent_tool_id_from_candidates(
                    self._notebook_path(next_spec),
                    notebook_list_candidates,
                )
                if parent_tool_id:
                    next_spec["parentToolUseId"] = parent_tool_id
                    self._inherit_batch_tool_operation(next_spec, operation_by_tool_id)
            if (
                tool_name == _COMPUTER_USE_TOOL_NAME
                and self._computer_use_action(next_spec) in _COMPUTER_USE_BROWSER_FOLLOW_UP_ACTIONS
                and self._tool_call_parent_id(next_spec) is None
            ):
                parent_tool_id = self._matching_value_parent_tool_id_from_candidates(
                    self._computer_use_browser_key(next_spec),
                    computer_use_browser_candidates,
                )
                if parent_tool_id:
                    next_spec["parentToolUseId"] = parent_tool_id
                    self._inherit_batch_tool_operation(next_spec, operation_by_tool_id)
            if (
                tool_name == "run_command"
                and file_change_parent_tool_id
                and self._tool_call_parent_id(next_spec) is None
                and is_verification_command(self._tool_call_arguments(next_spec).get("command"))
            ):
                next_spec["parentToolUseId"] = file_change_parent_tool_id
                self._inherit_batch_tool_operation(next_spec, operation_by_tool_id)
            if (
                tool_name in {_GIT_STATUS_TOOL_NAME, _GIT_DIFF_TOOL_NAME}
                and file_change_parent_tool_id
                and self._tool_call_parent_id(next_spec) is None
            ):
                next_spec["parentToolUseId"] = file_change_parent_tool_id
                self._inherit_batch_tool_operation(next_spec, operation_by_tool_id)
            if (
                tool_name == _GIT_DIFF_TOOL_NAME
                and git_status_parent_tool_id
                and self._tool_call_parent_id(next_spec) is None
            ):
                next_spec["parentToolUseId"] = git_status_parent_tool_id
                self._inherit_batch_tool_operation(next_spec, operation_by_tool_id)
            if self._tool_call_parent_id(next_spec) is not None and tool_name not in _FILE_CHANGE_TOOL_NAMES:
                self._inherit_batch_tool_operation(next_spec, operation_by_tool_id)
            if tool_name in _CONTEXT_DISCOVERY_TOOL_NAMES:
                if tool_id:
                    read_parent_candidates.append((tool_id, tool_name, set()))
            if tool_name == _GIT_STATUS_TOOL_NAME and tool_id:
                git_status_parent_tool_id = tool_id
            if tool_name in _FILE_CHANGE_TOOL_NAMES:
                if tool_id:
                    file_change_parent_tool_id = tool_id
                    read_parent_candidates.append((tool_id, tool_name, self._tool_reference_paths(next_spec)))
            if self._is_generic_reference_source_tool_name(tool_name):
                reference_paths = self._tool_reference_paths(next_spec)
                if tool_id and reference_paths:
                    read_parent_candidates.append((tool_id, tool_name, reference_paths))
                reference_urls = self._tool_reference_urls(next_spec)
                if tool_id and reference_urls:
                    for reference_url in sorted(reference_urls):
                        web_fetch_candidates.append((tool_id, reference_url))
            if tool_name == _SCRATCHPAD_WRITE_TOOL_NAME:
                key = self._scratchpad_key(next_spec)
                if tool_id and key:
                    scratchpad_write_candidates.append((tool_id, key))
            if tool_name == _WEB_FETCH_TOOL_NAME:
                url = self._tool_url(next_spec)
                if tool_id and url:
                    web_fetch_candidates.append((tool_id, url))
            if tool_name == _NOTEBOOK_TOOL_NAME and self._notebook_action(next_spec) == _NOTEBOOK_LIST_ACTION:
                path = self._notebook_path(next_spec)
                if tool_id and path:
                    notebook_list_candidates.append((tool_id, path))
            if tool_name == _COMPUTER_USE_TOOL_NAME and self._computer_use_action(next_spec) in _COMPUTER_USE_BROWSER_OBSERVE_ACTIONS:
                key = self._computer_use_browser_key(next_spec)
                if tool_id and key:
                    computer_use_browser_candidates.append((tool_id, key))
            self._remember_batch_tool_operation(next_spec, operation_by_tool_id)
            annotated.append(next_spec)
        return annotated

    def _annotate_follow_up_tool_spec(
        self,
        tool_spec: dict[str, Any] | None,
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not isinstance(tool_spec, dict):
            return tool_spec
        annotated = self._annotate_tool_spec_batch([tool_spec])[0]
        if self._tool_call_parent_id(annotated) is not None:
            self._inherit_follow_up_tool_operation(annotated, tool_results)
            return annotated
        parent_tool_id = self._follow_up_parent_tool_id(annotated, tool_results)
        if parent_tool_id:
            annotated["parentToolUseId"] = parent_tool_id
        self._inherit_follow_up_tool_operation(annotated, tool_results)
        return annotated

    def _annotate_tool_call_with_completed_results(
        self,
        tool_call: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(tool_call, dict):
            return tool_call
        annotated = dict(tool_call)
        if self._tool_call_parent_id(annotated) is None:
            parent_tool_id = self._follow_up_parent_tool_id(annotated, tool_results)
            if parent_tool_id:
                annotated["parentToolUseId"] = parent_tool_id
        self._inherit_follow_up_tool_operation(annotated, tool_results)
        return annotated

    @staticmethod
    def _sync_tool_metadata(source: dict[str, Any], target: dict[str, Any]) -> None:
        for key in ("parentToolUseId", "toolOperationId", "toolOperationLabel"):
            if source.get(key) is not None:
                target[key] = source[key]

    def _ensure_tool_result_operation(
        self,
        tool_spec: dict[str, Any],
        tool_result: dict[str, Any],
    ) -> None:
        if not isinstance(tool_spec, dict) or not isinstance(tool_result, dict):
            return
        if tool_result.get("toolOperationId") or tool_result.get("toolOperationLabel"):
            return
        result_payload = tool_result.get("result")
        tool_like = {
            "name": tool_result.get("name") or tool_spec.get("name"),
            "arguments": deepcopy(tool_spec.get("arguments", {})),
            "result": result_payload if isinstance(result_payload, dict) else {},
        }
        self._annotate_tool_operation(tool_like)
        for key in ("toolOperationId", "toolOperationLabel"):
            value = tool_like.get(key)
            if isinstance(value, str) and value.strip():
                tool_result[key] = value.strip()

    def _annotate_tool_operation(self, tool_like: dict[str, Any]) -> None:
        if tool_like.get("toolOperationId") or tool_like.get("toolOperationLabel"):
            return
        tool_name = self._tool_call_name(tool_like) or str(tool_like.get("name") or "").strip()
        operation_key = self._tool_operation_key(tool_name, tool_like)
        if not operation_key:
            return
        tool_like["toolOperationId"] = operation_key
        label = _TOOL_OPERATION_LABELS.get(operation_key.split(":", 1)[0])
        if label:
            tool_like["toolOperationLabel"] = label

    def _remember_batch_tool_operation(
        self,
        tool_like: dict[str, Any],
        operation_by_tool_id: dict[str, tuple[str, str]],
    ) -> None:
        tool_id = self._tool_call_id(tool_like)
        operation_id = tool_like.get("toolOperationId")
        if not tool_id or not isinstance(operation_id, str) or not operation_id.strip():
            return
        operation_label = tool_like.get("toolOperationLabel")
        operation_by_tool_id[tool_id] = (
            operation_id.strip(),
            operation_label.strip() if isinstance(operation_label, str) and operation_label.strip() else "",
        )

    def _inherit_batch_tool_operation(
        self,
        tool_like: dict[str, Any],
        operation_by_tool_id: dict[str, tuple[str, str]],
    ) -> None:
        parent_tool_id = self._tool_call_parent_id(tool_like)
        if not parent_tool_id:
            return
        operation = operation_by_tool_id.get(parent_tool_id)
        if not operation:
            return
        operation_id, operation_label = operation
        tool_like["toolOperationId"] = operation_id
        if operation_label:
            tool_like["toolOperationLabel"] = operation_label

    def _inherit_follow_up_tool_operation(self, tool_like: dict[str, Any], tool_results: list[dict[str, Any]]) -> None:
        parent_tool_id = self._tool_call_parent_id(tool_like)
        if not parent_tool_id:
            return
        result_by_id: dict[str, dict[str, Any]] = {}
        for tool_result in tool_results:
            if not isinstance(tool_result, dict):
                continue
            tool_result_id = self._tool_call_id(tool_result)
            if tool_result_id:
                result_by_id[tool_result_id] = tool_result
        seen_tool_ids: set[str] = set()
        current_tool_id = parent_tool_id
        while current_tool_id and current_tool_id not in seen_tool_ids:
            seen_tool_ids.add(current_tool_id)
            tool_result = result_by_id.get(current_tool_id)
            if tool_result is None:
                return
            operation_id = tool_result.get("toolOperationId")
            operation_label = tool_result.get("toolOperationLabel")
            has_operation = isinstance(operation_id, str) and bool(operation_id.strip())
            has_label = isinstance(operation_label, str) and bool(operation_label.strip())
            if has_operation:
                tool_like["toolOperationId"] = operation_id.strip()
            if has_label:
                tool_like["toolOperationLabel"] = operation_label.strip()
            if has_operation or has_label:
                return
            current_tool_id = self._tool_call_parent_id(tool_result) or ""

    def _tool_operation_key(self, tool_name: str, tool_like: dict[str, Any]) -> str:
        if tool_name in {"search_files", "code_search"}:
            query = self._tool_query(tool_like)
            return f"context:search:{query}" if query else "context:search"
        if tool_name in {"list_dir", "list_directory", "read_file"}:
            paths = sorted(self._tool_reference_paths(tool_like))
            return f"context:path:{paths[0]}" if paths else "context:path"
        if tool_name in _FILE_CHANGE_TOOL_NAMES:
            paths = sorted(self._tool_reference_paths(tool_like))
            return f"file_change:{paths[0]}" if paths else "file_change"
        if tool_name == "run_command" and is_verification_command(self._tool_call_arguments(tool_like).get("command")):
            return "verification"
        if tool_name in {_GIT_STATUS_TOOL_NAME, _GIT_DIFF_TOOL_NAME}:
            return "git"
        if tool_name in {_WEB_FETCH_TOOL_NAME, _BROWSER_TOOL_NAME}:
            url = self._tool_url(tool_like)
            return f"web:{url}" if url else "web"
        if tool_name == _NOTEBOOK_TOOL_NAME:
            path = self._notebook_path(tool_like)
            return f"notebook:{path}" if path else "notebook"
        if tool_name in {_SCRATCHPAD_WRITE_TOOL_NAME, "scratchpad.read"}:
            key = self._scratchpad_key(tool_like)
            return f"scratchpad:{key}" if key else "scratchpad"
        if tool_name.startswith("memory."):
            query = self._tool_query(tool_like)
            return f"memory:{query}" if query else "memory"
        if tool_name == _COMPUTER_USE_TOOL_NAME:
            arguments = self._tool_call_arguments(tool_like)
            target = _compact_operation_key(arguments.get("target") or arguments.get("app") or arguments.get("application") or "desktop")
            browser_key = self._computer_use_browser_key(tool_like)
            if browser_key:
                return f"computer_use:browser:{browser_key}"
            action = _compact_operation_key(arguments.get("action") or "request")
            return f"computer_use:{target}:{action}" if target and action else "computer_use"
        if tool_name.startswith("mcp__"):
            parts = tool_name.split("__", 2)
            server = _compact_operation_key(parts[1] if len(parts) >= 2 else "mcp")
            tool = _compact_operation_key(parts[2] if len(parts) >= 3 else tool_name)
            hint = self._tool_operation_hint(tool_like)
            suffix = f":{hint}" if hint else ""
            return f"mcp:{server}:{tool}{suffix}"
        hint = self._tool_operation_hint(tool_like)
        if hint:
            return f"tool:{_compact_operation_key(tool_name or 'tool')}:{hint}"
        return ""

    def _follow_up_parent_tool_id(
        self,
        tool_spec: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> str:
        tool_name = self._tool_call_name(tool_spec) or str(tool_spec.get("name") or "").strip()
        if tool_name == "read_file":
            return self._last_read_parent_tool_result_id(tool_results, self._tool_reference_paths(tool_spec))
        if tool_name == "scratchpad.read":
            return self._last_scratchpad_write_tool_result_id(tool_results, self._scratchpad_key(tool_spec))
        if tool_name == _BROWSER_TOOL_NAME:
            return self._last_web_fetch_tool_result_id(tool_results, self._tool_url(tool_spec))
        if tool_name == _NOTEBOOK_TOOL_NAME and self._notebook_action(tool_spec) in _NOTEBOOK_DETAIL_ACTIONS:
            return self._last_notebook_list_tool_result_id(tool_results, self._notebook_path(tool_spec))
        if (
            tool_name == _COMPUTER_USE_TOOL_NAME
            and self._computer_use_action(tool_spec) in _COMPUTER_USE_BROWSER_FOLLOW_UP_ACTIONS
        ):
            return self._last_computer_use_browser_observation_tool_result_id(
                tool_results,
                self._computer_use_browser_key(tool_spec),
            )
        if tool_name == "run_command" and is_verification_command(self._tool_call_arguments(tool_spec).get("command")):
            return self._last_tool_result_id(tool_results, _FILE_CHANGE_TOOL_NAMES)
        if tool_name in {_GIT_STATUS_TOOL_NAME, _GIT_DIFF_TOOL_NAME}:
            file_change_parent_tool_id = self._last_tool_result_id(tool_results, _FILE_CHANGE_TOOL_NAMES)
            if file_change_parent_tool_id:
                return file_change_parent_tool_id
            if tool_name == _GIT_DIFF_TOOL_NAME:
                git_status_parent_tool_id = self._last_tool_result_id(tool_results, frozenset({_GIT_STATUS_TOOL_NAME}))
                if git_status_parent_tool_id:
                    return git_status_parent_tool_id
        return ""

    def _read_parent_tool_id_from_candidates(
        self,
        read_paths: set[str],
        candidates: list[tuple[str, str, set[str]]],
    ) -> str:
        for tool_id, tool_name, candidate_paths in reversed(candidates):
            if not tool_id:
                continue
            if tool_name in _CONTEXT_DISCOVERY_TOOL_NAMES:
                return tool_id
            if read_paths and self._paths_overlap(read_paths, candidate_paths):
                return tool_id
        return ""

    def _last_read_parent_tool_result_id(self, tool_results: list[dict[str, Any]], read_paths: set[str]) -> str:
        for tool_result in reversed(tool_results):
            if not isinstance(tool_result, dict):
                continue
            tool_name = str(tool_result.get("name") or "").strip()
            if tool_name in _CONTEXT_DISCOVERY_TOOL_NAMES:
                tool_id = self._tool_call_id(tool_result)
                if tool_id:
                    return tool_id
            if tool_name in _FILE_CHANGE_TOOL_NAMES and read_paths and self._paths_overlap(
                read_paths,
                self._tool_reference_paths(tool_result),
            ):
                tool_id = self._tool_call_id(tool_result)
                if tool_id:
                    return tool_id
            if (
                self._is_generic_reference_source_tool_name(tool_name)
                and read_paths
                and self._paths_overlap(read_paths, self._tool_reference_paths(tool_result))
            ):
                tool_id = self._tool_call_id(tool_result)
                if tool_id:
                    return tool_id
        return ""

    @staticmethod
    def _scratchpad_read_parent_tool_id_from_candidates(
        read_key: str,
        candidates: list[tuple[str, str]],
    ) -> str:
        if not read_key:
            return ""
        for tool_id, write_key in reversed(candidates):
            if tool_id and write_key == read_key:
                return tool_id
        return ""

    @staticmethod
    def _matching_value_parent_tool_id_from_candidates(
        value: str,
        candidates: list[tuple[str, str]],
    ) -> str:
        if not value:
            return ""
        for tool_id, candidate_value in reversed(candidates):
            if tool_id and candidate_value == value:
                return tool_id
        return ""

    def _last_scratchpad_write_tool_result_id(self, tool_results: list[dict[str, Any]], read_key: str) -> str:
        if not read_key:
            return ""
        for tool_result in reversed(tool_results):
            if not isinstance(tool_result, dict):
                continue
            if str(tool_result.get("name") or "").strip() != _SCRATCHPAD_WRITE_TOOL_NAME:
                continue
            if self._scratchpad_key(tool_result) != read_key:
                continue
            tool_id = self._tool_call_id(tool_result)
            if tool_id:
                return tool_id
        return ""

    def _last_web_fetch_tool_result_id(self, tool_results: list[dict[str, Any]], url: str) -> str:
        if not url:
            return ""
        for tool_result in reversed(tool_results):
            if not isinstance(tool_result, dict):
                continue
            tool_name = str(tool_result.get("name") or "").strip()
            if tool_name == _WEB_FETCH_TOOL_NAME:
                matches = self._tool_url(tool_result) == url or url in self._tool_reference_urls(tool_result)
            elif self._is_generic_reference_source_tool_name(tool_name):
                matches = url in self._tool_reference_urls(tool_result)
            else:
                matches = False
            if not matches:
                continue
            tool_id = self._tool_call_id(tool_result)
            if tool_id:
                return tool_id
        return ""

    def _last_notebook_list_tool_result_id(self, tool_results: list[dict[str, Any]], path: str) -> str:
        if not path:
            return ""
        for tool_result in reversed(tool_results):
            if not isinstance(tool_result, dict):
                continue
            if str(tool_result.get("name") or "").strip() != _NOTEBOOK_TOOL_NAME:
                continue
            if self._notebook_action(tool_result) != _NOTEBOOK_LIST_ACTION:
                continue
            if self._notebook_path(tool_result) != path:
                continue
            tool_id = self._tool_call_id(tool_result)
            if tool_id:
                return tool_id
        return ""

    def _last_computer_use_browser_observation_tool_result_id(self, tool_results: list[dict[str, Any]], key: str) -> str:
        if not key:
            return ""
        for tool_result in reversed(tool_results):
            if not isinstance(tool_result, dict):
                continue
            if str(tool_result.get("name") or "").strip() != _COMPUTER_USE_TOOL_NAME:
                continue
            if self._computer_use_action(tool_result) not in _COMPUTER_USE_BROWSER_OBSERVE_ACTIONS:
                continue
            if self._computer_use_browser_key(tool_result) != key:
                continue
            tool_id = self._tool_call_id(tool_result)
            if tool_id:
                return tool_id
        return ""

    @classmethod
    def _tool_url(cls, tool_like: dict[str, Any]) -> str:
        arguments = cls._tool_call_arguments(tool_like)
        for source in (arguments, tool_like, tool_like.get("result")):
            if not isinstance(source, dict):
                continue
            value = source.get("url")
            if isinstance(value, str) and value.strip():
                return cls._normalize_tool_url(value)
        return ""

    @classmethod
    def _notebook_path(cls, tool_like: dict[str, Any]) -> str:
        arguments = cls._tool_call_arguments(tool_like)
        for source in (arguments, tool_like, tool_like.get("result")):
            if not isinstance(source, dict):
                continue
            value = source.get("path")
            if isinstance(value, str) and value.strip():
                return cls._normalize_tool_path(value)
        return ""

    @classmethod
    def _notebook_action(cls, tool_like: dict[str, Any]) -> str:
        arguments = cls._tool_call_arguments(tool_like)
        for source in (arguments, tool_like, tool_like.get("result")):
            if not isinstance(source, dict):
                continue
            value = source.get("action") or source.get("notebookAction")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @classmethod
    def _computer_use_action(cls, tool_like: dict[str, Any]) -> str:
        arguments = cls._tool_call_arguments(tool_like)
        for source in (arguments, tool_like, tool_like.get("result")):
            if not isinstance(source, dict):
                continue
            value = source.get("action")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return ""

    @classmethod
    def _computer_use_browser_key(cls, tool_like: dict[str, Any]) -> str:
        arguments = cls._tool_call_arguments(tool_like)
        result = tool_like.get("result")
        request = result.get("request") if isinstance(result, dict) and isinstance(result.get("request"), dict) else None
        sources = tuple(source for source in (arguments, request, tool_like, result) if isinstance(source, dict))
        for source in sources:
            value = source.get("url")
            if isinstance(value, str) and value.strip():
                return f"url:{cls._normalize_tool_url(value)}"
        for source in sources:
            page_id = source.get("pageId")
            context_id = source.get("browserContextId")
            if isinstance(page_id, str) and page_id.strip():
                context_text = context_id.strip() if isinstance(context_id, str) and context_id.strip() else "default"
                return f"page:{context_text}:{page_id.strip().lower()}"
        return ""

    @classmethod
    def _scratchpad_key(cls, tool_like: dict[str, Any]) -> str:
        arguments = cls._tool_call_arguments(tool_like)
        for source in (arguments, tool_like, tool_like.get("result")):
            if not isinstance(source, dict):
                continue
            value = source.get("key")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @classmethod
    def _tool_query(cls, tool_like: dict[str, Any]) -> str:
        arguments = cls._tool_call_arguments(tool_like)
        for source in (arguments, tool_like, tool_like.get("result")):
            if not isinstance(source, dict):
                continue
            value = source.get("query") or source.get("pattern") or source.get("content")
            if isinstance(value, str) and value.strip():
                return _compact_operation_key(value)
        return ""

    @classmethod
    def _tool_operation_hint(cls, tool_like: dict[str, Any]) -> str:
        query = cls._tool_query(tool_like)
        if query:
            return query
        url = cls._tool_url(tool_like)
        if url:
            return f"url:{_compact_operation_key(url, 96)}"
        urls = sorted(cls._tool_reference_urls(tool_like))
        if urls:
            return f"url:{_compact_operation_key(urls[0], 96)}"
        path = cls._tool_operation_path(tool_like)
        if path:
            return f"path:{_compact_operation_key(path, 96)}"
        field_hint = cls._tool_operation_field_hint(tool_like)
        if field_hint:
            return field_hint
        return ""

    @classmethod
    def _tool_operation_path(cls, tool_like: dict[str, Any]) -> str:
        arguments = cls._tool_call_arguments(tool_like)
        for source in (arguments, tool_like, tool_like.get("result")):
            if not isinstance(source, dict):
                continue
            for key in ("path", "file", "relativePath", "relative_path"):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return cls._normalize_tool_path(value)
            for key in (
                "changedPaths",
                "paths",
                "files",
                "items",
                "results",
                "matches",
                "sources",
                "references",
            ):
                path = cls._first_path_from_value(source.get(key))
                if path:
                    return path
        return ""

    @classmethod
    def _first_path_from_value(cls, value: Any) -> str:
        if isinstance(value, str) and value.strip():
            return cls._normalize_tool_path(value)
        if isinstance(value, dict):
            for key in ("path", "file", "relativePath", "relative_path"):
                path = cls._first_path_from_value(value.get(key))
                if path:
                    return path
            return ""
        if isinstance(value, list):
            for item in value:
                path = cls._first_path_from_value(item)
                if path:
                    return path
        return ""

    @classmethod
    def _tool_operation_field_hint(cls, tool_like: dict[str, Any]) -> str:
        arguments = cls._tool_call_arguments(tool_like)
        sources = (
            (arguments, ("target", "resource", "resourceId", "resource_id", "id", "key", "action")),
            (tool_like, ("target", "resource", "resourceId", "resource_id", "key", "action")),
            (tool_like.get("result"), ("target", "resource", "resourceId", "resource_id", "id", "key", "action")),
        )
        for source, keys in sources:
            if not isinstance(source, dict):
                continue
            for key in keys:
                value = source.get(key)
                if isinstance(value, bool) or value is None:
                    continue
                if not isinstance(value, (str, int, float)):
                    continue
                compact = _compact_operation_key(value)
                if compact:
                    return f"{key.lower()}:{compact}"
        return ""

    @staticmethod
    def _normalize_tool_url(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            parts = urlsplit(text)
        except ValueError:
            return text.rstrip("/").lower()
        if not parts.scheme and not parts.netloc:
            return text.rstrip("/").lower()
        path = parts.path.rstrip("/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))

    @staticmethod
    def _normalize_tool_path(value: Any) -> str:
        text = str(value or "").strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        return text.rstrip("/").lower()

    @staticmethod
    def _paths_overlap(left: set[str], right: set[str]) -> bool:
        return bool(left and right and left.intersection(right))

    @classmethod
    def _is_generic_reference_source_tool_name(cls, tool_name: str) -> bool:
        name = str(tool_name or "").strip()
        if not name:
            return False
        if name.startswith("mcp__"):
            return True
        if name.startswith(("memory.", "scratchpad.")):
            return False
        return name not in _STRUCTURED_TOOL_NAMES

    @classmethod
    def _paths_from_patch_text(cls, patch_text: Any) -> set[str]:
        text = str(patch_text or "")
        if not text:
            return set()
        paths: set[str] = set()
        for line in text.splitlines():
            candidate = ""
            if line.startswith("diff --git "):
                parts = line.split()
                if len(parts) >= 4:
                    candidate = parts[3]
            elif line.startswith("+++ "):
                candidate = line[4:].strip()
            if candidate.startswith("b/"):
                candidate = candidate[2:]
            if candidate and candidate != "/dev/null":
                normalized = cls._normalize_tool_path(candidate)
                if normalized:
                    paths.add(normalized)
        return paths

    @classmethod
    def _tool_reference_paths(cls, tool_like: dict[str, Any]) -> set[str]:
        paths: set[str] = set()

        def add(value: Any) -> None:
            if isinstance(value, str):
                normalized = cls._normalize_tool_path(value)
                if normalized:
                    paths.add(normalized)
            elif isinstance(value, dict):
                for key in ("path", "file", "target", "relativePath", "relative_path"):
                    add(value.get(key))
                for key in ("changedPaths", "paths", "files", "items", "results", "matches", "sources", "references"):
                    add(value.get(key))
            elif isinstance(value, list):
                for item in value:
                    add(item)

        arguments = cls._tool_call_arguments(tool_like)
        for key in ("path", "file", "target", "changedPaths", "paths", "files", "items", "results", "matches"):
            add(arguments.get(key))
        paths.update(cls._paths_from_patch_text(arguments.get("patchText") or arguments.get("patch_text")))
        for key in ("path", "file", "target", "changedPaths", "paths", "files", "items", "results", "matches"):
            add(tool_like.get(key))
        paths.update(cls._paths_from_patch_text(tool_like.get("patchText") or tool_like.get("patch_text")))
        result = tool_like.get("result")
        if isinstance(result, dict):
            for key in ("path", "file", "target", "changedPaths", "paths", "files", "items", "results", "matches"):
                add(result.get(key))
            paths.update(cls._paths_from_patch_text(result.get("patchText") or result.get("patch_text") or result.get("diffText")))
            patch = result.get("patch")
            if isinstance(patch, dict):
                add(patch.get("changedPaths"))
                paths.update(cls._paths_from_patch_text(patch.get("patchText") or patch.get("patch_text") or patch.get("diffText")))
        return paths

    @classmethod
    def _tool_reference_urls(cls, tool_like: dict[str, Any]) -> set[str]:
        urls: set[str] = set()

        def add(value: Any) -> None:
            if isinstance(value, str):
                try:
                    parts = urlsplit(value.strip())
                except ValueError:
                    return
                if parts.scheme and parts.netloc:
                    normalized = cls._normalize_tool_url(value)
                    if normalized:
                        urls.add(normalized)
            elif isinstance(value, dict):
                for key in ("url", "uri", "href", "link"):
                    add(value.get(key))
                for key in ("items", "results", "matches", "links", "sources", "references"):
                    add(value.get(key))
            elif isinstance(value, list):
                for item in value:
                    add(item)

        arguments = cls._tool_call_arguments(tool_like)
        add(arguments)
        add(tool_like)
        result = tool_like.get("result")
        if isinstance(result, dict):
            add(result)
        return urls

    @staticmethod
    def _last_tool_result_id(tool_results: list[dict[str, Any]], tool_names: frozenset[str]) -> str:
        for tool_result in reversed(tool_results):
            if not isinstance(tool_result, dict):
                continue
            if str(tool_result.get("name") or "").strip() not in tool_names:
                continue
            tool_id = ReactToolHelpersMixin._tool_call_id(tool_result)
            if tool_id:
                return tool_id
        return ""

    def _ensure_tool_call_id(self, tool_call: dict[str, Any]) -> str:
        tool_id = self._tool_call_id(tool_call)
        if not tool_id:
            tool_id = self._store.new_id("tc")
        tool_call["id"] = tool_id
        return tool_id

    @staticmethod
    def _tool_call_id(tool_call: dict[str, Any]) -> str:
        for key in ("id", "toolUseId", "tool_use_id", "tool_call_id", "call_id"):
            value = tool_call.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        metadata = tool_call.get("metadata")
        if isinstance(metadata, dict):
            for key in ("id", "toolUseId", "tool_use_id", "tool_call_id", "call_id"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _tool_call_parent_id(tool_call: dict[str, Any]) -> str | None:
        candidates = (
            tool_call.get("parentToolUseId"),
            tool_call.get("parent_tool_use_id"),
            tool_call.get("parentToolCallId"),
            tool_call.get("parent_tool_call_id"),
            tool_call.get("parentId"),
            tool_call.get("parent_id"),
        )
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip()
        metadata = tool_call.get("metadata")
        if isinstance(metadata, dict):
            for key in ("parentToolUseId", "parent_tool_use_id", "parentToolCallId", "parent_tool_call_id", "parentId", "parent_id"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    @staticmethod
    def _tool_call_name(tool_call: dict[str, Any]) -> str:
        name = tool_call.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        function_payload = tool_call.get("function")
        if isinstance(function_payload, dict):
            function_name = function_payload.get("name")
            if isinstance(function_name, str) and function_name.strip():
                return function_name.strip()
        return ""

    @staticmethod
    def _tool_call_arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
        raw_arguments = tool_call.get("arguments")
        if raw_arguments is None:
            function_payload = tool_call.get("function")
            if isinstance(function_payload, dict):
                raw_arguments = function_payload.get("arguments")
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if isinstance(raw_arguments, str):
            try:
                parsed = json.loads(raw_arguments or "{}")
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

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
            "notebook",
        }
        if tool_name in workspace_tools:
            arguments.pop("workspace_root", None)
            arguments["workspaceRoot"] = context["workspace_root"]
            original_root = context.get("original_workspace_root")
            if original_root:
                arguments.setdefault("originalWorkspaceRoot", original_root)
            active_worktree = context.get("active_worktree") if isinstance(context.get("active_worktree"), dict) else {}
            worktree_id = active_worktree.get("id") if isinstance(active_worktree, dict) else None
            if worktree_id:
                arguments.setdefault("activeWorktreeId", worktree_id)

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
        for key in ("parentToolUseId", "toolGroupId", "toolIndex", "toolTotal", "toolOperationId", "toolOperationLabel"):
            if tool_spec.get(key) is not None:
                tool_result[key] = tool_spec[key]
            else:
                tool_result.pop(key, None)
        for key in ("toolCategory", "toolPhaseId", "toolPhaseLabel"):
            if tool_spec.get(key) is not None:
                tool_result[key] = tool_spec[key]
        tool_result.update(self._tool_semantic_metadata_for_spec(tool_result))
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
