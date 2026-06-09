from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from local_agent_runtime.main import build_server


ANCHOR = "WORKTREE_MCP_SKILL_SMOKE"


def _rpc(server: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
    envelope = {
        "jsonrpc": "2.0",
        "id": f"req_{int(time.time() * 1000)}_{method}",
        "method": method,
        "params": params,
    }
    response = server.handle_line(json.dumps(envelope, ensure_ascii=False))
    if "error" in response:
        raise RuntimeError(f"{method} failed: {response['error']}")
    return response["result"]


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _record_check(checks: list[dict[str, Any]], failures: list[str], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": passed, "detail": detail})
    if not passed:
        failures.append(f"{name}: {detail}")


def _short_commands(command_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for command in command_logs:
        commands.append(
            {
                "id": command.get("id"),
                "taskId": command.get("taskId"),
                "command": command.get("command"),
                "cwd": command.get("cwd"),
                "status": command.get("status"),
                "exitCode": command.get("exitCode"),
            }
        )
    return commands


def _init_repo(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "README.md").write_text(
        f"# {ANCHOR}\n\nUse the MCP knowledge base before editing.\n",
        encoding="utf-8",
    )
    (workspace / "rules.py").write_text(
        "def normalize_status(value: str) -> str:\n"
        "    return value.strip().lower()\n",
        encoding="utf-8",
    )
    (workspace / "test_rules.py").write_text(
        "from rules import normalize_status\n\n"
        "def test_normalize_status():\n"
        "    assert normalize_status(' Mitigated ') == 'mitigated'\n",
        encoding="utf-8",
    )
    _run(["git", "init"], workspace)
    _run(["git", "config", "user.email", "smoke@example.com"], workspace)
    _run(["git", "config", "user.name", "Smoke Test"], workspace)
    _run(["git", "add", "."], workspace)
    _run(["git", "commit", "-m", "initial"], workspace)


def main() -> int:
    start = time.time()
    smoke_root = Path(__file__).resolve().parent / f"worktree_mcp_skill_{time.strftime('%Y%m%d_%H%M%S')}"
    workspace = smoke_root / "workspace"
    smoke_root.mkdir(parents=True, exist_ok=True)
    _init_repo(workspace)

    db_path = smoke_root / "runtime.sqlite3"
    old_repo_root = os.environ.get("LOCAL_AGENT_REPO_ROOT")
    old_db_path = os.environ.get("LOCAL_AGENT_DB_PATH")
    os.environ["LOCAL_AGENT_REPO_ROOT"] = str(workspace)
    os.environ["LOCAL_AGENT_DB_PATH"] = str(db_path)

    server = build_server(database_path=str(db_path))
    try:
        provider_base = os.environ.get("YUANBAO_SMOKE_PROVIDER_BASE_URL", "http://pixel.try-chatapi.com")
        provider_key = os.environ.get("YUANBAO_SMOKE_PROVIDER_API_KEY", "")
        provider_model = os.environ.get("YUANBAO_SMOKE_PROVIDER_MODEL", "gpt-5.4")
        provider_api_format = os.environ.get("YUANBAO_SMOKE_PROVIDER_API_FORMAT", "openai-chat")
        provider_timeout = int(os.environ.get("YUANBAO_SMOKE_PROVIDER_TIMEOUT", "180"))
        max_task_steps = int(os.environ.get("YUANBAO_SMOKE_MAX_TASK_STEPS", "20"))
        child_timeout_ms = int(os.environ.get("YUANBAO_SMOKE_CHILD_TIMEOUT_MS", "60000"))

        _rpc(server, "config.update", {
            "config": {
                "provider": {
                    "mode": "openai-compatible",
                    "apiFormat": provider_api_format,
                    "baseUrl": provider_base,
                    "apiKey": provider_key,
                    "model": provider_model,
                    "streamingEnabled": True,
                    "timeout": provider_timeout,
                },
                "policy": {
                    "approvalMode": "none",
                    "maxTaskSteps": max_task_steps,
                },
                "autonomy": {
                    "activeProfileId": "worktree-mcp",
                    "profiles": [
                        {
                            "id": "worktree-mcp",
                            "name": "Worktree MCP Smoke",
                            "level": "L3",
                            "maxSteps": 20,
                            "maxParallelSubtasks": 4,
                            "allowBackground": True,
                            "allowSubagents": True,
                            "allowFileWrite": "allowed",
                            "allowShell": "allowed",
                            "allowNetwork": False,
                            "memoryRecallPolicy": "workspace_first_session_boosted",
                            "retryLimit": 2,
                            "timeoutMs": 600000,
                            "childTaskTimeoutMs": child_timeout_ms,
                            "compactionThreshold": 20000,
                        }
                    ],
                },
                "worktree": {
                    "autoBindWriteTasks": True,
                    "baseRef": "HEAD",
                    "cleanupPolicy": "keep",
                    "mergePolicy": "approval_required",
                },
            }
        })

        mcp_server_script = Path(__file__).resolve().parent / "local_kb_mcp_server.py"
        _rpc(server, "mcp.server.create", {
            "id": "kb",
            "name": "KB",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(mcp_server_script)],
            "enabled": True,
        })

        _rpc(server, "skill.create", {
            "id": "worktree_mcp_skill",
            "name": "Worktree MCP Skill",
            "description": "Uses MCP guidance while editing only inside the active worktree.",
            "system_prompt": (
                "Before editing, consult the MCP knowledge base. "
                "Keep changes scoped to the active worktree and verify with pytest."
            ),
            "tool_whitelist": ["read_file", "write_file", "run_command", "search_files"],
            "parameter_constraints": {},
            "category": "coding",
            "tool_policy": "inherit_mcp",
        })

        workspace_record = _rpc(server, "workspace.open", {"path": str(workspace)})["workspace"]
        session = _rpc(server, "session.create", {"workspaceId": workspace_record["id"], "title": "Worktree MCP Smoke"})["session"]

        prompt = (
            f"Using the focus anchor {ANCHOR}, first consult the MCP knowledge base, then update README.md "
            "with a short 'Guidance' section that mentions the anchor, and finally run python -m pytest -q. "
            "Do not change behavior in rules.py unless tests require it."
        )
        result = _rpc(server, "message.send", {"sessionId": session["id"], "content": prompt, "skillId": "worktree_mcp_skill"})
        task = result["task"]
        task_id = task["id"]

        provider_turns = _rpc(server, "provider_turn.list", {"taskId": task_id}).get("turns", [])
        snapshots = _rpc(server, "context_snapshot.list", {"taskId": task_id}).get("snapshots", [])
        worktree = _rpc(server, "worktree.getByTask", {"taskId": task_id}).get("worktree")
        persisted_task = _rpc(server, "task.get", {"taskId": task_id})["task"]
        autonomy_report = _rpc(server, "autonomy.report", {"taskId": task_id})
        command_logs = _rpc(server, "command_log.list", {"sessionId": session["id"], "limit": 100}).get("commandLogs", [])
        skill_usage = _rpc(server, "skill.usage", {"skillId": "worktree_mcp_skill"}).get("usage", [])
        mcp_servers = _rpc(server, "mcp.server.list", {}).get("servers", [])

        readme_text = (workspace / "README.md").read_text(encoding="utf-8")
        worktree_readme = ""
        if worktree and worktree.get("worktreePath"):
            wt_path = Path(worktree["worktreePath"])
            if (wt_path / "README.md").exists():
                worktree_readme = (wt_path / "README.md").read_text(encoding="utf-8")

        direct_pytest = _run([sys.executable, "-m", "pytest", "-q"], workspace)
        checks: list[dict[str, Any]] = []
        failures: list[str] = []
        task_status = persisted_task.get("status") or task.get("status")
        routing = persisted_task.get("routing") if isinstance(persisted_task.get("routing"), dict) else {}
        routed_skill = routing.get("skill_id") or routing.get("skillId")
        active_worktree = routing.get("activeWorktree") or routing.get("active_worktree")
        kb_servers = [server for server in mcp_servers if server.get("id") == "kb"]
        kb_server = kb_servers[0] if kb_servers else {}
        successful_pytest_commands = [
            command
            for command in command_logs
            if "pytest" in str(command.get("command", ""))
            and command.get("status") in {"completed", "succeeded"}
            and command.get("exitCode") == 0
        ]

        _record_check(
            checks,
            failures,
            "task completed",
            task_status == "completed",
            f"expected completed, got {task_status!r}",
        )
        _record_check(
            checks,
            failures,
            "mcp server connected",
            bool(kb_server)
            and (kb_server.get("connected") is True or kb_server.get("status") == "connected")
            and int(kb_server.get("toolCount") or 0) >= 1,
            (
                f"kb connected={kb_server.get('connected')!r}, "
                f"status={kb_server.get('status')!r}, toolCount={kb_server.get('toolCount')!r}"
            ),
        )
        _record_check(
            checks,
            failures,
            "skill usage recorded",
            len(skill_usage) >= 1 and routed_skill == "worktree_mcp_skill",
            f"usage={len(skill_usage)}, routedSkill={routed_skill!r}",
        )
        _record_check(
            checks,
            failures,
            "worktree bound",
            bool(worktree and worktree.get("worktreePath") and active_worktree),
            f"worktreePath={worktree.get('worktreePath') if worktree else None!r}, activeWorktree={bool(active_worktree)}",
        )
        _record_check(
            checks,
            failures,
            "root workspace unchanged",
            "## Guidance" not in readme_text,
            "root README should not receive generated guidance",
        )
        _record_check(
            checks,
            failures,
            "worktree README updated from MCP guidance",
            "## Guidance" in worktree_readme and ANCHOR in worktree_readme,
            "worktree README must include Guidance section and anchor",
        )
        _record_check(
            checks,
            failures,
            "task command log includes pytest",
            bool(successful_pytest_commands),
            f"successful pytest command logs={len(successful_pytest_commands)}",
        )
        _record_check(
            checks,
            failures,
            "direct pytest still passes",
            direct_pytest.returncode == 0,
            f"direct pytest exit={direct_pytest.returncode}",
        )

        report = {
            "ok": not failures,
            "checks": checks,
            "failures": failures,
            "smokeRoot": str(smoke_root),
            "workspace": str(workspace),
            "providerModel": provider_model,
            "providerApiFormat": provider_api_format,
            "task": {
                "id": task_id,
                "status": task_status,
                "routing": task.get("routing"),
                "summary": task.get("summary") or task.get("resultSummary"),
            },
            "worktree": worktree,
            "mcpServers": mcp_servers,
            "skillUsageCount": len(skill_usage),
            "providerTurns": len(provider_turns),
            "contextSnapshots": len(snapshots),
            "autonomyReportCounts": {
                "commands": len(autonomy_report.get("commands", [])),
                "subagents": len(autonomy_report.get("subagents", [])),
                "compactions": len(autonomy_report.get("compactions", [])),
                "decisions": len(autonomy_report.get("decisions", [])),
            },
            "commandLogs": _short_commands(command_logs),
            "persistedRouting": persisted_task.get("routing"),
            "rootReadme": readme_text,
            "worktreeReadme": worktree_readme,
            "directPytest": {
                "returnCode": direct_pytest.returncode,
                "stdout": direct_pytest.stdout,
                "stderr": direct_pytest.stderr,
            },
            "durationSeconds": round(time.time() - start, 2),
        }
        report_text = json.dumps(report, ensure_ascii=False, indent=2)
        (smoke_root / "report.json").write_text(report_text, encoding="utf-8")
        try:
            sys.stdout.write(report_text + "\n")
        except UnicodeEncodeError:
            sys.stdout.buffer.write((report_text + "\n").encode("utf-8", errors="replace"))
        return 0 if not failures else 1
    finally:
        try:
            server.graceful_shutdown()
        finally:
            if old_repo_root is None:
                os.environ.pop("LOCAL_AGENT_REPO_ROOT", None)
            else:
                os.environ["LOCAL_AGENT_REPO_ROOT"] = old_repo_root
            if old_db_path is None:
                os.environ.pop("LOCAL_AGENT_DB_PATH", None)
            else:
                os.environ["LOCAL_AGENT_DB_PATH"] = old_db_path


if __name__ == "__main__":
    raise SystemExit(main())
