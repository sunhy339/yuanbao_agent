from __future__ import annotations

import os
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from local_agent_runtime.services.worker_process_transport import (
    WorkerProcessExitError,
    WorkerProcessTimeoutError,
    WorkerProcessTransport,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SRC = Path(__file__).resolve().parents[1] / "src"


def _python_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    python_path_entries = [str(RUNTIME_SRC)]
    existing = env.get("PYTHONPATH")
    if existing:
        python_path_entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
    if extra:
        env.update(extra)
    return env


def _result(response: dict[str, object], key: str) -> dict[str, object]:
    payload = response["result"]
    assert isinstance(payload, dict)
    value = payload[key]
    assert isinstance(value, dict)
    return value


def _write_child_script(tmp_path: Path, name: str, body: str) -> Path:
    script_path = tmp_path / name
    script_path.write_text(dedent(body), encoding="utf-8")
    return script_path


def _read_event(transport: WorkerProcessTransport, event_type: str, *, timeout: float) -> dict[str, object]:
    while True:
        event = transport.recv_event(timeout=timeout)
        if event["type"] == event_type:
            return event


def test_worker_process_transport_handles_rpc_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite3"

    with WorkerProcessTransport.for_python_module(
        "local_agent_runtime.main",
        cwd=str(PROJECT_ROOT),
        env=_python_env({"LOCAL_AGENT_DB_PATH": str(db_path)}),
    ) as transport:
        response = transport.request("config.get", {}, timeout=5.0)

    assert response["jsonrpc"] == "2.0"
    assert "error" not in response
    config = _result(response, "config")
    policy = config["policy"]
    assert isinstance(policy, dict)
    assert policy["approvalMode"] in {"manual", "never", "on-request", "on_write_or_command"}


def test_worker_process_transport_receives_events_without_losing_rpc_responses(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite3"

    with WorkerProcessTransport.for_python_module(
        "local_agent_runtime.main",
        cwd=str(PROJECT_ROOT),
        env=_python_env({"LOCAL_AGENT_DB_PATH": str(db_path)}),
    ) as transport:
        workspace = _result(
            transport.request("workspace.open", {"path": str(tmp_path / "workspace")}, timeout=5.0),
            "workspace",
        )
        session = _result(
            transport.request(
                "session.create",
                {"workspaceId": workspace["id"], "title": "worker transport"},
                timeout=5.0,
            ),
            "session",
        )
        worker = _result(
            transport.request(
                "collab.worker.upsert",
                {
                    "workerId": "transport_worker",
                    "name": "Transport Worker",
                    "role": "worker",
                    "capabilities": ["collab"],
                },
                timeout=5.0,
            ),
            "worker",
        )

        response = transport.request(
            "collab.task.create",
            {
                "sessionId": session["id"],
                "title": "transport event",
                "description": "emit an event before the rpc response returns",
                "priority": 2,
                "metadata": {"source": "transport-test", "workerId": worker["id"]},
            },
            timeout=5.0,
        )
        created_event = _read_event(transport, "collab.task.created", timeout=5.0)

    task = _result(response, "task")
    assert task["title"] == "transport event"
    assert created_event["taskId"] == task["id"]
    payload = created_event["payload"]
    assert isinstance(payload, dict)
    assert payload["task"]["id"] == task["id"]


def test_worker_process_transport_times_out_waiting_for_response(tmp_path: Path) -> None:
    script_path = _write_child_script(
        tmp_path,
        "slow_child.py",
        """
        import json
        import sys
        import time

        for raw_line in sys.stdin:
            request = json.loads(raw_line)
            time.sleep(0.5)
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"ok": True},
            }) + "\\n")
            sys.stdout.flush()
        """,
    )

    with WorkerProcessTransport(
        [sys.executable, "-u", str(script_path)],
        cwd=str(PROJECT_ROOT),
        env=_python_env(),
    ) as transport:
        with pytest.raises(WorkerProcessTimeoutError):
            transport.request("slow.call", {}, timeout=0.05)


def test_worker_process_transport_raises_when_child_exits_before_reply(tmp_path: Path) -> None:
    script_path = _write_child_script(
        tmp_path,
        "exit_child.py",
        """
        import sys

        sys.stdin.readline()
        raise SystemExit(7)
        """,
    )

    with WorkerProcessTransport(
        [sys.executable, "-u", str(script_path)],
        cwd=str(PROJECT_ROOT),
        env=_python_env(),
    ) as transport:
        with pytest.raises(WorkerProcessExitError) as exc_info:
            transport.request("child.exit", {}, timeout=2.0)

    assert exc_info.value.returncode == 7


def test_worker_process_transport_exposes_stream_callbacks_and_tails(tmp_path: Path) -> None:
    script_path = _write_child_script(
        tmp_path,
        "streaming_child.py",
        """
        import json
        import sys
        import time

        for raw_line in sys.stdin:
            request = json.loads(raw_line)
            sys.stdout.write("log:")
            sys.stdout.flush()
            time.sleep(0.05)
            sys.stdout.write("ready\\n")
            sys.stdout.flush()
            sys.stderr.write("warn:")
            sys.stderr.flush()
            time.sleep(0.05)
            sys.stderr.write("done\\n")
            sys.stderr.flush()
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"ok": True},
            }) + "\\n")
            sys.stdout.flush()
        """,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    with WorkerProcessTransport(
        [sys.executable, "-u", str(script_path)],
        cwd=str(PROJECT_ROOT),
        env=_python_env(),
    ) as transport:
        transport.add_stream_callback("stdout", stdout_chunks.append)
        transport.add_stream_callback("stderr", stderr_chunks.append)
        response = transport.request("stream.call", {}, timeout=2.0)
        drained_stdout = "".join(transport.take_stream_chunks("stdout"))
        drained_stderr = "".join(transport.take_stream_chunks("stderr"))

    result = response["result"]
    assert isinstance(result, dict)
    assert result["ok"] is True
    assert "log:ready\n" in "".join(stdout_chunks)
    assert "warn:done\n" in "".join(stderr_chunks)
    assert "log:ready\n" in drained_stdout
    assert "\"ok\": true" in drained_stdout
    assert drained_stderr == "warn:done\n"
    assert transport.take_stream_chunks("stderr") == []
    assert transport.stream_tail("stderr") == "warn:done\n"


def test_worker_process_transport_times_out_when_child_only_streams_partial_output(tmp_path: Path) -> None:
    script_path = _write_child_script(
        tmp_path,
        "partial_only_child.py",
        """
        import json
        import sys
        import time

        for raw_line in sys.stdin:
            json.loads(raw_line)
            sys.stdout.write("partial-stdout")
            sys.stdout.flush()
            sys.stderr.write("partial-stderr")
            sys.stderr.flush()
            time.sleep(0.5)
        """,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    with WorkerProcessTransport(
        [sys.executable, "-u", str(script_path)],
        cwd=str(PROJECT_ROOT),
        env=_python_env(),
    ) as transport:
        transport.add_stream_callback("stdout", stdout_chunks.append)
        transport.add_stream_callback("stderr", stderr_chunks.append)
        with pytest.raises(WorkerProcessTimeoutError):
            transport.request("partial.call", {}, timeout=0.1)
        drained_stdout = "".join(transport.take_stream_chunks("stdout"))
        drained_stderr = "".join(transport.take_stream_chunks("stderr"))

    assert "partial-stdout" in "".join(stdout_chunks)
    assert "partial-stderr" in "".join(stderr_chunks)
    assert drained_stdout == "partial-stdout"
    assert drained_stderr == "partial-stderr"
    assert transport.stream_tail("stdout") == "partial-stdout"
    assert transport.stream_tail("stderr") == "partial-stderr"
