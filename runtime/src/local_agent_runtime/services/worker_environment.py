from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from .runtime_dependencies import lookup_env_value, resolve_node_executable


DEFAULT_CHILD_TOOL_ALLOWLIST = (
    "list_dir",
    "search_files",
    "read_file",
    "git_status",
    "git_diff",
    "code_search",
    "web_fetch",
    "browser",
    "ask_user_question",
)

DEFAULT_CHILD_LOCAL_TOOL_ALLOWLIST = (
    "list_dir",
    "search_files",
    "read_file",
    "git_status",
    "git_diff",
    "code_search",
)

DEFAULT_ENV_ALLOWLIST = (
    "LOCAL_AGENT_PROVIDER_MODE",
    "LOCAL_AGENT_PROVIDER_API_KEY",
    "LOCAL_AGENT_OPENAI_API_KEY",
    "LOCAL_AGENT_PROVIDER_BASE_URL",
    "LOCAL_AGENT_PROVIDER_MODEL",
    "LOCAL_AGENT_CHILD_PROVIDER_MODEL",
    "LOCAL_AGENT_PROVIDER_TEMPERATURE",
    "LOCAL_AGENT_PROVIDER_MAX_TOKENS",
    "LOCAL_AGENT_PROVIDER_TIMEOUT",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "OPENAI_TEMPERATURE",
    "OPENAI_MAX_TOKENS",
    "OPENAI_TIMEOUT",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "XAI_API_KEY",
    "GROQ_API_KEY",
    "COHERE_API_KEY",
    "TOGETHER_API_KEY",
    "PERPLEXITY_API_KEY",
    "YUANBAO_PROVIDER_MODE",
    "YUANBAO_SMOKE_PROVIDER_API_KEY",
    "YUANBAO_SMOKE_PROVIDER_BASE_URL",
    "YUANBAO_SMOKE_PROVIDER_MODEL",
    "YUANBAO_SMOKE_PROVIDER_TIMEOUT",
    "YUANBAO_SMOKE_PROVIDER_MAX_TOKENS",
    "YUANBAO_SMOKE_PROVIDER_API_FORMAT",
    "YUANBAO_SMOKE_PROVIDER_MODE",
    "YUANBAO_SMOKE_PROVIDER_NAME",
    "LOCAL_AGENT_NODE_EXECUTABLE",
    "LOCAL_AGENT_REPO_ROOT",
)

DEFAULT_CHILD_PROVIDER_MODEL_MAP = {
    "gpt-5.4": "gpt-5.4-mini",
}

WINDOWS_RUNTIME_ENV = (
    "PATH",
    "SystemRoot",
    "TEMP",
    "TMP",
)

UNSAFE_CHILD_TOOLS = frozenset(
    {
        "agent",
        "task",
    }
)
KNOWN_CHILD_TOOLS = frozenset((*DEFAULT_CHILD_TOOL_ALLOWLIST, "run_command", "apply_patch", "write_file"))

# Tool alias map: common aliases -> canonical tool names
TOOL_ALIAS_MAP: dict[str, str] = {
    "rg": "search_files",
    "grep": "search_files",
    "search": "search_files",
    "cat": "read_file",
    "read": "read_file",
    "git status": "git_status",
    "status": "git_status",
    "git diff": "git_diff",
    "diff": "git_diff",
    "shell": "run_command",
    "command": "run_command",
    "patch": "apply_patch",
}


def resolve_tool_alias(name: str) -> str:
    """Resolve a tool alias to its canonical name.

    If the name is a known alias, return the canonical tool name.
    Otherwise return the name unchanged.
    """
    return TOOL_ALIAS_MAP.get(name, name)


