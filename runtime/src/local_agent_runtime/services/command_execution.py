from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from .worker_process_runtime import WorkerProcessRuntime


def build_shell_command(shell_name: str, command: str) -> list[str]:
    if shell_name == "bash":
        return ["bash", "-lc", command]
    if shell_name == "zsh":
        return ["zsh", "-lc", command]
    return ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command]


def run_shell_command(
    shell_name: str,
    command: str,
    cwd: Path,
    timeout_ms: int,
    *,
    stdout_callback: Callable[[str], None] | None = None,
    stderr_callback: Callable[[str], None] | None = None,
) -> tuple[str, str, int | None, str, int]:
    started = time.perf_counter()
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    runtime = WorkerProcessRuntime(
        build_shell_command(shell_name, command),
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def _chunk_handler(
        buffer: list[str],
        callback: Callable[[str], None] | None,
    ) -> Callable[[str], None]:
        def _handle(chunk: str) -> None:
            buffer.append(chunk)
            if callback is not None:
                callback(chunk)

        return _handle

    exit_code: int | None = None
    status = "completed"

    runtime.start()
    runtime.open_stream_drain("stdout", chunk_callback=_chunk_handler(stdout_chunks, stdout_callback))
    runtime.open_stream_drain("stderr", chunk_callback=_chunk_handler(stderr_chunks, stderr_callback))
    try:
        try:
            exit_code = runtime.wait(timeout=timeout_ms / 1000 if timeout_ms else None)
        except subprocess.TimeoutExpired:
            runtime.kill()
            status = "timeout"
            exit_code = None
        else:
            if exit_code is None:
                status = "failed"
            elif exit_code < 0:
                status = "killed"
            elif exit_code != 0:
                status = "failed"
    finally:
        runtime.cleanup()

    duration_ms = int((time.perf_counter() - started) * 1000)
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    return stdout, stderr, exit_code, status, duration_ms
