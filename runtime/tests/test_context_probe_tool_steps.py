from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.code_search import build_code_search_tool
from local_agent_runtime.tools.git_diff import build_git_diff_tool
from local_agent_runtime.tools.git_status import build_git_status_tool
from local_agent_runtime.tools.list_dir import build_list_dir_tool
from local_agent_runtime.tools.read_file import build_read_file_tool
from local_agent_runtime.tools.search_files import build_search_files_tool


def _store() -> SQLiteStore:
    return SQLiteStore(":memory:")


def _policy() -> PolicyGuard:
    return PolicyGuard(approval_mode="none")


def _step_labels(result: dict) -> list[str]:
    return [step["label"] for step in result.get("steps", [])]


def test_read_file_result_includes_runtime_steps(tmp_path: Path) -> None:
    (tmp_path / "alpha.txt").write_text("hello world", encoding="utf-8")
    tool = build_read_file_tool(_policy(), _store())["handler"]

    result = tool({"workspaceRoot": str(tmp_path), "path": "alpha.txt"})

    assert result["content"] == "hello world"
    assert _step_labels(result) == ["resolve", "read", "decode"]
    assert result["steps"][0]["summary"] == "alpha.txt"
    assert result["steps"][1]["summary"] == "11 byte(s)"


def test_list_dir_result_includes_runtime_steps(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    tool = build_list_dir_tool(_policy(), _store())["handler"]

    result = tool({"workspaceRoot": str(tmp_path), "path": "src"})

    assert result["path"] == "src"
    assert result["items"]
    assert _step_labels(result) == ["resolve", "walk"]
    assert result["steps"][-1]["summary"] == "1 item(s)"


def test_search_files_result_includes_runtime_steps(tmp_path: Path) -> None:
    (tmp_path / "alpha.txt").write_text("needle here\n", encoding="utf-8")
    tool = build_search_files_tool(_policy(), _store())["handler"]

    result = tool({"workspaceRoot": str(tmp_path), "query": "needle", "mode": "content"})

    assert result["total"] >= 1
    assert _step_labels(result)[0:2] == ["prepare", "backend"]
    assert _step_labels(result)[-1] == "search"
    assert "needle" in result["steps"][0]["summary"]


def test_code_search_result_includes_runtime_steps(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def target_function():\n    return True\n", encoding="utf-8")
    tool = build_code_search_tool(_policy(), _store())["handler"]

    result = tool({
        "workspaceRoot": str(tmp_path),
        "path": "src",
        "query": "target",
        "mode": "definition",
    })

    assert result["totalMatches"] == 1
    assert _step_labels(result) == ["prepare", "walk", "scan", "match"]
    assert "src" in result["steps"][1]["summary"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git executable is not available")
def test_git_status_and_diff_results_include_runtime_steps(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True, text=True)
    tracked.write_text("after\n", encoding="utf-8")

    status_tool = build_git_status_tool(_policy(), _store())["handler"]
    diff_tool = build_git_diff_tool(_policy(), _store())["handler"]

    status = status_tool({"workspaceRoot": str(tmp_path)})
    diff = diff_tool({"workspaceRoot": str(tmp_path)})

    assert status["isGitRepository"] is True
    assert status["changes"]
    assert _step_labels(status) == ["resolve", "repository", "status"]
    assert status["steps"][-1]["summary"] == "1 change(s)"
    assert diff["files"]
    assert _step_labels(diff) == ["resolve", "repository", "diff", "files"]
    assert diff["steps"][-1]["summary"] == "1 file(s)"


@pytest.mark.skipif(shutil.which("git") is None, reason="git executable is not available")
def test_git_diff_large_diff_registers_patch_artifact(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True, text=True)
    tracked.write_text("after\n" + ("x" * 10_000) + "\n", encoding="utf-8")

    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    diff_tool = build_git_diff_tool(_policy(), store)["handler"]

    result = diff_tool({"workspaceRoot": str(tmp_path), "sessionId": "s1", "taskId": "t1"})

    assert result["artifactId"].startswith("art")
    artifacts = store.list_artifacts({"sessionId": "s1"})["artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "patch"
    assert artifacts[0]["content"]["diff"] == result["diff"]
    assert artifacts[0]["metadata"]["source"] == "git_diff"
