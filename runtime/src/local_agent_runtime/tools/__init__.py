"""Tool registry and built-in tool definitions."""

from __future__ import annotations

from typing import Any

from .list_dir import build_list_dir_tool
from .search_files import build_search_files_tool
from .read_file import build_read_file_tool
from .task import build_task_tool
from .run_command import build_run_command_tool
from .apply_patch import build_apply_patch_tool
from .git_status import build_git_status_tool
from .git_diff import build_git_diff_tool
from .write_file import build_write_file_tool
from .web_fetch import build_web_fetch_tool
from .code_search import build_code_search_tool
from .notebook import build_notebook_tool
from .browser import build_browser_tool
from .computer_use import build_computer_use_tool
from .memory import build_memory_remember_tool, build_memory_recall_tool
from .scratchpad_tool import build_scratchpad_write_tool, build_scratchpad_read_tool


def build_builtin_tools(
    policy_guard: Any,
    store: Any,
    subagent_service: Any | None = None,
    *,
    memory_manager: Any | None = None,
    scratchpad: Any | None = None,
    permission_engine: Any | None = None,
    computer_use_executor: Any | None = None,
) -> dict[str, Any]:
    """Build all built-in tools, returning a name -> handler mapping."""
    builders = [
        ("list_dir", build_list_dir_tool),
        ("search_files", build_search_files_tool),
        ("read_file", build_read_file_tool),
        ("task", build_task_tool),
        ("run_command", build_run_command_tool),
        ("apply_patch", build_apply_patch_tool),
        ("git_status", build_git_status_tool),
        ("git_diff", build_git_diff_tool),
        ("write_file", build_write_file_tool),
        ("web_fetch", build_web_fetch_tool),
        ("code_search", build_code_search_tool),
        ("notebook", build_notebook_tool),
        ("browser", build_browser_tool),
        ("computer_use", build_computer_use_tool),
    ]
    tools: dict[str, Any] = {}
    _engine_tools = {"run_command", "apply_patch", "write_file", "web_fetch", "task", "notebook", "computer_use"}
    for name, builder in builders:
        if name == "computer_use":
            tools[name] = builder(
                policy_guard,
                store,
                subagent_service,
                permission_engine=permission_engine,
                computer_use_executor=computer_use_executor,
            )["handler"]
        elif name in _engine_tools:
            tools[name] = builder(policy_guard, store, subagent_service, permission_engine=permission_engine)["handler"]
        else:
            tools[name] = builder(policy_guard, store, subagent_service)["handler"]

    # Memory tools (optional – only registered when memory_manager is provided)
    if memory_manager is not None:
        tools["memory.remember"] = build_memory_remember_tool(memory_manager)["handler"]
        tools["memory.recall"] = build_memory_recall_tool(memory_manager)["handler"]

    # Scratchpad tools (optional – only registered when scratchpad is provided)
    if scratchpad is not None:
        tools["scratchpad.write"] = build_scratchpad_write_tool(scratchpad)["handler"]
        tools["scratchpad.read"] = build_scratchpad_read_tool(scratchpad)["handler"]

    return tools
