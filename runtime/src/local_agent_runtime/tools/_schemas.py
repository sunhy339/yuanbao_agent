"""Built-in tool schema definitions and OpenAI conversion helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..services.worker_environment import DEFAULT_CHILD_TOOL_ALLOWLIST
from .memory import MEMORY_TOOL_SCHEMAS
from .scratchpad_tool import SCRATCHPAD_TOOL_SCHEMAS


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

CHILD_TOOL_ALLOWLIST_TOOL_NAMES = [*DEFAULT_CHILD_TOOL_ALLOWLIST, "run_command", "apply_patch", "write_file"]


def _child_tool_allowlist_property() -> dict[str, Any]:
    return {
        "type": "array",
        "description": (
            "Optional child-worker tool allowlist. Defaults to read-only tools; include run_command, apply_patch, "
            "or write_file to extend the child with command execution or file edits."
        ),
        "items": {
            "type": "string",
            "enum": CHILD_TOOL_ALLOWLIST_TOOL_NAMES,
        },
        "uniqueItems": True,
        "default": list(DEFAULT_CHILD_TOOL_ALLOWLIST),
        "examples": [["list_dir", "search_files", "read_file"], ["read_file", "run_command", "apply_patch", "write_file"]],
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
        "name": "ask_user_question",
        "description": (
            "Ask the user for missing information and pause the current task until they answer. "
            "Use this when a decision, requirement, credential, preference, or scope choice cannot be inferred safely."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "questions": {
                    "type": "array",
                    "description": "One to three short questions for the user.",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Stable snake_case identifier for mapping the answer.",
                                "minLength": 1,
                                "maxLength": 64,
                                "examples": ["target_scope"],
                            },
                            "header": {
                                "type": "string",
                                "description": "Short UI header for the question.",
                                "minLength": 1,
                                "maxLength": 80,
                                "examples": ["Scope"],
                            },
                            "question": {
                                "type": "string",
                                "description": "The question shown to the user.",
                                "minLength": 1,
                                "maxLength": 1000,
                            },
                            "options": {
                                "type": "array",
                                "description": "Optional mutually exclusive choices. Mark one as recommended when helpful.",
                                "maxItems": 5,
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "label": {"type": "string", "minLength": 1, "maxLength": 80},
                                        "value": {"type": "string", "minLength": 1, "maxLength": 120},
                                        "description": {"type": "string", "maxLength": 240},
                                        "recommended": {"type": "boolean", "default": False},
                                    },
                                    "required": ["label"],
                                },
                            },
                        },
                        "required": ["id", "question"],
                    },
                },
                "question": {
                    "type": "string",
                    "description": "Compatibility field for a single question; prefer questions[].",
                    "minLength": 1,
                    "maxLength": 1000,
                },
                "options": {
                    "type": "array",
                    "description": "Compatibility field for choices for the single question.",
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "label": {"type": "string", "minLength": 1, "maxLength": 80},
                            "value": {"type": "string", "minLength": 1, "maxLength": 120},
                            "description": {"type": "string", "maxLength": 240},
                            "recommended": {"type": "boolean", "default": False},
                        },
                        "required": ["label"],
                    },
                },
                "summary": {
                    "type": "string",
                    "description": "Brief summary shown in the pause card.",
                    "maxLength": 1000,
                },
                "reason": {
                    "type": "string",
                    "description": "Why the user answer is needed.",
                    "maxLength": 240,
                    "default": "needs_user_input",
                },
                "resumePolicy": {
                    "type": "string",
                    "description": "How the task should resume after the user answers.",
                    "default": "requires_user_follow_up",
                    "examples": ["requires_user_follow_up", "requires_user_budget_update"],
                },
            },
        },
        "safety": {
            "level": "safe",
            "requires_approval": False,
            "category": "task",
            "sandboxed": False,
            "notes": [
                "Does not modify files or execute code.",
                "Pauses the current ReAct task until the user supplies an answer.",
            ],
        },
        "hints": [
            "Ask only when continuing would require guessing user intent.",
            "Prefer one focused question; use up to three only when the choices are independent.",
            "Include options for common paths and mark a recommended option when there is a clear default.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 0,
            "estimated_duration_ms": 1000,
        },
    },
    {
        "name": "enter_plan_mode",
        "description": (
            "Enter a read-only planning mode for the current task. Use this before making changes when the user asked "
            "for a plan-first workflow or when the task needs exploration before execution."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Short reason why plan mode is needed.",
                    "maxLength": 500,
                },
            },
        },
        "safety": {
            "level": "safe",
            "requires_approval": False,
            "category": "task",
            "sandboxed": False,
            "notes": [
                "Does not modify files or execute code.",
                "Restricts subsequent model-visible tools to read-only tools plus exit_plan_mode.",
            ],
        },
        "hints": [
            "Use read-only tools while in plan mode.",
            "Call exit_plan_mode with a clear plan when ready for user approval.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 0,
            "estimated_duration_ms": 1000,
        },
    },
    {
        "name": "exit_plan_mode",
        "description": (
            "Submit the proposed execution plan for user approval and pause the task until the user approves or rejects it."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Short summary of the proposed plan.",
                    "maxLength": 1200,
                },
                "plan": {
                    "description": "Structured plan object or plan text.",
                    "oneOf": [
                        {
                            "type": "object",
                            "additionalProperties": True,
                            "properties": {
                                "summary": {"type": "string", "maxLength": 1200},
                                "steps": {
                                    "type": "array",
                                    "items": {
                                        "oneOf": [
                                            {"type": "string", "maxLength": 500},
                                            {
                                                "type": "object",
                                                "additionalProperties": True,
                                                "properties": {
                                                    "title": {"type": "string", "maxLength": 200},
                                                    "description": {"type": "string", "maxLength": 500},
                                                },
                                            },
                                        ],
                                    },
                                },
                            },
                        },
                        {"type": "string", "maxLength": 4000},
                    ],
                },
                "steps": {
                    "type": "array",
                    "description": "Compatibility field for plan steps when plan is not an object.",
                    "items": {"type": "string", "maxLength": 500},
                    "maxItems": 20,
                },
                "risks": {
                    "type": "array",
                    "description": "Optional known risks or assumptions.",
                    "items": {"type": "string", "maxLength": 300},
                    "maxItems": 10,
                },
                "goal": {
                    "type": "string",
                    "description": "Original or refined goal for the plan approval card.",
                    "maxLength": 1000,
                },
                "taskId": {
                    "type": "string",
                    "description": "Runtime task id injected by the orchestrator; models usually omit this.",
                },
                "approvalId": {
                    "type": "string",
                    "description": "Approval id supplied when resuming after the user approved or rejected the plan.",
                },
            },
        },
        "safety": {
            "level": "safe",
            "requires_approval": False,
            "category": "task",
            "sandboxed": False,
            "notes": [
                "Does not execute the plan itself.",
                "Creates a plan approval and pauses until the user decides.",
            ],
        },
        "hints": [
            "Make the plan specific enough for the user to approve.",
            "After approval, continue with the original task using the approved plan as constraints.",
        ],
        "metadata": {
            "rate_limit": None,
            "cost_per_use": 0,
            "estimated_duration_ms": 1000,
        },
    },
    {
        "name": "agent",
        "description": (
            "Delegate a focused job to a child agent. This is the Claude Code style AgentTool wrapper over the "
            "runtime child-task system; it records a child task, runs it through the subagent boundary, and returns "
            "the child result."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "prompt": _string_property(
                    "Instruction for the child agent.",
                    examples=["Inspect the current docs structure and summarize missing sections."],
                ),
                "agent_type": _string_property(
                    "Child agent role.",
                    default="explorer",
                    examples=["explorer", "analyst", "coder", "reviewer", "summarizer"],
                ),
                "agentType": _string_property(
                    "Camel-case compatibility alias for agent_type.",
                    default="explorer",
                ),
                "title": _string_property(
                    "Optional short title for the delegated job.",
                    examples=["Inspect docs structure"],
                ),
                "cwd": _string_property(
                    "Optional preferred working directory for the child agent. The runtime keeps this as child profile metadata.",
                    examples=["D:/projects/app", "/workspace/app"],
                ),
                "mode": {
                    "type": "string",
                    "description": "Requested child execution mode.",
                    "enum": ["read_only", "default", "write", "review", "summarize"],
                    "default": "read_only",
                },
                "tool_allowlist": _child_tool_allowlist_property(),
                "toolAllowlist": _child_tool_allowlist_property(),
                "budget": {
                    "type": "object",
                    "description": "Optional advisory budget for the child agent.",
                    "additionalProperties": True,
                    "properties": {
                        "maxTokens": {"type": "integer", "minimum": 1},
                        "remainingTokens": {"type": "integer", "minimum": 0},
                        "maxToolCalls": {"type": "integer", "minimum": 1},
                        "tool_allowlist": _child_tool_allowlist_property(),
                        "toolAllowlist": _child_tool_allowlist_property(),
                    },
                },
                "plan_mode_required": {
                    "type": "boolean",
                    "description": "Whether the child agent should plan before writing. Stored as child profile metadata.",
                    "default": False,
                },
                "planModeRequired": {
                    "type": "boolean",
                    "description": "Camel-case compatibility alias for plan_mode_required.",
                    "default": False,
                },
                "model": _string_property("Optional child model override."),
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
                "Delegates through the same permission-gated subagent dispatch capability as task.",
                "Children default to the read-only allowlist unless tool_allowlist explicitly includes write or command tools.",
            ],
        },
        "hints": [
            "Use this for focused investigation, review, summarization, or bounded implementation subtasks.",
            "Prefer the default read-only allowlist unless the child agent must edit files.",
        ],
        "metadata": {
            "rate_limit": 10,
            "cost_per_use": 10,
            "estimated_duration_ms": 30000,
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
            "For brand-new large files or full-file replacements, prefer write_file instead of a large patchText payload.",
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
                "taskId": {
                    "type": "string",
                    "description": "Runtime task id injected by the orchestrator; models usually omit this.",
                },
                "approvalId": {
                    "type": "string",
                    "description": "Approval id supplied when retrying an approved file write.",
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
            "Use write_file for creating new files, large files, or full replacements.",
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
            "requires_approval": True,
            "category": "notebook",
            "sandboxed": True,
            "notes": [
                "execute_cell runs code in a subprocess and is gated by run_command approval.",
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
    {
        "name": "computer_use",
        "description": (
            "Request permission for a desktop or browser computer-use action. "
            "The runtime pauses for user approval before any action is executed."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Computer-use action to request.",
                    "enum": ["inspect", "screenshot", "click", "type", "key", "scroll"],
                },
                "target": _string_property(
                    "Target app, browser tab, window, or surface.",
                    default="desktop",
                    examples=["VS Code", "Browser", "desktop"],
                ),
                "permission": _string_property(
                    "Short user-facing description of the requested permission.",
                    examples=["Read the current VS Code window", "Click the Save button"],
                ),
                "details": {
                    "type": "string",
                    "description": "Optional extra details shown in the permission card.",
                },
                "selector": {
                    "type": "string",
                    "description": "Optional target selector or accessibility label.",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type when action is type.",
                },
                "x": {"type": "number", "description": "Optional x coordinate."},
                "y": {"type": "number", "description": "Optional y coordinate."},
                "direction": {
                    "type": "string",
                    "description": "Scroll direction when action is scroll.",
                    "enum": ["up", "down", "left", "right"],
                },
                "amount": {
                    "type": "number",
                    "description": "Optional scroll/click magnitude.",
                },
                "url": {
                    "type": "string",
                    "description": "Optional browser page URL associated with the target.",
                },
                "pageId": {
                    "type": "string",
                    "description": "Optional host/browser page identifier for injected browser executors.",
                },
                "browserContextId": {
                    "type": "string",
                    "description": "Optional host/browser context identifier for injected browser executors.",
                },
                "approvalId": {
                    "type": "string",
                    "description": "Runtime-supplied approval id when resuming after approval.",
                },
            },
            "required": ["action", "target", "permission"],
        },
        "safety": {
            "level": "dangerous",
            "requires_approval": True,
            "category": "computer_use",
            "sandboxed": False,
            "notes": [
                "Always requires explicit user approval.",
                "inspect returns runtime environment information, or browser page title/text/elements when a browser executor is available; screenshot attempts a local or browser screenshot backend.",
                "click/type/key/scroll execute only when a desktop-control or injected browser/accessibility backend is available; otherwise they return a blocked result with recovery guidance.",
            ],
        },
        "hints": [
            "Use inspect when you need permission to look at the active app/window.",
            "Use inspect with target='browser' and url/pageId/browserContextId when you need the current page title, text, and interactive element hints.",
            "For click, provide x/y coordinates unless a browser DOM/accessibility executor with click.selector support is available.",
            "For selector-based click/type/scroll, include url/pageId/browserContextId when known; without an injected browser DOM/accessibility executor the runtime will block with selector_executor_required.",
            "Set LOCAL_AGENT_COMPUTER_USE_PLAYWRIGHT=1 with the optional computer-use-browser extra/playwright package installed to enable the built-in browser session executor.",
            "Use a clear permission string so the user knows exactly what is being requested.",
        ],
        "metadata": {
            "rate_limit": 12,
            "cost_per_use": 10,
            "estimated_duration_ms": 1000,
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
