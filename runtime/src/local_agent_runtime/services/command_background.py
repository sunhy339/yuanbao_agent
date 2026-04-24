from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from ..models import RuntimeEvent
from ..store.sqlite_store import SQLiteStore
from .command_execution import build_shell_command
from .worker_process_runtime import WorkerProcessRuntime


@dataclass(slots=True)
class BackgroundCommandRequest:
    database_path: str
    command_log_id: str
    task_id: str
    session_id: str
    command: str
    cwd: str
    shell: str
    timeout_ms: int
    workspace_root: str


class _StreamingArtifactWriter:
    def __init__(self, *, artifact_path: Path) -> None:
        self._artifact_path = artifact_path
        self._lock = threading.Lock()
        self._writer = artifact_path.open("w", encoding="utf-8", errors="replace")

    @property
    def artifact_path(self) -> str:
        return str(self._artifact_path)

    def append(self, chunk: str) -> None:
        if not chunk:
            return
        with self._lock:
            self._writer.write(chunk)
            self._writer.flush()

    def close(self) -> None:
        with self._lock:
            self._writer.close()


class BackgroundCommandEventBridge:
    def __init__(self) -> None:
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.Lock()

    def add_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(event)


@dataclass(slots=True)
class _RunningBackgroundCommand:
    request: BackgroundCommandRequest
    runtime: WorkerProcessRuntime | None = None
    cancelled: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def attach_runtime(self, runtime: WorkerProcessRuntime) -> bool:
        with self.lock:
            self.runtime = runtime
            cancelled = self.cancelled
        return cancelled

    def cancel(self) -> None:
        runtime: WorkerProcessRuntime | None
        with self.lock:
            self.cancelled = True
            runtime = self.runtime
        if runtime is not None:
            runtime.kill()

    def is_cancelled(self) -> bool:
        with self.lock:
            return self.cancelled


