from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "runtime" / "src"))

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore


SMOKE_MIN_OUTPUT_TOKENS = 6000
SMOKE_PROBE_OUTPUT_TOKENS = 128


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _int_setting(source: dict[str, Any], *keys: str, default: int) -> int:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def choose_glm_provider_config() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    source = SQLiteStore(str(REPO_ROOT / "runtime" / ".local-agent-runtime.sqlite3"))
    try:
        provider = source.get_config({})["config"].get("provider", {})
    finally:
        source.close()

    profiles = provider.get("profiles") if isinstance(provider.get("profiles"), list) else []
    candidates: list[dict[str, Any]] = []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        haystack = " ".join(
            str(profile.get(key) or "")
            for key in ("id", "name", "model", "defaultModel", "fallbackModel", "baseUrl")
        ).lower()
        if "glm" in haystack:
            candidates.append(profile)
    if isinstance(provider, dict):
        haystack = " ".join(str(provider.get(key) or "") for key in ("name", "model", "baseUrl")).lower()
        if "glm" in haystack:
            candidates.append(provider)

    probe_results: list[dict[str, Any]] = []
    for candidate in candidates:
        if not (
            candidate.get("mode") != "mock"
            and candidate.get("baseUrl")
            and candidate.get("model")
            and (candidate.get("apiKey") or os.environ.get(str(candidate.get("apiKeyEnvVarName") or "")))
        ):
            continue
        base_profile = dict(candidate)
        probe_profile = dict(base_profile)
        probe_profile["timeout"] = _int_setting(probe_profile, "timeout", default=90)
        probe_profile["maxTokens"] = min(
            _int_setting(probe_profile, "maxTokens", "maxOutputTokens", default=SMOKE_PROBE_OUTPUT_TOKENS),
            SMOKE_PROBE_OUTPUT_TOKENS,
        )
        probe_profile["maxOutputTokens"] = min(
            _int_setting(probe_profile, "maxOutputTokens", "maxTokens", default=SMOKE_PROBE_OUTPUT_TOKENS),
            SMOKE_PROBE_OUTPUT_TOKENS,
        )
        provider_config = {
            **probe_profile,
            "activeProfileId": str(probe_profile.get("id") or "glm-probe"),
            "profiles": [probe_profile],
        }
        public = {
            "name": str(base_profile.get("name") or base_profile.get("id") or "GLM"),
            "mode": str(base_profile.get("mode") or ""),
            "apiFormat": str(base_profile.get("apiFormat") or "openai-chat"),
            "model": str(base_profile.get("model") or ""),
        }
        try:
            adapter = ProviderAdapter(config={"provider": provider_config})
            metadata = adapter.provider_request_metadata({"config": {"provider": provider_config}})
            response = adapter.chat(
                messages=[{"role": "user", "content": "Reply with exactly: ok"}],
                tools=None,
                context={"config": {"provider": provider_config}},
            )
            content = str((response.get("message") or {}).get("content") or "").strip()
            probe_results.append({**public, "status": "ok", "requestPath": metadata.get("requestPath"), "sample": content[:40]})
        except Exception as exc:  # noqa: BLE001
            probe_results.append({**public, "status": "failed", "error": str(exc)[:300]})
            continue

        selected = {
            "id": "glm-child-execution-smoke",
            "name": public["name"],
            "mode": str(base_profile.get("mode") or "openai-compatible"),
            "baseUrl": str(base_profile.get("baseUrl")),
            "model": str(base_profile.get("model")),
            "defaultModel": str(base_profile.get("defaultModel") or base_profile.get("model")),
            "fallbackModel": str(base_profile.get("fallbackModel") or base_profile.get("model")),
            "apiKeyEnvVarName": str(base_profile.get("apiKeyEnvVarName") or "LOCAL_AGENT_PROVIDER_API_KEY"),
            "apiFormat": str(base_profile.get("apiFormat") or "openai-chat"),
            "temperature": 0.0,
            "maxTokens": max(
                _int_setting(base_profile, "maxTokens", "maxOutputTokens", default=SMOKE_MIN_OUTPUT_TOKENS),
                SMOKE_MIN_OUTPUT_TOKENS,
            ),
            "maxOutputTokens": max(
                _int_setting(base_profile, "maxOutputTokens", "maxTokens", default=SMOKE_MIN_OUTPUT_TOKENS),
                SMOKE_MIN_OUTPUT_TOKENS,
            ),
            "maxContextTokens": _int_setting(base_profile, "maxContextTokens", default=120000),
            "timeout": _int_setting(base_profile, "timeout", default=90),
        }
        if base_profile.get("apiKey"):
            selected["apiKey"] = str(base_profile["apiKey"])
        return {**selected, "activeProfileId": selected["id"], "profiles": [selected]}, public, probe_results

    raise RuntimeError("No usable GLM provider config found: " + json.dumps(probe_results, ensure_ascii=False))


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "README.md").write_text(
        "# Score Smoke\n\nFix `normalize_score` and prove it with pytest.\n",
        encoding="utf-8",
    )
    (workspace / "score.py").write_text(
        "def normalize_score(value: int, maximum: int) -> float:\n"
        "    if maximum <= 0:\n"
        "        raise ValueError('maximum must be positive')\n"
        "    return round(maximum / value, 3)\n",
        encoding="utf-8",
    )
    (workspace / "test_score.py").write_text(
        "import pytest\n\n"
        "from score import normalize_score\n\n\n"
        "def test_normalizes_score():\n"
        "    assert normalize_score(25, 100) == 0.25\n\n\n"
        "def test_rounds_to_three_decimals():\n"
        "    assert normalize_score(1, 3) == 0.333\n\n\n"
        "def test_rejects_bad_maximum():\n"
        "    with pytest.raises(ValueError):\n"
        "        normalize_score(1, 0)\n",
        encoding="utf-8",
    )
    return workspace


