from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO


class WorkerProcessRuntime:
    """Lifecycle wrapper around a single worker subprocess."""

    def __init__(
        self,
        command: Sequence[str] | str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        stdin: int | TextIO | None = None,
        stdout: int | TextIO | None = None,
        stderr: int | TextIO | None = None,
        text: bool = False,
        creationflags: int = 0,
    ) -> None:
        self._command = list(command) if not isinstance(command, str) else command
        self._cwd = str(Path(cwd)) if cwd is not None else None
        self._env = dict(env) if env is not None else None
        self._stdin = stdin
        self._stdout = stdout
        self._stderr = stderr
        self._text = text
        self._creationflags = creationflags
        self._process: subprocess.Popen[Any] | None = None

    @property
    def pid(self) -> int | None:
        if self._process is None:
            return None
        return self._process.pid

    def start(self) -> "WorkerProcessRuntime":
        process = self._process
        if process is not None and process.poll() is None:
            raise RuntimeError("Worker process is already running")
        if process is not None:
            self.cleanup()

        self._process = subprocess.Popen(
            self._command,
            cwd=self._cwd,
            env=self._env,
            stdin=self._stdin,
            stdout=self._stdout,
            stderr=self._stderr,
            text=self._text,
            creationflags=self._creationflags | self._platform_creationflags(),
        )
        return self

    def terminate(self) -> int | None:
        process = self._process
        if process is None:
            return None
        exit_code = process.poll()
        if exit_code is not None:
            return exit_code

        if os.name == "nt":
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except (OSError, ValueError):
                process.terminate()
        else:
            process.terminate()
        return process.poll()

    def kill(self) -> int | None:
        process = self._process
        if process is None:
            return None
        exit_code = process.poll()
        if exit_code is not None:
            return exit_code

        process.kill()
        return self._poll_or_wait(process, timeout=0.1)

    def wait(self, timeout: float | None = None) -> int:
        process = self._require_process()
        return process.wait(timeout=timeout)

    def poll(self) -> int | None:
        process = self._process
        if process is None:
            return None
        return process.poll()

    def cleanup(self) -> None:
        process = self._process
        if process is None:
            return

        if process.poll() is None:
            process.kill()
            process.wait()

        self._close_stream(process.stdin)
        self._close_stream(process.stdout)
        self._close_stream(process.stderr)
        self._process = None

    def _require_process(self) -> subprocess.Popen[Any]:
        process = self._process
        if process is None:
            raise RuntimeError("Worker process has not been started")
        return process

    def _platform_creationflags(self) -> int:
        if os.name != "nt":
            return 0
        create_new_process_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return create_new_process_group

    def _poll_or_wait(self, process: subprocess.Popen[Any], *, timeout: float) -> int | None:
        exit_code = process.poll()
        if exit_code is not None:
            return exit_code
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return process.poll()

    def _close_stream(self, stream: Any) -> None:
        if stream is None:
            return
        close = getattr(stream, "close", None)
        if close is None:
            return
        try:
            close()
        except OSError:
            pass
