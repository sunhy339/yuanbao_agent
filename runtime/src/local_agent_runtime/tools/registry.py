from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from ..services.worker_environment import DEFAULT_CHILD_TOOL_ALLOWLIST

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _string_property(description: str, *, default: str | None = None, examples: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "string",
        "minLength": 1,
        "description": description,
    }
    if default is not None:
        schema["default"] = default
    if examples:
        schema["examples"] = examples
    return schema


WORKSPACE_ROOT_PROPERTY = _string_property(
    "Absolute path to the active workspace root. Tools must stay within this directory.",
    examples=["D:/projects/example", "/Users/me/project"],
)

CHILD_TOOL_ALLOWLIST_TOOL_NAMES = [*DEFAULT_CHILD_TOOL_ALLOWLIST, "run_command", "apply_patch"]


def _child_tool_allowlist_property() -> dict[str, Any]:
    return {
        "type": "array",
        "description": (
            "Optional child-worker tool allowlist. Defaults to read-only tools; include run_command or apply_patch "
            "only when the child task explicitly needs command execution or file edits."
        ),
        "items": {
            "type": "string",
            "enum": CHILD_TOOL_ALLOWLIST_TOOL_NAMES,
        },
        "uniqueItems": True,
        "default": list(DEFAULT_CHILD_TOOL_ALLOWLIST),
        "examples": [["list_dir", "search_files", "read_file"], ["read_file", "run_command", "apply_patch"]],
    }