def build_store(db_path: Path, workspace: Path, provider_config: dict[str, Any]) -> tuple[SQLiteStore, EventBus, dict[str, Any], dict[str, Any]]:
    event_bus = EventBus()
    store = SQLiteStore(str(db_path))
    store.update_config({
        "config": {
            "provider": provider_config,
            "permissions": {
                "preset": "autonomous",
                "capabilities": {
                    "readFile": {"mode": "allow", "scope": "*"},
                    "writeFile": {"mode": "allow", "scope": "*"},
                    "runCommand": {"mode": "allow", "scope": "*"},
                    "webFetch": {"mode": "blocked", "scope": "*"},
                    "network": {"mode": "blocked", "scope": "*"},
                    "subagents": {"mode": "allow", "scope": "*"},
                    "memoryWrite": {"mode": "allow", "scope": "*"},
                    "gitWrite": {"mode": "ask", "scope": "*"},
                    "hooksExecute": {"mode": "ask", "scope": "*"},
                },
            },
            "tools": {
                "runCommand": {
                    "allowedShell": "powershell",
                    "allowedCommands": [],
                    "allowlist": [],
                    "deniedCommands": [],
                    "denylist": [],
                    "blockedPatterns": ["rm -rf", "shutdown", "format"],
                    "allowedCwdRoots": [str(workspace)],
                }
            },
            "runtime": {"maxTaskSteps": 8},
        }
    })
    ws = store.upsert_workspace(str(workspace))
    session = store.create_session(workspace_id=ws["id"], title="GLM child execution smoke")
    parent = store.create_task(
        session_id=session["id"],
        task_type="chat",
        goal="Dispatch a child worker that fixes score.py and runs pytest.",
        plan=[],
        status="running",
    )
    return store, event_bus, session, parent


