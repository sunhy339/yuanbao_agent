from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from enum import Enum
from typing import Any

from ..services.worker_environment import DEFAULT_CHILD_TOOL_ALLOWLIST
from .memory import MEMORY_TOOL_SCHEMAS
from .scratchpad_tool import SCRATCHPAD_TOOL_SCHEMAS

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class ToolRateLimitError(Exception):
    """Raised when a tool's per-session rate limit is exceeded."""


class ToolCategory(str, Enum):
    """Semantic category for a registered tool."""

    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    SEARCH = "search"
    EXECUTION = "execution"
    NETWORK = "network"
    GIT = "git"
    TASK = "task"
    NOTEBOOK = "notebook"
    MEMORY = "memory"


class SafetyLevel(str, Enum):
    """Risk classification for tool execution."""

    SAFE = "safe"
    MEDIUM = "medium"
    DANGEROUS = "dangerous"


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
        "safety": {
            "level": "safe",
            "requires_approval": False,
            "category": "file_read",
            "sandboxed": True,
            "notes": [
                "Read-only: does not modify files.",
                "Paths are resolved relative to workspaceRoot and rejected if they escape the workspace.",
            ],
        },
        "hints": [
            "Use path='.' with recursive=false for a quick top-level inventory.",
            "Set recursive=true and max_depth=2 or 3 when looking for likely source files.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 1,
            "estimated_duration_ms": 500,
        },
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
        "safety": {
            "level": "safe",
            "requires_approval": False,
            "category": "search",
            "sandboxed": True,
            "notes": [
                "Read-only: does not modify files.",
                "Large/binary files may be skipped or decoded with replacement by the runtime.",
            ],
        },
        "hints": [
            "Use mode='filename' when the user names a file or extension.",
            "Use max_results=8-20 for agent loops to keep context small.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 2,
            "estimated_duration_ms": 2000,
        },
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
        "safety": {
            "level": "safe",
            "requires_approval": False,
            "category": "file_read",
            "sandboxed": True,
            "notes": [
                "Read-only: does not modify files.",
                "Do not use this tool to inspect secrets or unrelated private files.",
            ],
        },
        "hints": [
            "Read the smallest relevant file first.",
            "Set max_bytes for large generated files or logs.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 1,
            "estimated_duration_ms": 500,
        },
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
        "safety": {
            "level": "medium",
            "requires_approval": False,
            "category": "task",
            "sandboxed": False,
            "notes": [
                "Creates collaboration records and agent messages, but does not spawn a separate process in this slice.",
                "The child task is executed through an in-process runner boundary with retry and timeout policy.",
            ],
        },
        "hints": [
            "Use this when you want a structured child collaboration task instead of a shell command.",
            "Keep prompts short and action-oriented so the child task result stays focused.",
        ],
        "metadata": {
            "rate_limit": 10,
            "cost_per_use": 10,
            "estimated_duration_ms": 30000,
        },
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
                "background": {
                    "type": "boolean",
                    "description": "If true, schedule the command as a background runtime job and return immediately.",
                    "default": False,
                },
                "backgroundJob": {
                    "description": "Alias for background mode; accepts a boolean or an object with enabled=true/false.",
                    "oneOf": [
                        {
                            "type": "boolean",
                        },
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "enabled": {
                                    "type": "boolean",
                                    "default": True,
                                }
                            },
                        },
                    ],
                },
                "runInBackground": {
                    "type": "boolean",
                    "description": "Secondary alias for background mode.",
                    "default": False,
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
        "safety": {
            "level": "dangerous",
            "requires_approval": True,
            "category": "execution",
            "sandboxed": False,
            "notes": [
                "Dangerous: commands may modify files, execute code, access network, or delete data.",
                "Requires approval under the default policy before execution.",
                "Never use interactive commands, background daemons, destructive deletes, shutdowns, or formatting commands.",
            ],
        },
        "hints": [
            "Prefer read-only commands first, such as tests, git status, or file listings.",
            "Use explicit timeouts for long-running test/build commands.",
            "Set background=true when the command should keep running while the runtime continues other work.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 5,
            "estimated_duration_ms": 10000,
        },
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
        "safety": {
            "level": "dangerous",
            "requires_approval": True,
            "category": "file_write",
            "sandboxed": True,
            "notes": [
                "Dangerous: can modify workspace files and overwrite user changes if the patch is wrong.",
                "Requires approval under the default policy before file changes are applied.",
                "Patch paths must remain inside workspaceRoot.",
            ],
        },
        "hints": [
            "Keep patches small and focused.",
            "Prefer unified diffs for targeted edits and files[] for new small files.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 3,
            "estimated_duration_ms": 3000,
        },
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
        "safety": {
            "level": "safe",
            "requires_approval": False,
            "category": "git",
            "sandboxed": True,
            "notes": [
                "Read-only: runs git status and does not modify repository state.",
                "cwd is constrained to workspaceRoot.",
            ],
        },
        "hints": [
            "Use before editing when the user warns about parallel workers.",
            "Use cwd for monorepos with nested repositories.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 1,
            "estimated_duration_ms": 1000,
        },
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
        "safety": {
            "level": "safe",
            "requires_approval": False,
            "category": "git",
            "sandboxed": True,
            "notes": [
                "Read-only: runs git diff and does not modify repository state.",
                "Path filters are constrained to workspaceRoot.",
            ],
        },
        "hints": [
            "Use staged=true to review the index before commit.",
            "Pass path for focused review of a single file or subtree.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 1,
            "estimated_duration_ms": 1000,
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create or replace a workspace file with the given content. Creates parent directories automatically "
            "unless create_dirs is set to false."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspaceRoot": WORKSPACE_ROOT_PROPERTY,
                "path": _string_property(
                    "Workspace-relative file path to create or replace.",
                    examples=["src/utils.py", "config/settings.json"],
                ),
                "content": {
                    "type": "string",
                    "description": "Complete file content to write.",
                },
                "encoding": {
                    "type": "string",
                    "description": "Text encoding for the file.",
                    "default": "utf-8",
                },
                "create_dirs": {
                    "type": "boolean",
                    "description": "If true, create parent directories if they do not exist.",
                    "default": True,
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "If true, allow overwriting existing files.",
                    "default": True,
                },
            },
            "required": ["workspaceRoot", "path", "content"],
        },
        "safety": {
            "level": "dangerous",
            "requires_approval": True,
            "category": "file_write",
            "sandboxed": True,
            "notes": [
                "Dangerous: can create new files or overwrite existing workspace files.",
                "Paths must stay inside workspaceRoot.",
            ],
        },
        "hints": [
            "Prefer apply_patch for small edits to existing files.",
            "Use write_file for creating new files or full replacements.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 2,
            "estimated_duration_ms": 1000,
        },
    },
    {
        "name": "web_fetch",
        "description": (
            "Fetch content from a URL using HTTP GET or POST. Returns the response body as text "
            "with status code and content type metadata."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": _string_property(
                    "HTTP or HTTPS URL to fetch.",
                    examples=["https://api.example.com/data", "https://docs.python.org/3/"],
                ),
                "method": {
                    "type": "string",
                    "description": "HTTP method.",
                    "enum": ["GET", "POST", "PUT", "DELETE", "HEAD"],
                    "default": "GET",
                },
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers.",
                    "additionalProperties": {"type": "string"},
                },
                "body": {
                    "description": "Optional request body; string or JSON-serializable object.",
                    "oneOf": [{"type": "string"}, {"type": "object"}],
                },
                "timeout": {
                    "type": "integer",
                    "description": "Request timeout in seconds.",
                    "minimum": 5,
                    "maximum": 120,
                    "default": 30,
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum response bytes to read.",
                    "minimum": 1024,
                    "maximum": 2097152,
                    "default": 524288,
                },
            },
            "required": ["url"],
        },
        "safety": {
            "level": "medium",
            "requires_approval": False,
            "category": "network",
            "sandboxed": False,
            "notes": [
                "Makes outbound network requests to the specified URL.",
                "Does not send credentials or cookies.",
                "Response size is capped by max_bytes.",
            ],
        },
        "hints": [
            "Use for fetching documentation, API responses, or public web content.",
            "Set max_bytes lower for known small responses.",
        ],
        "metadata": {
            "rate_limit": 20,
            "cost_per_use": 3,
            "estimated_duration_ms": 5000,
        },
    },
    {
        "name": "code_search",
        "description": (
            "AST-aware code search across the workspace. Supports finding definitions, references, symbols, "
            "and text chunks. More precise than search_files for code navigation."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspaceRoot": WORKSPACE_ROOT_PROPERTY,
                "query": _string_property(
                    "Symbol name or text pattern to search for.",
                    examples=["ToolRegistry", "handle_request", "MyComponent"],
                ),
                "mode": {
                    "type": "string",
                    "description": "Search mode: definition, reference, symbol, or chunk.",
                    "enum": ["definition", "reference", "symbol", "chunk"],
                    "default": "definition",
                },
                "path": _string_property(
                    "Workspace-relative directory to scope the search.",
                    default=".",
                    examples=[".", "src", "runtime/src"],
                ),
                "glob": {
                    "type": "array",
                    "description": "File glob patterns to include.",
                    "items": {"type": "string", "minLength": 1},
                    "default": ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.rs", "**/*.go"],
                    "examples": [["**/*.py"], ["src/**/*.ts"]],
                },
                "ignore": {
                    "type": "array",
                    "description": "Additional ignore patterns.",
                    "items": {"type": "string", "minLength": 1},
                    "default": [],
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum directory depth.",
                    "minimum": 1,
                    "maximum": 8,
                    "default": 6,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results.",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 30,
                },
            },
            "required": ["workspaceRoot", "query"],
        },
        "safety": {
            "level": "safe",
            "requires_approval": False,
            "category": "search",
            "sandboxed": True,
            "notes": [
                "Read-only: does not modify files.",
                "Search scope is constrained to workspaceRoot.",
            ],
        },
        "hints": [
            "Use mode='definition' to find class/function declarations.",
            "Use mode='reference' to find where a symbol is used.",
            "Use mode='symbol' for a combined definition + reference search.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 3,
            "estimated_duration_ms": 3000,
        },
    },
    {
        "name": "notebook",
        "description": (
            "Read and execute Jupyter notebook (.ipynb) cells. List cells, read cell content and outputs, "
            "or execute code cells in a subprocess."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspaceRoot": WORKSPACE_ROOT_PROPERTY,
                "path": _string_property(
                    "Workspace-relative path to the .ipynb file.",
                    examples=["analysis.ipynb", "notebooks/experiment.ipynb"],
                ),
                "action": {
                    "type": "string",
                    "description": "Action to perform on the notebook.",
                    "enum": ["list_cells", "get_cell", "execute_cell"],
                    "default": "list_cells",
                },
                "cell_index": {
                    "type": "integer",
                    "description": "Cell index for get_cell or execute_cell actions.",
                    "minimum": 0,
                },
                "timeout": {
                    "type": "integer",
                    "description": "Execution timeout in seconds for execute_cell.",
                    "minimum": 5,
                    "maximum": 300,
                    "default": 60,
                },
            },
            "required": ["workspaceRoot", "path"],
        },
        "safety": {
            "level": "medium",
            "requires_approval": False,
            "category": "notebook",
            "sandboxed": True,
            "notes": [
                "execute_cell runs code in a subprocess — treat it like run_command.",
                "Read-only actions (list_cells, get_cell) are safe.",
            ],
        },
        "hints": [
            "Use list_cells first to see the notebook structure.",
            "Use get_cell to inspect a specific cell's source and outputs.",
            "Use execute_cell only when you need to re-run a code cell.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 5,
            "estimated_duration_ms": 5000,
        },
    },
    {
        "name": "browser",
        "description": (
            "Fetch a web page and extract its readable text content, or return the raw HTML. "
            "Useful for reading documentation, articles, or API pages."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": _string_property(
                    "HTTP or HTTPS URL to browse.",
                    examples=["https://docs.python.org/3/", "https://example.com"],
                ),
                "action": {
                    "type": "string",
                    "description": "read extracts text, raw returns full HTML.",
                    "enum": ["read", "raw"],
                    "default": "read",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Request timeout in seconds.",
                    "minimum": 5,
                    "maximum": 120,
                    "default": 30,
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum response bytes to read.",
                    "minimum": 4096,
                    "maximum": 2097152,
                    "default": 524288,
                },
            },
            "required": ["url"],
        },
        "safety": {
            "level": "medium",
            "requires_approval": False,
            "category": "network",
            "sandboxed": False,
            "notes": [
                "Makes outbound network requests.",
                "Does not execute JavaScript — only fetches static HTML.",
                "Response size is capped by max_bytes.",
            ],
        },
        "hints": [
            "Use action='read' for extracting article or documentation text.",
            "Use action='raw' when you need the HTML structure.",
        ],
        "metadata": {
            "rate_limit": 20,
            "cost_per_use": 3,
            "estimated_duration_ms": 5000,
        },
    },
]

