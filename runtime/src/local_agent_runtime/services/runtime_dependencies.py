from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path


def lookup_env_value(env: Mapping[str, str], key: str) -> str | None:
    if key in env:
        return env[key]
    if os.name != "nt":
        return None

    normalized_key = key.upper()
    for candidate_key, value in env.items():
        if candidate_key.upper() == normalized_key:
            return value
    return None


def resolve_node_executable(env: Mapping[str, str] | None = None) -> str | None:
    source_env = env if env is not None else os.environ
    configured = lookup_env_value(source_env, "LOCAL_AGENT_NODE_EXECUTABLE")
    if isinstance(configured, str) and configured and Path(configured).is_file():
        return str(Path(configured))

    path_value = lookup_env_value(source_env, "PATH")
    discovered = shutil.which("node", path=path_value) if path_value else shutil.which("node")
    if discovered and Path(discovered).is_file():
        return str(Path(discovered))

    home = Path.home()
    for candidate in (
        home / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe",
        home / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node",
    ):
        if candidate.is_file():
            return str(candidate)
    return None
