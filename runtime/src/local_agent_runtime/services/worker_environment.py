from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path


DEFAULT_CHILD_TOOL_ALLOWLIST = (
    "list_dir",
    "search_files",
    "read_file",
    "git_status",
    "git_diff",
)

DEFAULT_ENV_ALLOWLIST = (
    "LOCAL_AGENT_PROVIDER_MODE",
    "LOCAL_AGENT_PROVIDER_API_KEY",
    "LOCAL_AGENT_OPENAI_API_KEY",
    "LOCAL_AGENT_PROVIDER_BASE_URL",
    "LOCAL_AGENT_PROVIDER_MODEL",
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
)

WINDOWS_RUNTIME_ENV = (
    "PATH",
    "SystemRoot",
    "TEMP",
    "TMP",
)

UNSAFE_CHILD_TOOLS = frozenset(
    {
        "task",
    }
)
KNOWN_CHILD_TOOLS = frozenset((*DEFAULT_CHILD_TOOL_ALLOWLIST, "run_command", "apply_patch"))


def build_child_worker_env(
    *,
    parent_env: Mapping[str, str],
    runtime_src: str | Path,
    db_path: str | os.PathLike[str] | None = None,
    database_path: str | os.PathLike[str] | None = None,
    allowlist: Iterable[str] | None = None,
    env_allowlist: Sequence[str] | None = None,
    tool_allowlist: Sequence[str] | str | None = None,
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
        value = _lookup_env_value(parent_env, key)
        if _env_value_present(value):
            env[key] = str(value)

    existing_python_path = _lookup_env_value(parent_env, "PYTHONPATH")
    python_path_entries = [runtime_src_path]
    if _env_value_present(existing_python_path):
        python_path_entries.append(str(existing_python_path))
    env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
    env["LOCAL_AGENT_DB_PATH"] = child_database_path
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
        if not name or name in seen:
            continue
        if name in UNSAFE_CHILD_TOOLS or name not in KNOWN_CHILD_TOOLS:
            raise ValueError(f"Tools not allowed for child workers: {name}")
        seen.add(name)
        normalized.append(name)
    return tuple(normalized)


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


def _lookup_env_value(env: Mapping[str, str], key: str) -> str | None:
    if key in env:
        return env[key]
    if os.name != "nt":
        return None

    normalized_key = key.upper()
    for candidate_key, value in env.items():
        if candidate_key.upper() == normalized_key:
            return value
    return None