def build_child_worker_env(
    *,
    parent_env: Mapping[str, str],
    runtime_src: str | Path,
    db_path: str | os.PathLike[str] | None = None,
    database_path: str | os.PathLike[str] | None = None,
    allowlist: Iterable[str] | None = None,
    env_allowlist: Sequence[str] | None = None,
    tool_allowlist: Sequence[str] | str | None = None,
    child_model: str | None = None,
) -> dict[str, str]:
    child_database_path = _required_path_text(
        db_path if db_path is not None else database_path,
        name="db_path",
    )
    runtime_src_path = _required_path_text(runtime_src, name="runtime_src")
    allowed = set(DEFAULT_ENV_ALLOWLIST)
    allowed.update(_normalize_env_allowlist(env_allowlist))
    allowed.update(_normalize_env_allowlist(allowlist))
    if os.name == "nt":
        allowed.update(WINDOWS_RUNTIME_ENV)

    env: dict[str, str] = {}
    for key in allowed:
        value = lookup_env_value(parent_env, key)
        if _env_value_present(value):
            env[key] = str(value)

    if isinstance(child_model, str) and child_model.strip():
        normalized_child_model = child_model.strip()
        env["LOCAL_AGENT_PROVIDER_MODEL"] = normalized_child_model
        env["LOCAL_AGENT_CHILD_PROVIDER_MODEL"] = normalized_child_model

    node_executable = _node_executable(parent_env)
    if node_executable:
        env["LOCAL_AGENT_NODE_EXECUTABLE"] = node_executable
        node_dir = str(Path(node_executable).parent)
        existing_path = env.get("PATH")
        if existing_path:
            path_entries = existing_path.split(os.pathsep)
            if node_dir not in path_entries:
                env["PATH"] = os.pathsep.join([node_dir, *path_entries])
        else:
            env["PATH"] = node_dir

    existing_python_path = lookup_env_value(parent_env, "PYTHONPATH")
    python_path_entries = [runtime_src_path]
    if _env_value_present(existing_python_path):
        python_path_entries.append(str(existing_python_path))
    env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
    env["LOCAL_AGENT_DB_PATH"] = child_database_path
    env["LOCAL_AGENT_CHILD_WORKER"] = "1"
    env["LOCAL_AGENT_PYTHON_EXECUTABLE"] = sys.executable
    env["LOCAL_AGENT_RECOMMENDED_PYTEST_COMMAND"] = _recommended_python_module_command(sys.executable, "pytest", "-q")
    env["PYTHONUNBUFFERED"] = "1"
    env["LOCAL_AGENT_CHILD_TOOL_ALLOWLIST"] = ",".join(normalize_child_tool_allowlist(tool_allowlist))
    return env


def normalize_child_tool_allowlist(value: Sequence[str] | str | None = None) -> tuple[str, ...]:
    if value is None:
        raw_items: Sequence[str] = DEFAULT_CHILD_TOOL_ALLOWLIST
    elif isinstance(value, str):
        raw_items = value.split(",")
    else:
        raw_items = value

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        name = str(item).strip()
        if not name:
            continue
        if name == "mcp__*" or name.startswith("mcp__"):
            if name in seen:
                continue
            seen.add(name)
            normalized.append(name)
            continue
        # Resolve alias before validation
        name = resolve_tool_alias(name)
        if name in seen:
            continue
        if name in UNSAFE_CHILD_TOOLS or name not in KNOWN_CHILD_TOOLS:
            raise ValueError(f"Tools not allowed for child workers: {name}")
        seen.add(name)
        normalized.append(name)
    if any(name in {"run_command", "apply_patch", "write_file"} for name in normalized):
        expanded: list[str] = []
        expanded_seen: set[str] = set()
        for name in (*DEFAULT_CHILD_LOCAL_TOOL_ALLOWLIST, *normalized):
            if name in expanded_seen:
                continue
            expanded_seen.add(name)
            expanded.append(name)
        normalized = expanded
    return tuple(normalized)


def _recommended_python_module_command(python_executable: str, module: str, *args: str) -> str:
    executable = str(python_executable).strip()
    if not executable:
        executable = "python"
    quoted = f'"{executable}"' if any(ch.isspace() for ch in executable) else executable
    if os.name == "nt" and quoted.startswith('"'):
        quoted = f"& {quoted}"
    return " ".join([quoted, "-m", module, *args])


def _required_path_text(path: str | os.PathLike[str] | None, *, name: str) -> str:
    if path is None:
        raise ValueError(f"{name} is required")
    text = str(Path(path)) if isinstance(path, os.PathLike) else str(path)
    if not text.strip():
        raise ValueError(f"{name} is required")
    return text


def _normalize_env_allowlist(value: Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    names: list[str] = []
    for item in value:
        name = str(item).strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _env_value_present(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _node_executable(parent_env: Mapping[str, str]) -> str | None:
    return resolve_node_executable(parent_env)


def resolve_child_provider_model(
    *,
    parent_env: Mapping[str, str],
    provider_config: Mapping[str, object] | None = None,
    explicit_model: str | None = None,
) -> str | None:
    if isinstance(explicit_model, str) and explicit_model.strip():
        return explicit_model.strip()

    config = provider_config if isinstance(provider_config, Mapping) else {}
    configured_child_model = _string_config_value(config, "childModel", "subagentModel")
    if configured_child_model:
        return configured_child_model

    parent_model = (
        lookup_env_value(parent_env, "LOCAL_AGENT_PROVIDER_MODEL")
        or _string_config_value(config, "model", "defaultModel")
    )
    if not isinstance(parent_model, str) or not parent_model.strip():
        return None
    normalized_parent_model = parent_model.strip()

    configured_map = _normalize_model_map(
        config.get("childModelMap") or config.get("subagentModelMap"),
    )
    mapped_model = configured_map.get(normalized_parent_model)
    if mapped_model:
        return mapped_model

    return DEFAULT_CHILD_PROVIDER_MODEL_MAP.get(normalized_parent_model)


def _string_config_value(source: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_model_map(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        mapped = str(raw_value).strip()
        if key and mapped:
            normalized[key] = mapped
    return normalized