class BackgroundCommandService:
    def __init__(self, database_path: str, event_bridge: BackgroundCommandEventBridge) -> None:
        self._database_path = database_path
        self._event_bridge = event_bridge
        self._counter = itertools.count(1)
        self._lock = threading.Lock()
        self._running: dict[str, _RunningBackgroundCommand] = {}

    def submit(self, request: BackgroundCommandRequest) -> None:
        state = _RunningBackgroundCommand(request=request)
        with self._lock:
            self._running[request.command_log_id] = state
        worker = threading.Thread(
            target=self._run,
            args=(state,),
            name=f"command-bg-{next(self._counter)}",
            daemon=True,
        )
        worker.start()

    def cancel_task(self, task_id: str) -> list[str]:
        with self._lock:
            states = [state for state in self._running.values() if state.request.task_id == task_id]
        for state in states:
            state.cancel()
        return [state.request.command_log_id for state in states]

    def cancel_command(self, command_log_id: str) -> bool:
        with self._lock:
            state = self._running.get(command_log_id)
        if state is None:
            return False
        state.cancel()
        return True

    def _run(self, state: _RunningBackgroundCommand) -> None:
        request = state.request
        store = SQLiteStore(request.database_path)
        status = "completed"
        exit_code: int | None = None
        stdout_path = self._artifact_path(store, request.command_log_id, "stdout")
        stderr_path = self._artifact_path(store, request.command_log_id, "stderr")
        stdout_writer = _StreamingArtifactWriter(artifact_path=stdout_path)
        stderr_writer = _StreamingArtifactWriter(artifact_path=stderr_path)
        runtime: WorkerProcessRuntime | None = None
        pending_chunks: Queue[tuple[str, str]] = Queue()
        pending_buffers: dict[str, list[str]] = {"stdout": [], "stderr": []}

        try:
            cwd_path = Path(request.cwd)
            cwd_abs = cwd_path.resolve() if cwd_path.is_absolute() else (Path(request.workspace_root) / cwd_path).resolve()
            runtime = WorkerProcessRuntime(
                build_shell_command(request.shell, request.command),
                cwd=str(cwd_abs),
                stdin=None,
                stdout=-1,
                stderr=-1,
                text=True,
            )
            runtime.start()
            if state.attach_runtime(runtime):
                runtime.kill()

            runtime.open_stream_drain(
                "stdout",
                chunk_callback=self._stream_callback(
                    chunk_queue=pending_chunks,
                    stream_name="stdout",
                    writer=stdout_writer,
                ),
            )
            runtime.open_stream_drain(
                "stderr",
                chunk_callback=self._stream_callback(
                    chunk_queue=pending_chunks,
                    stream_name="stderr",
                    writer=stderr_writer,
                ),
            )

            deadline = (time.monotonic() + (request.timeout_ms / 1000)) if request.timeout_ms else None
            while True:
                self._drain_pending_chunks(
                    store=store,
                    request=request,
                    chunk_queue=pending_chunks,
                    pending_buffers=pending_buffers,
                )
                if state.is_cancelled():
                    runtime.kill()
                exit_code = runtime.poll()
                if exit_code is not None:
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    runtime.kill()
                    status = "timeout"
                    exit_code = None
                    break
                time.sleep(0.02)

            self._drain_pending_chunks(
                store=store,
                request=request,
                chunk_queue=pending_chunks,
                pending_buffers=pending_buffers,
                flush_all=True,
            )
            if state.is_cancelled():
                status = "cancelled"
            elif status != "timeout":
                if exit_code is None:
                    status = "failed"
                elif exit_code < 0:
                    status = "killed"
                elif exit_code != 0:
                    status = "failed"
        except Exception as exc:  # noqa: BLE001
            status = "cancelled" if state.is_cancelled() else "failed"
            stderr_writer.append(str(exc))
            self._append_command_output_trace(
                store,
                task_id=request.task_id,
                command_log_id=request.command_log_id,
                stream_name="stderr",
                chunk=str(exc),
            )
            self._emit_output_event(
                store=store,
                request=request,
                stream_name="stderr",
                chunk=str(exc),
            )
        finally:
            if runtime is not None:
                runtime.cleanup()
            self._drain_pending_chunks(
                store=store,
                request=request,
                chunk_queue=pending_chunks,
                pending_buffers=pending_buffers,
                flush_all=True,
            )
            stdout_writer.close()
            stderr_writer.close()

            command_log = store.update_command_log(
                request.command_log_id,
                status=status,
                exit_code=exit_code,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                finished_at=store.now(),
            )
            self._emit_terminal_event(store=store, request=request, command_log=command_log)
            store.close()
            with self._lock:
                self._running.pop(request.command_log_id, None)

    def _stream_callback(
        self,
        *,
        chunk_queue: Queue[tuple[str, str]],
        stream_name: str,
        writer: _StreamingArtifactWriter,
    ) -> Callable[[str], None]:
        def _handle(chunk: str) -> None:
            writer.append(chunk)
            chunk_queue.put((stream_name, chunk))

        return _handle

    def _drain_pending_chunks(
        self,
        *,
        store: SQLiteStore,
        request: BackgroundCommandRequest,
        chunk_queue: Queue[tuple[str, str]],
        pending_buffers: dict[str, list[str]],
        flush_all: bool = False,
    ) -> None:
        while True:
            try:
                stream_name, chunk = chunk_queue.get_nowait()
            except Empty:
                break
            buffer = pending_buffers.setdefault(stream_name, [])
            buffer.append(chunk)
            combined = "".join(buffer)
            if not flush_all and "\n" not in combined and len(combined) < 256:
                continue
            self._flush_output_buffer(
                store=store,
                request=request,
                stream_name=stream_name,
                pending_buffers=pending_buffers,
            )

        if flush_all:
            for stream_name, buffer in pending_buffers.items():
                if buffer:
                    self._flush_output_buffer(
                        store=store,
                        request=request,
                        stream_name=stream_name,
                        pending_buffers=pending_buffers,
                    )

    def _flush_output_buffer(
        self,
        *,
        store: SQLiteStore,
        request: BackgroundCommandRequest,
        stream_name: str,
        pending_buffers: dict[str, list[str]],
    ) -> None:
        buffer = pending_buffers.setdefault(stream_name, [])
        chunk = "".join(buffer)
        if not chunk:
            return
        buffer.clear()
        self._append_command_output_trace(
            store,
            task_id=request.task_id,
            command_log_id=request.command_log_id,
            stream_name=stream_name,
            chunk=chunk,
        )
        self._emit_output_event(
            store=store,
            request=request,
            stream_name=stream_name,
            chunk=chunk,
        )

    def _append_command_output_trace(
        self,
        store: SQLiteStore,
        *,
        task_id: str,
        command_log_id: str,
        stream_name: str,
        chunk: str,
    ) -> None:
        if not chunk:
            return
        store.append_trace_event(
            task_id=task_id,
            event_type="command.output",
            source="command",
            related_id=command_log_id,
            payload={
                "commandId": command_log_id,
                "stream": stream_name,
                "chunk": chunk,
            },
        )

    def _emit_output_event(
        self,
        *,
        store: SQLiteStore,
        request: BackgroundCommandRequest,
        stream_name: str,
        chunk: str,
    ) -> None:
        if not chunk:
            return
        self._event_bridge.emit(
            self._event_payload(
                store=store,
                request=request,
                event_type="command.output",
                payload={
                    "commandId": request.command_log_id,
                    "stream": stream_name,
                    "chunk": chunk,
                },
            )
        )

    def emit_started_event(self, request: BackgroundCommandRequest) -> dict[str, Any]:
        store = SQLiteStore(request.database_path)
        try:
            event = self._event_payload(
                store=store,
                request=request,
                event_type="command.started",
                payload={
                    "commandId": request.command_log_id,
                    "command": request.command,
                    "cwd": request.cwd,
                    "shell": request.shell,
                    "status": "running",
                    "background": True,
                },
            )
            self._event_bridge.emit(event)
            return event
        finally:
            store.close()

    def _emit_terminal_event(
        self,
        *,
        store: SQLiteStore,
        request: BackgroundCommandRequest,
        command_log: dict[str, Any],
    ) -> None:
        event_type = "command.completed" if command_log["status"] == "completed" else "command.failed"
        self._event_bridge.emit(
            self._event_payload(
                store=store,
                request=request,
                event_type=event_type,
                payload={
                    "commandId": command_log["id"],
                    "command": command_log["command"],
                    "cwd": command_log["cwd"],
                    "shell": request.shell,
                    "status": command_log["status"],
                    "exitCode": command_log["exitCode"],
                    "durationMs": command_log["durationMs"],
                    "stdoutPath": command_log["stdoutPath"],
                    "stderrPath": command_log["stderrPath"],
                    "background": True,
                },
            )
        )

    def _event_payload(
        self,
        *,
        store: SQLiteStore,
        request: BackgroundCommandRequest,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        event = RuntimeEvent(
            event_id=store.new_id("evt"),
            session_id=request.session_id,
            task_id=request.task_id,
            type=event_type,
            ts=store.now(),
            payload=payload,
        )
        return {
            "eventId": event.event_id,
            "sessionId": event.session_id,
            "taskId": event.task_id,
            "type": event.type,
            "ts": event.ts,
            "payload": event.payload,
        }

    def _artifact_path(self, store: SQLiteStore, command_log_id: str, stream_name: str) -> Path:
        if store.database_path == ":memory:":
            artifact_dir = Path.cwd() / "runtime_artifacts"
        else:
            artifact_dir = Path(store.database_path).expanduser().resolve().parent / "runtime_artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir / f"{command_log_id}_{stream_name}.log"


_SERVICES: dict[str, BackgroundCommandService] = {}
_BRIDGES: dict[str, BackgroundCommandEventBridge] = {}
_SERVICES_LOCK = threading.RLock()


def get_background_command_event_bridge(database_path: str) -> BackgroundCommandEventBridge:
    with _SERVICES_LOCK:
        bridge = _BRIDGES.get(database_path)
        if bridge is None:
            bridge = BackgroundCommandEventBridge()
            _BRIDGES[database_path] = bridge
        return bridge


def get_background_command_service(database_path: str) -> BackgroundCommandService:
    with _SERVICES_LOCK:
        service = _SERVICES.get(database_path)
        if service is None:
            service = BackgroundCommandService(database_path, get_background_command_event_bridge(database_path))
            _SERVICES[database_path] = service
        return service


def cancel_background_commands(*, database_path: str, task_id: str) -> list[str]:
    return get_background_command_service(database_path).cancel_task(task_id)


def cancel_background_command(*, database_path: str, command_log_id: str) -> bool:
    return get_background_command_service(database_path).cancel_command(command_log_id)
