from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from queue import Empty, Queue
from threading import Lock
from typing import Any

from .worker_process_runtime import WorkerProcessRuntime, WorkerProcessStreamDrain


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
        self._lock = Lock()
        self._stdout_buffer_lock = Lock()
        self._stdout_buffer = ""
        self._closed = False
        self._stdout_drain: WorkerProcessStreamDrain | None = None
        self._stderr_drain: WorkerProcessStreamDrain | None = None

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
        self._stdout_buffer = ""
        self._stdout_drain = self._runtime.open_stream_drain("stdout", chunk_callback=self._handle_stdout_chunk)
        self._stderr_drain = self._runtime.open_stream_drain("stderr")
        return self

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._runtime.cleanup()

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
                    response = self._take_ready_response(response_queue)
                    if response is not None:
                        self._drain_events(event_callback)
                        return response
                    self._raise_if_exited()
                    raise WorkerProcessTimeoutError(f"Timed out waiting for worker response to {method}.")
                try:
                    response = response_queue.get(timeout=min(remaining, 0.05))
                    self._drain_events(event_callback)
                    return response
                except Empty:
                    response = self._take_ready_response(response_queue)
                    if response is not None:
                        self._drain_events(event_callback)
                        return response
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

    def add_stream_callback(self, stream_name: str, callback: Callable[[str], None]) -> None:
        self._stream_drain(stream_name).add_callback(callback)

    def take_stream_chunks(self, stream_name: str) -> list[str]:
        return self._stream_drain(stream_name).take_chunks()

    def stream_tail(self, stream_name: str) -> str:
        return self._stream_drain(stream_name).tail_text()

    def _send_json(self, payload: dict[str, Any]) -> None:
        process = self._process()
        stdin = process.stdin
        if stdin is None:
            raise WorkerProcessExitError(process.poll(), self._stderr_text())
        stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stdin.flush()

    def _handle_stdout_chunk(self, chunk: str) -> None:
        lines: list[str] = []
        with self._stdout_buffer_lock:
            self._stdout_buffer += chunk
            while True:
                newline_index = self._stdout_buffer.find("\n")
                if newline_index < 0:
                    break
                line = self._stdout_buffer[:newline_index].rstrip("\r")
                self._stdout_buffer = self._stdout_buffer[newline_index + 1 :]
                lines.append(line)
        for line in lines:
            self._handle_stdout_line(line)

    def _handle_stdout_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return
        if payload.get("kind") == "event" and isinstance(payload.get("payload"), dict):
            self._event_queue.put(payload["payload"])
            return
        request_id = payload.get("id")
        if isinstance(request_id, str):
            with self._lock:
                queue = self._response_queues.get(request_id)
            if queue is not None:
                queue.put(payload)

    def _process(self) -> subprocess.Popen[str]:
        process = self._runtime._require_process()  # noqa: SLF001
        return process

    def _next_request_id(self) -> str:
        with self._lock:
            self._request_id += 1
            return f"rpc_{self._request_id}"

    def _raise_if_exited(self) -> None:
        self._flush_stdout_buffer_if_closed()
        returncode = self._runtime.poll()
        if returncode is None:
            return
        raise WorkerProcessExitError(returncode, self._stderr_text())

    def _stderr_text(self) -> str:
        drain = self._stderr_drain
        if drain is None:
            return ""
        return drain.tail_text()

    def _drain_events(self, event_callback: Any | None) -> None:
        if event_callback is None:
            return
        while True:
            try:
                event = self._event_queue.get_nowait()
            except Empty:
                return
            event_callback(event)

    def _flush_stdout_buffer_if_closed(self) -> None:
        drain = self._stdout_drain
        if drain is None or not drain.is_closed:
            return
        with self._stdout_buffer_lock:
            remainder = self._stdout_buffer.rstrip("\r")
            self._stdout_buffer = ""
        if remainder:
            self._handle_stdout_line(remainder)

    def _take_ready_response(self, response_queue: Queue[dict[str, Any]]) -> dict[str, Any] | None:
        self._flush_stdout_buffer_if_closed()
        try:
            return response_queue.get_nowait()
        except Empty:
            return None

    def _stream_drain(self, stream_name: str) -> WorkerProcessStreamDrain:
        if stream_name == "stdout":
            drain = self._stdout_drain
        elif stream_name == "stderr":
            drain = self._stderr_drain
        else:
            raise ValueError(f"Unsupported worker stream: {stream_name}")
        if drain is None:
            raise RuntimeError("Worker transport has not been started")
        return drain
