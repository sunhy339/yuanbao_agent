from __future__ import annotations

import sys
from pathlib import Path

from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.policy.permission_engine import PermissionEngine
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools._shared import run_shell
from local_agent_runtime.tools.run_command import _powershell_execution_command, build_run_command_tool


def test_powershell_type_reads_utf8_file_without_mojibake(tmp_path: Path) -> None:
    source_text = "# \u6ce8\u91ca: \u4fdd\u7559\u4e2d\u6587\nprint('\u5b8c\u6210')\n"
    source = tmp_path / "calculator.py"
    source.write_bytes(source_text.encode("utf-8"))

    stdout, stderr, exit_code, status, _duration_ms = run_shell(
        "powershell",
        "type calculator.py",
        tmp_path,
        10_000,
    )

    assert status == "completed"
    assert exit_code == 0
    assert stderr == ""
    assert stdout == source_text


def test_powershell_native_command_output_uses_utf8(tmp_path: Path) -> None:
    native_text = "\u7f16\u7801\u8f93\u51fa\u6b63\u5e38"
    command = f'"{sys.executable}" -c "print(\'{native_text}\')"'

    stdout, stderr, exit_code, status, _duration_ms = run_shell("powershell", command, tmp_path, 10_000)

    assert status == "completed"
    assert exit_code == 0
    assert stderr == ""
    assert stdout.strip() == native_text


def test_powershell_native_exe_preserves_nonzero_exit(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("def nope(:\n", encoding="utf-8")
    command = f'& "{sys.executable}" -m py_compile bad.py'

    stdout, stderr, exit_code, status, _duration_ms = run_shell("powershell", command, tmp_path, 10_000)

    assert status == "failed"
    assert exit_code not in (0, None)
    assert stdout == ""
    assert "SyntaxError" in stderr


def test_powershell_execution_command_prefixes_quoted_executable() -> None:
    command = r'"C:\Python314\python.exe" -m pytest -q'

    assert _powershell_execution_command(command, "powershell") == f"& {command}"


def test_powershell_execution_command_adapts_bash_and_chain() -> None:
    command = 'cd "C:\\demo" && python -m py_compile game.py && echo "SYNTAX OK"'
    adapted = _powershell_execution_command(command, "powershell")

    assert "&&" not in adapted
    assert "if ($?)" in adapted
    assert "py_compile game.py" in adapted


def test_powershell_and_chain_executes_on_windows_powershell(tmp_path: Path) -> None:
    source = tmp_path / "game.py"
    source.write_text("print('ok')\n", encoding="utf-8")

    command = _powershell_execution_command(
        f'cd "{tmp_path}" && "{sys.executable}" -m py_compile game.py && echo "SYNTAX OK"',
        "powershell",
    )
    stdout, stderr, exit_code, status, _duration_ms = run_shell("powershell", command, tmp_path, 10_000)

    assert status == "completed"
    assert exit_code == 0
    assert stderr == ""
    assert "SYNTAX OK" in stdout


def test_powershell_multi_file_listing_adaptation_executes(tmp_path: Path) -> None:
    for name in ("index.html", "styles.css", "app.js", "README.md"):
        (tmp_path / name).write_text("ok\n", encoding="utf-8")

    command = _powershell_execution_command(
        f'cd /d "{tmp_path}" && ls -la index.html styles.css app.js README.md 2>&1',
        "powershell",
    )
    stdout, stderr, exit_code, status, _duration_ms = run_shell("powershell", command, tmp_path, 10_000)

    assert status == "completed"
    assert exit_code == 0
    assert stderr == ""
    assert "index.html" in stdout
    assert "README.md" in stdout


def test_run_command_falls_back_from_missing_bash_and_expands_py_compile_globs(tmp_path: Path) -> None:
    package = tmp_path / "kanban_cli"
    tests = tmp_path / "tests"
    package.mkdir()
    tests.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "models.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tests / "test_models.py").write_text("def test_value():\n    assert 1 == 1\n", encoding="utf-8")

    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    workspace = store.upsert_workspace(str(tmp_path))
    session = store.create_session(workspace_id=workspace["id"], title="run command")
    task = store.create_task(session_id=session["id"], task_type="main", goal="verify", plan=[])
    config = store.get_config({})["config"]
    tool = build_run_command_tool(
        PolicyGuard(approval_mode=config["policy"]["approvalMode"]),
        store,
        permission_engine=PermissionEngine(config=config, store=store),
    )["handler"]

    result = tool({
        "workspaceRoot": str(tmp_path),
        "taskId": task["id"],
        "command": "python -m py_compile kanban_cli/*.py tests/*.py",
        "cwd": ".",
        "shell": "bash",
        "timeoutMs": 30_000,
    })

    assert result["status"] == "completed"
    assert result["exitCode"] == 0
    assert result["shell"] == "powershell"
    assert "executedCommand" in result