BUILTIN_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_dir",
        "description": (
            "List files and directories under a workspace-relative directory. Use this first to inspect structure; "
            "results honor ignore patterns and never traverse outside workspaceRoot."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspaceRoot": WORKSPACE_ROOT_PROPERTY,
                "path": _string_property(
                    "Workspace-relative directory to list. Use '.' for the workspace root.",
                    default=".",
                    examples=[".", "runtime/src", "shared/src"],
                ),
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to recursively include descendant directories.",
                    "default": False,
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum recursive depth from path; clamped by the runtime to a safe upper bound.",
                    "minimum": 1,
                    "maximum": 8,
                    "default": 1,
                },
                "ignore": {
                    "type": "array",
                    "description": "Additional glob or directory-name patterns to exclude from results.",
                    "items": {"type": "string", "minLength": 1},
                    "default": [],
                    "examples": [[".git", "node_modules", "*.log"]],
                },
            },
            "required": ["workspaceRoot"],
        },
        "safety": [
            "Read-only: does not modify files.",
            "Paths are resolved relative to workspaceRoot and rejected if they escape the workspace.",
        ],
        "hints": [
            "Use path='.' with recursive=false for a quick top-level inventory.",
            "Set recursive=true and max_depth=2 or 3 when looking for likely source files.",
        ],
    },
    {
        "name": "search_files",
        "description": (
            "Search files inside the workspace by content or filename. Prefer this before read_file when the relevant "
            "path is unknown; glob and ignore filters narrow the scan."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspaceRoot": WORKSPACE_ROOT_PROPERTY,
                "query": _string_property(
                    "Search text for content mode, or filename token for filename mode.",
                    examples=["ToolRegistry", "trace.list", "README.md"],
                ),
                "mode": {
                    "type": "string",
                    "description": "Search strategy: scan file contents or match file names.",
                    "enum": ["content", "filename"],
                    "default": "content",
                },
                "glob": {
                    "type": "array",
                    "description": "Optional include glob patterns applied before matching.",
                    "items": {"type": "string", "minLength": 1},
                    "default": [],
                    "examples": [["**/*.py"], ["runtime/**/*.py", "shared/**/*.ts"]],
                },
                "ignore": {
                    "type": "array",
                    "description": "Additional glob or directory-name patterns to exclude.",
                    "items": {"type": "string", "minLength": 1},
                    "default": [],
                    "examples": [[".git", "node_modules", "dist"]],
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum matches to return; runtime clamps this to avoid huge responses.",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 20,
                },
            },
            "required": ["workspaceRoot", "query"],
        },
        "safety": [
            "Read-only: does not modify files.",
            "Large/binary files may be skipped or decoded with replacement by the runtime.",
        ],
        "hints": [
            "Use mode='filename' when the user names a file or extension.",
            "Use max_results=8-20 for agent loops to keep context small.",
        ],
    },
    {
        "name": "read_file",
        "description": (
            "Read a single workspace-relative file as text. Use after list_dir or search_files identifies a likely "
            "target; max_bytes can limit large files."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspaceRoot": WORKSPACE_ROOT_PROPERTY,
                "path": _string_property(
                    "Workspace-relative file path to read.",
                    examples=["README.md", "runtime/src/local_agent_runtime/tools/registry.py"],
                ),
                "encoding": {
                    "type": "string",
                    "description": "Text encoding used to decode bytes; invalid sequences are replaced.",
                    "default": "utf-8",
                    "examples": ["utf-8"],
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Optional byte limit for the returned content.",
                    "minimum": 1,
                    "maximum": 1048576,
                    "default": 4000,
                },
                "ignore": {
                    "type": "array",
                    "description": "Additional ignore patterns; matching files are rejected.",
                    "items": {"type": "string", "minLength": 1},
                    "default": [],
                },
            },
            "required": ["workspaceRoot", "path"],
        },
        "safety": [
            "Read-only: does not modify files.",
            "Do not use this tool to inspect secrets or unrelated private files.",
        ],
        "hints": [
            "Read the smallest relevant file first.",
            "Set max_bytes for large generated files or logs.",
        ],
    },
    {
        "name": "task",
        "description": (
            "Create and execute a child collaboration task inline. The runtime records a child task, claims an "
            "agent worker, marks the task completed, and returns the child task result."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "prompt": _string_property(
                    "Instruction for the child collaboration task.",
                    examples=["Investigate the failing formatter command and summarize the cause."],
                ),
                "title": _string_property(
                    "Optional short title for the child task.",
                    examples=["Investigate formatter failure"],
                ),
                "agentType": _string_property(
                    "Optional worker role used to name the child collaboration worker.",
                    default="explorer",
                    examples=["explorer", "analyst", "coder"],
                ),
                "priority": {
                    "type": "integer",
                    "description": "Child task priority; the runtime clamps this into a safe 0-9 range.",
                    "minimum": 0,
                    "maximum": 9,
                    "default": 3,
                },
                "timeoutMs": {
                    "type": "integer",
                    "description": (
                        "Optional per-attempt child task timeout in milliseconds. Runner implementations enforce "
                        "this at the child task boundary."
                    ),
                    "minimum": 1000,
                    "maximum": 86400000,
                    "examples": [120000, 600000],
                },
                "retry": {
                    "type": "object",
                    "description": (
                        "Optional retry policy for child task execution. Runner implementations enforce retryable "
                        "failures at the child task boundary."
                    ),
                    "additionalProperties": False,
                    "properties": {
                        "maxAttempts": {
                            "type": "integer",
                            "description": "Maximum total attempts including the initial attempt.",
                            "minimum": 1,
                            "maximum": 10,
                            "default": 1,
                        },
                        "backoff": {
                            "type": "string",
                            "description": "Delay strategy between attempts.",
                            "enum": ["fixed", "exponential"],
                            "default": "fixed",
                        },
                        "delayMs": {
                            "type": "integer",
                            "description": "Initial delay before a retry attempt in milliseconds.",
                            "minimum": 0,
                            "maximum": 600000,
                            "default": 0,
                        },
                    },
                },
                "budget": {
                    "type": "object",
                    "description": "Optional advisory resource budget for the child task.",
                    "additionalProperties": True,
                    "properties": {
                        "maxTokens": {
                            "type": "integer",
                            "description": "Maximum model tokens allocated to the child task.",
                            "minimum": 1,
                        },
                        "remainingTokens": {
                            "type": "integer",
                            "description": "Remaining model tokens available to the child task.",
                            "minimum": 0,
                        },
                        "maxToolCalls": {
                            "type": "integer",
                            "description": "Maximum tool calls allocated to the child task.",
                            "minimum": 1,
                        },
                    },
                },
                "cancellation": {
                    "type": "object",
                    "description": "Optional cancellation metadata for coordinating child task cancellation.",
                    "additionalProperties": True,
                    "properties": {
                        "signalId": {
                            "type": "string",
                            "description": "Cancellation signal identifier supplied by the orchestrator.",
                            "minLength": 1,
                        },
                        "reason": {
                            "type": "string",
                            "description": "Human-readable cancellation reason.",
                            "minLength": 1,
                        },
                    },
                },
                "childToolAllowlist": _child_tool_allowlist_property(),
                "child_tool_allowlist": _child_tool_allowlist_property(),
                "sessionId": _string_property(
                    "Runtime session id injected by the orchestrator; models usually omit this.",
                ),
                "taskId": _string_property(
                    "Parent runtime task id injected by the orchestrator; models usually omit this.",
                ),
            },
            "required": ["prompt"],
        },
        "safety": [
            "Creates collaboration records and agent messages, but does not spawn a separate process in this slice.",
            "The child task is executed through an in-process runner boundary with retry and timeout policy.",
        ],
        "hints": [
            "Use this when you want a structured child collaboration task instead of a shell command.",
            "Keep prompts short and action-oriented so the child task result stays focused.",
        ],
    },
    {
        "name": "run_command",
        "description": (
            "Run a shell command in the workspace after policy approval. This can execute arbitrary code and should "
            "be used only for inspection, tests, builds, and narrowly-scoped project commands."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspaceRoot": WORKSPACE_ROOT_PROPERTY,
                "cwd": _string_property(
                    "Workspace-relative working directory for the command.",
                    default=".",
                    examples=[".", "runtime", "app"],
                ),
                "command": _string_property(
                    "Shell command to execute. Keep it non-interactive and scoped to the workspace.",
                    examples=["python -m pytest tests/test_context_builder.py -q", "git status --short"],
                ),
                "shell": {
                    "type": "string",
                    "description": "Shell used to execute command.",
                    "enum": ["powershell", "bash", "zsh"],
                    "default": "powershell",
                },
                "timeoutMs": {
                    "type": "integer",
                    "description": "Execution timeout in milliseconds.",
                    "minimum": 1000,
                    "maximum": 1800000,
                    "default": 600000,
                },
                "taskId": {
                    "type": "string",
                    "description": "Runtime task id injected by the orchestrator; models usually omit this.",
                },
                "approvalId": {
                    "type": "string",
                    "description": "Approval id supplied when retrying an approved command.",
                },
            },
            "required": ["workspaceRoot", "command"],
        },
        "safety": [
            "Dangerous: commands may modify files, execute code, access network, or delete data.",
            "Requires approval under the default policy before execution.",
            "Never use interactive commands, background daemons, destructive deletes, shutdowns, or formatting commands.",
        ],
        "hints": [
            "Prefer read-only commands first, such as tests, git status, or file listings.",
            "Use explicit timeouts for long-running test/build commands.",
        ],
    },
    {
        "name": "apply_patch",
        "description": (
            "Propose or apply workspace file edits after policy approval. Provide either a unified diff in patchText "
            "or a files array with complete replacement content."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspaceRoot": WORKSPACE_ROOT_PROPERTY,
                "patchText": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Unified diff text to apply inside the workspace.",
                    "examples": ["diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new"],
                },
                "files": {
                    "type": "array",
                    "description": "Alternative patch format: file replacements or creations.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "path": _string_property("Workspace-relative file path to create or replace."),
                            "content": {
                                "type": "string",
                                "description": "Complete desired file content.",
                            },
                        },
                        "required": ["path", "content"],
                    },
                    "maxItems": 20,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, validate and record the patch without applying it.",
                    "default": False,
                },
                "taskId": {
                    "type": "string",
                    "description": "Runtime task id injected by the orchestrator; models usually omit this.",
                },
                "approvalId": {
                    "type": "string",
                    "description": "Approval id supplied when retrying an approved patch.",
                },
            },
            "required": ["workspaceRoot"],
            "oneOf": [{"required": ["patchText"]}, {"required": ["files"]}],
        },
        "safety": [
            "Dangerous: can modify workspace files and overwrite user changes if the patch is wrong.",
            "Requires approval under the default policy before file changes are applied.",
            "Patch paths must remain inside workspaceRoot.",
        ],
        "hints": [
            "Keep patches small and focused.",
            "Prefer unified diffs for targeted edits and files[] for new small files.",
        ],
    },
    {
        "name": "git_status",
        "description": (
            "Return branch, upstream, ahead/behind counts, and porcelain working-tree changes for a git repository "
            "inside the workspace."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspaceRoot": WORKSPACE_ROOT_PROPERTY,
                "cwd": _string_property(
                    "Workspace-relative directory where git should run.",
                    default=".",
                    examples=[".", "runtime"],
                ),
            },
            "required": ["workspaceRoot"],
        },
        "safety": [
            "Read-only: runs git status and does not modify repository state.",
            "cwd is constrained to workspaceRoot.",
        ],
        "hints": [
            "Use before editing when the user warns about parallel workers.",
            "Use cwd for monorepos with nested repositories.",
        ],
    },
    {
        "name": "git_diff",
        "description": (
            "Return git diff text and changed-file metadata for unstaged or staged changes, optionally limited to a "
            "workspace-relative path."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspaceRoot": WORKSPACE_ROOT_PROPERTY,
                "cwd": _string_property(
                    "Workspace-relative directory where git should run.",
                    default=".",
                    examples=[".", "runtime"],
                ),
                "path": {
                    "type": "string",
                    "description": "Optional workspace-relative pathspec to limit the diff.",
                    "examples": ["runtime/src/local_agent_runtime/tools/registry.py"],
                },
                "staged": {
                    "type": "boolean",
                    "description": "If true, return staged diff; otherwise return unstaged diff.",
                    "default": False,
                },
            },
            "required": ["workspaceRoot"],
        },
        "safety": [
            "Read-only: runs git diff and does not modify repository state.",
            "Path filters are constrained to workspaceRoot.",
        ],
        "hints": [
            "Use staged=true to review the index before commit.",
            "Pass path for focused review of a single file or subtree.",
        ],
    },
]