def run_direct_pytest(workspace: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=str(workspace),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    return {
        "exitCode": proc.returncode,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
    }


def main() -> int:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    root = REPO_ROOT / "runtime" / "smoke_runs" / f"child_execution_glm_{stamp}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    workspace = make_workspace(root)
    provider_config, provider_public, probe_results = choose_glm_provider_config()
    store, event_bus, session, parent = build_store(root / "runtime.sqlite3", workspace, provider_config)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    try:
        collaboration = CollaborationService(store, event_bus)
        subagents = SubagentService(store, collaboration)
        prompt = (
            f"Work inside workspaceRoot {workspace}. Fix score.py so normalize_score returns "
            "round(value / maximum, 3) while keeping the ValueError guard. Use a file edit tool "
            "such as apply_patch or write_file, then run `python -m pytest -q` with run_command "
            "from the workspace. Return a concise summary with changed files and the pytest result."
        )
        result = subagents.dispatch({
            "sessionId": session["id"],
            "taskId": parent["id"],
            "agentType": "worker",
            "title": "Fix score normalization",
            "prompt": prompt,
            "timeoutMs": 240000,
            "childToolAllowlist": [
                "list_dir",
                "search_files",
                "read_file",
                "apply_patch",
                "write_file",
                "run_command",
            ],
        })
        child_runtime_id = ((result.get("message") or {}).get("payload") or {}).get("runtimeTask", {}).get("id")
        child_task = store.get_task({"taskId": child_runtime_id})["task"] if child_runtime_id else None
        turns = store.list_provider_turns(child_runtime_id) if child_runtime_id else []
        command_logs = store.list_command_logs({"taskId": child_runtime_id, "limit": 20}).get("commandLogs", []) if child_runtime_id else []
        tasks = store.list_tasks({"sessionId": session["id"]})["tasks"]
        collab_tasks = store.list_collaboration_tasks({"sessionId": session["id"]})["tasks"]
        messages = store.list_agent_messages({"taskId": result.get("childTaskId")}).get("messages", []) if result.get("childTaskId") else []
        score_content = (workspace / "score.py").read_text(encoding="utf-8")
        direct_pytest = run_direct_pytest(workspace)
        event_counts = Counter(str(event.get("type")) for event in events)
        provider_turns = [
            {
                "turnIndex": turn.get("turn_index"),
                "status": turn.get("status"),
                "finishReason": turn.get("response_finish_reason"),
                "toolCallCount": turn.get("response_tool_call_count"),
                "requestToolCount": turn.get("request_tool_count"),
                "turnDecision": turn.get("turn_decision"),
                "errorSummary": turn.get("error_summary"),
            }
            for turn in turns
        ]
        coverage_failures: list[str] = []
        if result.get("status") != "completed":
            coverage_failures.append(f"child dispatch status was {result.get('status')!r}")
        if not child_task or child_task.get("status") != "completed":
            coverage_failures.append("child runtime task did not complete")
        if "return round(value / maximum, 3)" not in score_content:
            coverage_failures.append("score.py was not fixed")
        if direct_pytest["exitCode"] != 0:
            coverage_failures.append("independent pytest failed")
        command_names = [str(log.get("command") or "") for log in command_logs]
        if not any("pytest" in command for command in command_names):
            coverage_failures.append("child did not run pytest through run_command")
        if not any(turn.get("requestToolCount", 0) and turn.get("requestToolCount", 0) >= 5 for turn in provider_turns):
            coverage_failures.append("provider turn did not expose expanded child tools")
        summary = {
            "smokeRoot": str(root),
            "workspace": str(workspace),
            "provider": provider_public,
            "providerProbeResults": probe_results,
            "coverageStatus": "passed" if not coverage_failures else "failed",
            "coverageFailures": coverage_failures,
            "dispatch": {
                "status": result.get("status"),
                "childTaskId": result.get("childTaskId"),
                "executionMode": ((result.get("subagent") or {}).get("executionMode")),
                "summary": str(result.get("summary") or "")[:1200],
            },
            "childRuntimeTask": {
                "id": child_task.get("id") if child_task else None,
                "status": child_task.get("status") if child_task else None,
                "role": child_task.get("role") if child_task else None,
                "routing": child_task.get("routing") if child_task else None,
                "summary": str(child_task.get("resultSummary") or "")[:1200] if child_task else None,
            },
            "collaborationTasks": [
                {"id": task.get("id"), "status": task.get("status"), "result": task.get("result")}
                for task in collab_tasks
            ],
            "sessionTasks": [
                {"id": task.get("id"), "type": task.get("type"), "status": task.get("status"), "role": task.get("role")}
                for task in tasks
            ],
            "providerTurns": provider_turns,
            "commands": [
                {
                    "command": log.get("command"),
                    "status": log.get("status"),
                    "exitCode": log.get("exitCode"),
                    "cwd": log.get("cwd"),
                }
                for log in command_logs
            ],
            "directPytest": direct_pytest,
            "messages": [
                {"kind": message.get("kind"), "body": str(message.get("body") or "")[:800]}
                for message in messages
            ],
            "events": {"counts": dict(event_counts)},
            "scorePy": score_content,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if not coverage_failures else 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
