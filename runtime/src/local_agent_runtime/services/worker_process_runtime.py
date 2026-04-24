from __future__ import annotations

import os
import signal
import subprocess
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, TextIO


class WorkerProcessStreamDrain:
    """Background stream drain that captures chunks and a bounded tail."""

    def __init__(
        self,
        stream_name: str,
        stream: TextIO,
        *,
        chunk_size: int = 4096,
        tail_max_chunks: int = 50,
        chunk_callback: Callable[[str], None] | None = None,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if tail_max_chunks <= 0:
            raise ValueError("tail_max_chunks must be greater than zero")

        self._stream_name = stream_name
        self._stream = stream
        self._chunk_size = chunk_size
        self._drained_chunks: deque[str] = deque()
        self._tail_chunks: deque[str] = deque(maxlen=tail_max_chunks)
        self._callbacks: list[Callable[[str], None]] = []
        self._lock = Lock()
        self._closed = Event()
        self._thread = Thread(target=self._run, name=f"worker-process-{stream_name}-drain", daemon=True)
        if chunk_callback is not None:
            self._callbacks.append(chunk_callback)

    @property
    def is_closed(self) -> bool:
        return self._closed.is_set()

    def start(self) -> "WorkerProcessStreamDrain":
        self._thread.start()
        return self

    def add_callback(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def take_chunks(self) -> list[str]:
        with self._lock:
            chunks = list(self._drained_chunks)
            self._drained_chunks.clear()
        return chunks

    def tail_chunks(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._tail_chunks)

    def tail_text(self) -> str:
        return "".join(self.tail_chunks())

    def wait_closed(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout)

    def join(self, timeout: float | None = None) -> bool:
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def _run(self) -> None:
        try:
            while True:
                try:
                    chunk = self._stream.read(1)
                except (OSError, ValueError):
                    return
                if not chunk:
                    return
                self._emit_chunk(chunk)
        finally:
            self._closed.set()

    def _emit_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        with self._lock:
            self._drained_chunks.append(chunk)
            self._tail_chunks.append(chunk)
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            callback(chunk)


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
        self._stream_drains: dict[str, WorkerProcessStreamDrain] = {}
        self._stream_lock = Lock()

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
        self._join_stream_drains()
        self._process = None

    def open_stream_drain(
        self,
        stream_name: str,
        *,
        chunk_callback: Callable[[str], None] | None = None,
        chunk_size: int = 4096,
        tail_max_chunks: int = 50,
    ) -> WorkerProcessStreamDrain:
        if stream_name not in {"stdout", "stderr"}:
            raise ValueError(f"Unsupported worker stream: {stream_name}")

        with self._stream_lock:
            drain = self._stream_drains.get(stream_name)
            if drain is not None:
                if chunk_callback is not None:
                    drain.add_callback(chunk_callback)
                return drain

            process = self._require_process()
            stream = getattr(process, stream_name)
            if stream is None:
                raise RuntimeError(f"Worker process stream {stream_name} is not available")

            drain = WorkerProcessStreamDrain(
                stream_name,
                stream,
                chunk_size=chunk_size,
                tail_max_chunks=tail_max_chunks,
                chunk_callback=chunk_callback,
            ).start()
            self._stream_drains[stream_name] = drain
            return drain

    def stream_drain(self, stream_name: str) -> WorkerProcessStreamDrain | None:
        with self._stream_lock:
            return self._stream_drains.get(stream_name)

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

    def _join_stream_drains(self) -> None:
        with self._stream_lock:
            drains = tuple(self._stream_drains.values())
            self._stream_drains.clear()
        for drain in drains:
            drain.join(timeout=1.0)