BUILTIN_TOOL_SCHEMAS_BY_NAME: dict[str, dict[str, Any]] = {
    schema["name"]: schema for schema in BUILTIN_TOOL_SCHEMAS
}


def tool_schema_to_openai_function(tool_schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a registry/context schema into an OpenAI-compatible function tool."""

    return {
        "type": "function",
        "function": {
            "name": tool_schema["name"],
            "description": tool_schema.get("description") or "",
            "parameters": deepcopy(
                tool_schema.get("input_schema")
                or tool_schema.get("parameters")
                or {"type": "object", "properties": {}, "required": []}
            ),
        },
    }


def to_openai_function_tools(tool_schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [tool_schema_to_openai_function(schema) for schema in tool_schemas]


class ToolRegistry:
    """Registers structured tools for the runtime."""

    def __init__(
        self,
        tools: dict[str, ToolHandler] | None = None,
        schemas: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._tools = tools or {}
        self._schemas = schemas or {}

    def register(self, name: str, handler: ToolHandler, schema: dict[str, Any] | None = None) -> None:
        self._tools[name] = handler
        if schema is not None:
            self._schemas[name] = schema

    def execute(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        handler = self._tools.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")
        return handler(params)

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [self._schema_for(name) for name in self._tools]

    @property
    def openai_function_tools(self) -> list[dict[str, Any]]:
        return to_openai_function_tools(self.schemas)

    def _schema_for(self, name: str) -> dict[str, Any]:
        schema = self._schemas.get(name) or BUILTIN_TOOL_SCHEMAS_BY_NAME.get(name)
        if schema is not None:
            return deepcopy(schema)
        return {
            "name": name,
            "description": f"Execute the registered tool named {name}.",
            "input_schema": {
                "type": "object",
                "additionalProperties": True,
                "properties": {},
                "required": [],
            },
            "safety": ["No safety metadata is registered for this custom tool."],
            "hints": [],
        }