BUILTIN_TOOL_SCHEMAS.extend(MEMORY_TOOL_SCHEMAS)
BUILTIN_TOOL_SCHEMAS.extend(SCRATCHPAD_TOOL_SCHEMAS)

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
    """Registers structured tools for the runtime.

    Enhanced with:
    - Parameter validation against JSON Schema ``required`` fields
    - Unified error wrapping on tool exceptions
    - ``has_tool()`` / ``list_tools()`` helpers
    """

    def __init__(
        self,
        tools: dict[str, ToolHandler] | None = None,
        schemas: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._tools = tools or {}
        self._schemas = schemas or {}
        self._call_counts: dict[str, dict[str, int]] = {}

    # ── registration ────────────────────────────────────────────────

    def register(self, name: str, handler: ToolHandler, schema: dict[str, Any] | None = None) -> None:
        self._tools[name] = handler
        if schema is not None:
            self._schemas[name] = schema

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        self._schemas.pop(name, None)

    def unregister_prefix(self, prefix: str) -> int:
        """Remove all tools whose name starts with *prefix*.  Returns count removed."""
        names = [n for n in self._tools if n.startswith(prefix)]
        for n in names:
            self._tools.pop(n, None)
            self._schemas.pop(n, None)
        return len(names)

    # ── query ───────────────────────────────────────────────────────

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[dict[str, Any]]:
        """Return lightweight tool descriptors for discovery."""
        result: list[dict[str, Any]] = []
        for name in self._tools:
            schema = self._schema_for(name)
            result.append(
                {
                    "name": name,
                    "description": schema.get("description", ""),
                    "safety": schema.get("safety", {}),
                    "metadata": schema.get("metadata", {}),
                }
            )
        return result

    # ── execution ───────────────────────────────────────────────────

    def check_rate_limit(self, name: str, session_id: str) -> bool:
        """Return True if the tool call is within rate limits for the session."""
        schema = self._schema_for(name)
        limit = schema.get("metadata", {}).get("rate_limit")
        if limit is None:
            return True
        count = self._call_counts.get(session_id, {}).get(name, 0)
        return count < limit

    def reset_session(self, session_id: str) -> None:
        """Clear per-session call counts."""
        self._call_counts.pop(session_id, None)

    def execute(self, name: str, params: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
        handler = self._tools.get(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")

        # Rate limit enforcement
        if session_id:
            if not self.check_rate_limit(name, session_id):
                limit = self._schema_for(name).get("metadata", {}).get("rate_limit")
                raise ToolRateLimitError(
                    f"工具 '{name}' 在会话 {session_id} 中已超过调用次数限制（{limit}）"
                )
            self._call_counts.setdefault(session_id, {})[name] = \
                self._call_counts.get(session_id, {}).get(name, 0) + 1

        self._validate_required(name, params)

        try:
            result = handler(params)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "failed",
                "error": str(exc),
                "toolName": name,
            }
        return result

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [self._schema_for(name) for name in self._tools]

    @property
    def openai_function_tools(self) -> list[dict[str, Any]]:
        return to_openai_function_tools(self.schemas)

    def _schema_for(self, name: str) -> dict[str, Any]:
        schema = self._schemas.get(name) or BUILTIN_TOOL_SCHEMAS_BY_NAME.get(name)
        if schema is not None:
            return schema
        return {
            "name": name,
            "description": f"Execute the registered tool named {name}.",
            "input_schema": {
                "type": "object",
                "additionalProperties": True,
                "properties": {},
                "required": [],
            },
            "safety": {
                "level": "medium",
                "requires_approval": False,
                "category": "task",
                "sandboxed": False,
                "notes": ["No safety metadata is registered for this custom tool."],
            },
            "hints": [],
        }

    def _validate_required(self, name: str, params: dict[str, Any]) -> None:
        schema = self._schema_for(name)
        input_schema = schema.get("input_schema") or {}
        required = list(input_schema.get("required") or [])
        # oneOf: at least one branch must be fully satisfied
        one_of = input_schema.get("oneOf")
        if one_of:
            branch_ok = any(
                all(field in params for field in branch.get("required", []))
                for branch in one_of
            )
            if not branch_ok:
                branch_names = [
                    ", ".join(branch.get("required", [])) for branch in one_of
                ]
                raise ValueError(
                    f"Missing required parameters for {name}: need one of ({'; '.join(branch_names)})"
                )
        missing = [field for field in required if field not in params]
        if missing:
            raise ValueError(f"Missing required parameters for {name}: {', '.join(missing)}")
