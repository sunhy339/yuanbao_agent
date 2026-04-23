from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import deque
from collections.abc import Mapping, Sequence
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Any

from .worker_process_runtime import WorkerProcessRuntime


class WorkerProcessTransportError(RuntimeError):
    """Base transport failure."""


class WorkerProcessTimeoutError(WorkerProcessTransportError):
    """Raised when the child process does not answer before the deadline."""


class WorkerProcessExitError(WorkerProcessTransportError):
    """Raised when the child process exits before fulfilling the request."""

    def __init__(self, returncode: int | None, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        detail = f"Worker process exited before replying (returncode={returncode})."
        if stderr.strip():
            detail = f"{detail} stderr: {stderr.strip()}"
        super().__init__(detail)


class WorkerProcessTransport:
    """JSON-line stdio transport for worker subprocesses."""

    def __init__(
        self,
        command: Sequence[str] | str,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._runtime = WorkerProcessRuntime(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._request_id = 0
        self._response_queues: dict[str, Queue[dict[str, Any]]] = {}
        self._event_queue: Queue[dict[str, Any]] = Queue()
        self._stderr_lines: deque[str] = deque(maxlen=50)
        self._lock = Lock()
        self._closed = False
        self._reader_done = Event()
        self._stdout_thread: Thread | None = None
        self._stderr_thread: Thread | None = None

    @classmethod
    def for_python_module(
        cls,
        module_name: str,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> WorkerProcessTransport:
        return cls([sys.executable, "-u", "-m", module_name], cwd=cwd, env=env)

    def __enter__(self) -> WorkerProcessTransport:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def start(self) -> WorkerProcessTransport:
        self._runtime.start()
        process = self._process()
        self._stdout_thread = Thread(target=self._read_stdout, args=(process,), daemon=True)
        self._stdout_thread.start()
        self._stderr_thread = Thread(target=self._read_stderr, args=(process,), daemon=True)
        self._stderr_thread.start()
        return self

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._runtime.cleanup()
        self._reader_done.wait(timeout=1.0)

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        event_callback: Any | None = None,
    ) -> dict[str, Any]:
        response_queue: Queue[dict[str, Any]] = Queue(maxsize=1)
        request_id = self._next_request_id()
        with self._lock:
            self._response_queues[request_id] = response_queue
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
        try:
            deadline = time.monotonic() + timeout
            while True:
                self._drain_events(event_callback)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._raise_if_exited()
                    raise WorkerProcessTimeoutError(f"Timed out waiting for worker response to {method}.")
                try:
                    response = response_queue.get(timeout=min(remaining, 0.05))
                    self._drain_events(event_callback)
                    return response
                except Empty:
                    self._raise_if_exited()
        finally:
            with self._lock:
                self._response_queues.pop(request_id, None)

    def recv_event(self, *, timeout: float) -> dict[str, Any]:
        try:
            return self._event_queue.get(timeout=timeout)
        except Empty as exc:
            self._raise_if_exited()
            raise WorkerProcessTimeoutError("Timed out waiting for worker event.") from exc

    def _send_json(self, payload: dict[str, Any]) -> None:
        process = self._process()
        stdin = process.stdin
        if stdin is None:
            raise WorkerProcessExitError(process.poll(), self._stderr_text())
        stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stdin.flush()

    def _read_stdout(self, process: subprocess.Popen[str]) -> None:
        try:
            stdout = process.stdout
            if stdout is None:
                return
            for raw_line in stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("kind") == "event" and isinstance(payload.get("payload"), dict):
                    self._event_queue.put(payload["payload"])
                    continue
                request_id = payload.get("id")
                if isinstance(request_id, str):
                    with self._lock:
                        queue = self._response_queues.get(request_id)
                    if queue is not None:
                        queue.put(payload)
        finally:
            self._reader_done.set()

    def _read_stderr(self, process: subprocess.Popen[str]) -> None:
        stderr = process.stderr
        if stderr is None:
            return
        for raw_line in stderr:
            line = raw_line.rstrip()
            if line:
                self._stderr_lines.append(line)

    def _process(self) -> subprocess.Popen[str]:
        process = self._runtime._require_process()  # noqa: SLF001
        return process

    def _next_request_id(self) -> str:
        with self._lock:
            self._request_id += 1
            return f"rpc_{self._request_id}"

    def _raise_if_exited(self) -> None:
        returncode = self._runtime.poll()
        if returncode is None:
            return
        raise WorkerProcessExitError(returncode, self._stderr_text())

    def _stderr_text(self) -> str:
        return "\n".join(self._stderr_lines)

    def _drain_events(self, event_callback: Any | None) -> None:
        if event_callback is None:
            return
        while True:
            try:
                event = self._event_queue.get_nowait()
            except Empty:
                return
            event_callback(event)
