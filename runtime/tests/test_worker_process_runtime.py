from __future__ import annotations

import sys
import subprocess
import textwrap
import time
from pathlib import Path

from local_agent_runtime.services.worker_process_runtime import WorkerProcessRuntime


def _write_script(path: Path, source: str) -> Path:
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _sleep_script(tmp_path: Path) -> Path:
    return _write_script(
        tmp_path / "sleep_forever.py",
        """
        import time

        while True:
            time.sleep(1)
        """,
    )


def _wait_for_path(path: Path, timeout_seconds: float = 5) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {path}")


def _wait_for_condition(predicate: object, timeout_seconds: float = 5) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for condition")


def _terminable_script(tmp_path: Path) -> tuple[Path, Path, Path]:
    marker = tmp_path / "terminated.txt"
    ready = tmp_path / "ready.txt"
    script = _write_script(
        tmp_path / "terminable.py",
        """
        import pathlib
        import signal
        import sys
        import time

        marker = pathlib.Path(sys.argv[1])
        ready = pathlib.Path(sys.argv[2])

        def _handle(signum, _frame):
            marker.write_text(str(signum), encoding="utf-8")
            raise SystemExit(0)

        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _handle)

        ready.write_text("ready", encoding="utf-8")

        while True:
            time.sleep(0.1)
        """,
    )
    return script, marker, ready


def _start_runtime(script: Path, *args: str) -> WorkerProcessRuntime:
    runtime = WorkerProcessRuntime([sys.executable, str(script), *args])
    runtime.start()
    assert runtime.pid is not None
    assert runtime.poll() is None
    return runtime


def _start_runtime_with_pipes(script: Path, *args: str) -> WorkerProcessRuntime:
    runtime = WorkerProcessRuntime(
        [sys.executable, str(script), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    runtime.start()
    assert runtime.pid is not None
    assert runtime.poll() is None
    return runtime


def test_worker_process_runtime_force_kill_stops_long_running_python(tmp_path: Path) -> None:
    runtime = _start_runtime(_sleep_script(tmp_path))

    exit_code = runtime.kill()
    assert runtime.wait(timeout=5) == exit_code
    assert exit_code is not None
    assert exit_code != 0
    assert runtime.poll() == exit_code

    runtime.cleanup()
    assert runtime.pid is None


def test_worker_process_runtime_terminate_requests_graceful_shutdown(tmp_path: Path) -> None:
    script, marker, ready = _terminable_script(tmp_path)
    runtime = _start_runtime(script, str(marker), str(ready))
    _wait_for_path(ready)

    runtime.terminate()
    exit_code = runtime.wait(timeout=5)

    assert exit_code == 0
    assert runtime.poll() == 0
    assert marker.exists()

    runtime.cleanup()
    assert runtime.pid is None


def test_worker_process_runtime_repeated_kill_is_safe(tmp_path: Path) -> None:
    runtime = _start_runtime(_sleep_script(tmp_path))

    first_exit = runtime.kill()
    assert runtime.wait(timeout=5) == first_exit

    second_exit = runtime.kill()
    assert second_exit == first_exit
    assert runtime.poll() == first_exit

    runtime.cleanup()
    runtime.cleanup()
    assert runtime.pid is None


def test_worker_process_runtime_drains_stdout_and_stderr_incrementally(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path / "streaming_child.py",
        """
        import sys
        import time

        sys.stdout.write("out-1")
        sys.stdout.flush()
        sys.stderr.write("err-1")
        sys.stderr.flush()
        time.sleep(0.5)
        sys.stdout.write("out-2\\n")
        sys.stdout.flush()
        sys.stderr.write("err-2\\n")
        sys.stderr.flush()
        time.sleep(0.1)
        """,
    )
    runtime = _start_runtime_with_pipes(script)
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stdout_drain = runtime.open_stream_drain("stdout", chunk_callback=stdout_chunks.append, chunk_size=1)
    stderr_drain = runtime.open_stream_drain("stderr", chunk_callback=stderr_chunks.append, chunk_size=1)

    _wait_for_condition(lambda: "".join(stdout_chunks) == "out-1")
    _wait_for_condition(lambda: "".join(stderr_chunks) == "err-1")
    assert runtime.poll() is None

    assert runtime.wait(timeout=5) == 0
    _wait_for_condition(lambda: stdout_drain.is_closed and stderr_drain.is_closed)

    assert "".join(stdout_chunks) == "out-1out-2\n"
    assert "".join(stderr_chunks) == "err-1err-2\n"
    assert "".join(stdout_drain.take_chunks()) == "out-1out-2\n"
    assert "".join(stderr_drain.take_chunks()) == "err-1err-2\n"
    assert stdout_drain.take_chunks() == []
    assert stderr_drain.take_chunks() == []
    assert stdout_drain.tail_text() == "out-1out-2\n"
    assert stderr_drain.tail_text() == "err-1err-2\n"

    runtime.cleanup()
    assert runtime.pid is None
