from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass
from pathlib import Path

from ..store.sqlite_store import SQLiteStore
from .command_execution import run_shell_command


@dataclass(slots=True)
class BackgroundCommandRequest:
    database_path: str
    command_log_id: str
    command: str
    cwd: str
    shell: str
    timeout_ms: int
    workspace_root: str


class _StreamingArtifactWriter:
    def __init__(
        self,
        *,
        artifact_path: Path,
    ) -> None:
        self._artifact_path = artifact_path
        self._lock = threading.Lock()
        self._drained_chunks: list[str] = []
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
            self._drained_chunks.append(chunk)

    def close(self) -> list[str]:
        with self._lock:
            chunks = list(self._drained_chunks)
            self._drained_chunks.clear()
            self._writer.close()
            return chunks


class BackgroundCommandService:
    def __init__(self) -> None:
        self._counter = itertools.count(1)

    def submit(self, request: BackgroundCommandRequest) -> None:
        worker = threading.Thread(
            target=self._run,
            args=(request,),
            name=f"command-bg-{next(self._counter)}",
            daemon=True,
        )
        worker.start()

    def _run(self, request: BackgroundCommandRequest) -> None:
        store = SQLiteStore(request.database_path)
        status = "completed"
        exit_code: int | None = None
        stdout_path = self._artifact_path(store, request.command_log_id, "stdout")
        stderr_path = self._artifact_path(store, request.command_log_id, "stderr")
        stdout_writer: _StreamingArtifactWriter | None = None
        stderr_writer: _StreamingArtifactWriter | None = None
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        try:
            command_log = store.get_command_log({"commandId": request.command_log_id})["commandLog"]
            stdout_writer = _StreamingArtifactWriter(
                artifact_path=stdout_path,
            )
            stderr_writer = _StreamingArtifactWriter(
                artifact_path=stderr_path,
            )
            cwd_path = Path(request.cwd)
            cwd_abs = cwd_path.resolve() if cwd_path.is_absolute() else (Path(request.workspace_root) / cwd_path).resolve()
            _stdout, _stderr, exit_code, status, _duration_ms = run_shell_command(
                request.shell,
                request.command,
                cwd_abs,
                request.timeout_ms,
                stdout_callback=stdout_writer.append,
                stderr_callback=stderr_writer.append,
            )
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            if stderr_writer is None:
                stderr_path.parent.mkdir(parents=True, exist_ok=True)
                stderr_path.write_text("", encoding="utf-8", errors="replace")
                stderr_writer = _StreamingArtifactWriter(
                    artifact_path=stderr_path,
                    store=store,
                    task_id=store.get_command_log({"commandId": request.command_log_id})["commandLog"]["taskId"],
                    command_log_id=request.command_log_id,
                    stream_name="stderr",
                )
            stderr_writer.append(str(exc))
        finally:
            if stdout_writer is not None:
                stdout_chunks = stdout_writer.close()
            else:
                stdout_path.parent.mkdir(parents=True, exist_ok=True)
                stdout_path.write_text("", encoding="utf-8", errors="replace")
            if stderr_writer is not None:
                stderr_chunks = stderr_writer.close()
            else:
                stderr_path.parent.mkdir(parents=True, exist_ok=True)
                stderr_path.write_text("", encoding="utf-8", errors="replace")

            if "command_log" in locals():
                for stream_name, chunks in (("stdout", stdout_chunks), ("stderr", stderr_chunks)):
                    if not chunks:
                        continue
                    self._append_command_output_trace(
                        store,
                        task_id=command_log["taskId"],
                        command_log_id=request.command_log_id,
                        stream_name=stream_name,
                        chunks=chunks,
                    )

            store.update_command_log(
                request.command_log_id,
                status=status,
                exit_code=exit_code,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                finished_at=store.now(),
            )
            store.close()

    def _append_command_output_trace(
        self,
        store: SQLiteStore,
        *,
        task_id: str,
        command_log_id: str,
        stream_name: str,
        chunks: list[str],
    ) -> None:
        buffer: list[str] = []
        buffered_len = 0
        for chunk in chunks:
            buffer.append(chunk)
            buffered_len += len(chunk)
            if "\n" not in chunk and buffered_len < 256:
                continue
            self._flush_trace_chunk(
                store,
                task_id=task_id,
                command_log_id=command_log_id,
                stream_name=stream_name,
                chunk="".join(buffer),
            )
            buffer.clear()
            buffered_len = 0
        if buffer:
            self._flush_trace_chunk(
                store,
                task_id=task_id,
                command_log_id=command_log_id,
                stream_name=stream_name,
                chunk="".join(buffer),
            )

    def _flush_trace_chunk(
        self,
        store: SQLiteStore,
        *,
        task_id: str,
        command_log_id: str,
        stream_name: str,
        chunk: str,
    ) -> None:
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

    def _artifact_path(self, store: SQLiteStore, command_log_id: str, stream_name: str) -> Path:
        if store.database_path == ":memory:":
            artifact_dir = Path.cwd() / "runtime_artifacts"
        else:
            artifact_dir = Path(store.database_path).expanduser().resolve().parent / "runtime_artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir / f"{command_log_id}_{stream_name}.log"


_SERVICES: dict[str, BackgroundCommandService] = {}
_SERVICES_LOCK = threading.Lock()


def get_background_command_service(database_path: str) -> BackgroundCommandService:
    with _SERVICES_LOCK:
        service = _SERVICES.get(database_path)
        if service is None:
            service = BackgroundCommandService()
            _SERVICES[database_path] = service
        return service
