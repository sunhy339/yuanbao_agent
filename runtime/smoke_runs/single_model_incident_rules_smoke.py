from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from long_running_fullstack_smoke import (
    REPO_ROOT,
    _int_setting,
    _positive_env_int,
    aggregate_autonomy_reports,
    build_runtime,
    checked_run,
    classify_failure,
    rpc,
    run,
    task_ids_for_session,
)
from local_agent_runtime.memory.types import MemoryKind
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.store.sqlite_store import SQLiteStore


ANCHOR_FOCUS = "SINGLE_MODEL_INCIDENT_RULES_ENGINE"
ANCHOR_MEMORY = "INCIDENT_RULES_MEMORY_SQLITE_PYTEST"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "README.md").write_text(
        "# Incident Rules Engine Fixture\n\n"
        "Build a local incident operations rules engine. Keep the focus anchors in docs and tests.\n",
        encoding="utf-8",
    )
    (workspace / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\npythonpath = [\".\"]\n",
        encoding="utf-8",
    )
    (workspace / "incident_seed.py").write_text(
        "from __future__ import annotations\n\n"
        "SEVERITIES = ['sev1', 'sev2', 'sev3', 'sev4']\n"
        "STATUSES = ['new', 'acknowledged', 'mitigated', 'resolved']\n",
        encoding="utf-8",
    )
    checked_run(["git", "init"], workspace)
    checked_run(["git", "config", "user.email", "smoke@example.invalid"], workspace)
    checked_run(["git", "config", "user.name", "Yuanbao Incident Smoke"], workspace)
    checked_run(["git", "add", "."], workspace)
    checked_run(["git", "commit", "-m", "initial incident rules fixture"], workspace)
    return workspace


def choose_single_provider_config() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    min_provider_timeout = _positive_env_int("YUANBAO_SMOKE_PROVIDER_MIN_TIMEOUT", default=120)
    override_base_url = os.environ.get("YUANBAO_SMOKE_PROVIDER_BASE_URL")
    override_model = os.environ.get("YUANBAO_SMOKE_PROVIDER_MODEL")
    override_api_key = os.environ.get("YUANBAO_SMOKE_PROVIDER_API_KEY")
    if override_base_url and override_model and override_api_key:
        profile = {
            "id": "single-model-env-smoke",
            "name": os.environ.get("YUANBAO_SMOKE_PROVIDER_NAME") or override_model,
            "mode": os.environ.get("YUANBAO_SMOKE_PROVIDER_MODE") or "openai-compatible",
            "baseUrl": override_base_url,
            "model": override_model,
            "defaultModel": override_model,
            "fallbackModel": override_model,
            "apiKeyEnvVarName": "YUANBAO_SMOKE_PROVIDER_API_KEY",
            "apiFormat": os.environ.get("YUANBAO_SMOKE_PROVIDER_API_FORMAT") or "openai-chat",
            "temperature": float(os.environ.get("YUANBAO_SMOKE_PROVIDER_TEMPERATURE") or "0.1"),
            "maxTokens": _int_setting(os.environ, "YUANBAO_SMOKE_PROVIDER_MAX_TOKENS", default=6000),
            "maxOutputTokens": _int_setting(os.environ, "YUANBAO_SMOKE_PROVIDER_MAX_TOKENS", default=6000),
            "maxContextTokens": _positive_env_int("YUANBAO_SMOKE_MAX_CONTEXT_TOKENS", default=20000),
            "timeout": max(
                _int_setting(os.environ, "YUANBAO_SMOKE_PROVIDER_TIMEOUT", default=180),
                min_provider_timeout,
            ),
            "streamingEnabled": os.environ.get("YUANBAO_SMOKE_STREAMING_ENABLED", "1").strip() != "0",
        }
    else:
        source = SQLiteStore(str(REPO_ROOT / "runtime" / ".local-agent-runtime.sqlite3"))
        try:
            provider = source.get_config({})["config"].get("provider", {})
        finally:
            source.close()
        profiles = provider.get("profiles") if isinstance(provider.get("profiles"), list) else []
        active_id = provider.get("activeProfileId")
        active_profile = next(
            (
                item for item in profiles
                if isinstance(item, dict) and item.get("id") == active_id
            ),
            None,
        )
        if not isinstance(active_profile, dict):
            raise RuntimeError(f"Active provider profile not found: {active_id!r}")
        profile = dict(active_profile)
        profile["id"] = "single-model-active-smoke"
        profile["timeout"] = max(_int_setting(profile, "timeout", default=180), min_provider_timeout)
        profile["maxTokens"] = max(_int_setting(profile, "maxTokens", "maxOutputTokens", default=6000), 6000)
        profile["maxOutputTokens"] = max(_int_setting(profile, "maxOutputTokens", "maxTokens", default=6000), 6000)
        profile["maxContextTokens"] = _positive_env_int("YUANBAO_SMOKE_MAX_CONTEXT_TOKENS", default=20000)
        profile["streamingEnabled"] = os.environ.get("YUANBAO_SMOKE_STREAMING_ENABLED", "1").strip() != "0"

    public = {
        "name": str(profile.get("name") or profile.get("id") or "provider"),
        "mode": str(profile.get("mode") or ""),
        "apiFormat": str(profile.get("apiFormat") or "openai-chat"),
        "model": str(profile.get("model") or ""),
        "source": "env_override" if override_base_url and override_model and override_api_key else "active_profile",
    }
    if str(profile.get("mode") or "").strip().lower() == "mock":
        raise RuntimeError("Active provider profile is mock; real LLM smoke requires a non-mock provider.")
    if not profile.get("baseUrl") or not profile.get("model"):
        raise RuntimeError("Active provider profile is missing baseUrl or model.")
    env_name = str(profile.get("apiKeyEnvVarName") or "").strip()
    if not profile.get("apiKey") and (not env_name or not os.environ.get(env_name)):
        raise RuntimeError(f"Active provider profile has no available API key; set {env_name or 'provider api key env var'}.")

    provider_config = {**profile, "activeProfileId": profile["id"], "profiles": [profile]}
    adapter = ProviderAdapter(config={"provider": provider_config})
    metadata = adapter.provider_request_metadata({"config": {"provider": provider_config}})
    try:
        response = adapter.chat(
            messages=[{"role": "user", "content": "Reply with exactly: ok"}],
            tools=None,
            context={"config": {"provider": provider_config}},
        )
        content = str((response.get("message") or {}).get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001
        probe = {**public, "status": "failed", "requestPath": metadata.get("requestPath"), "error": str(exc)[:500]}
        raise RuntimeError("Single active provider probe failed: " + json.dumps(probe, ensure_ascii=False)) from exc
    probe = {**public, "status": "ok", "requestPath": metadata.get("requestPath"), "sample": content[:40]}
    return provider_config, public, [probe]


def _proposal_kinds(store: Any, task_ids: list[str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for task_id in task_ids:
        try:
            proposals = store.list_proposals({"taskId": task_id}).get("proposals", [])
        except Exception:  # noqa: BLE001
            continue
        for proposal in proposals:
            if isinstance(proposal, dict):
                counts[str(proposal.get("kind") or "unknown")] += 1
    return dict(counts)


def _completion_evidence(task: dict[str, Any]) -> dict[str, Any]:
    structured = task.get("structuredResult")
    if not isinstance(structured, dict):
        return {}
    evidence = structured.get("completionEvidence")
    return evidence if isinstance(evidence, dict) else {}


def _compile_modules(workspace: Path, files: list[str]) -> dict[str, Any]:
    module_files = [
        str(workspace / name)
        for name in files
        if name.startswith("incident_") and name.endswith(".py") and not name.startswith("test_")
    ]
    if not module_files:
        return {"returnCode": 1, "stdout": "", "stderr": "no incident module files found"}
    completed = run([sys.executable, "-m", "py_compile", *module_files], workspace, timeout=180)
    return {
        "returnCode": completed.returncode,
        "stdout": completed.stdout[-3000:],
        "stderr": completed.stderr[-3000:],
    }


def _classify_runner_exception(exc: Exception) -> str:
    text = str(exc).casefold()
    if "single active provider probe failed" in text:
        if "no upstream channel available" in text or "503" in text:
            return "provider_unavailable"
        if "api key expired" in text or "403" in text or "unauthorized" in text or "401" in text:
            return "provider_auth_or_key_failure"
        if "timed out" in text or "timeout" in text:
            return "provider_timeout"
        return "provider_probe_failed"
    if "active provider profile is mock" in text:
        return "provider_not_real"
    if "no available api key" in text or "set " in text and "api key" in text:
        return "provider_missing_key"
    return "smoke_runner_exception"


def _goal() -> str:
    return (
        "You are running a single-model release smoke for the local agent runtime. "
        "Use the configured primary LLM/model only; do not switch provider profiles. "
        "This is not a plan-only answer. Build a non-frontend Python module called Incident Operations Rules Engine. "
        f"Preserve the focus anchor {ANCHOR_FOCUS} and memory anchor {ANCHOR_MEMORY} in README, tests, and final summary. "
        "The implementation must be a real, testable backend/data-processing slice with multiple files. "
        "You must call the task tool at least twice: one child should review incident data/rule/SLA risks, "
        "and another child should review test/reporting/import-export coverage risks. "
        "Implement at least these modules or close equivalents: incident_models.py, incident_storage.py, "
        "incident_rules.py, incident_reporting.py, incident_import_export.py, and optionally incident_cli.py. "
        "Capabilities required: incident creation with validation, SQLite or JSON persistence with explicit db_path/storage injection, "
        "status transitions new/acknowledged/mitigated/resolved, severity/priority/SLA calculation, tag filtering/search, "
        "rule evaluation for escalation/ownership, audit log entries, summary reporting, and import/export round trip. "
        "Write at least two pytest files that cover validation failures, successful creation, persistence isolation with tmp_path, "
        "status transitions, filtering/search, rule escalation, reporting aggregation, and import/export. "
        "Update README.md with architecture, run commands, test commands, child-agent division of labor, known limits, "
        "and how both anchors were preserved. "
        "You must run python -m pytest -q and python -m py_compile for the incident modules, and verify the main files exist. "
        "Do not ask the user for confirmation; if a safe command/tool is available, do the work and record the real results. "
        "Final summary must list changed files, child task results, verification commands with actual outcomes, residual risks, "
        "and both anchors."
    )


def main() -> int:
    start = time.monotonic()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    root = REPO_ROOT / "runtime" / "smoke_runs" / f"single_model_incident_rules_{stamp}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    workspace = make_workspace(root)
    store = None
    events: list[dict[str, Any]] = []
    counter = [0]
    flow_steps: list[dict[str, Any]] = []
    output: dict[str, Any] = {
        "smokeRoot": str(root),
        "workspace": str(workspace),
    }

    def mark(step: str, **data: Any) -> None:
        flow_steps.append({"step": step, "elapsedSeconds": round(time.monotonic() - start, 2), **data})

    exit_code = 1
    try:
        provider_config, provider_public, probe_results = choose_single_provider_config()
        output.update({"provider": provider_public, "providerProbeResults": probe_results})
        server, store, events, memory_manager = build_runtime(root / "runtime.sqlite3", workspace, provider_config)
        mark("provider.probed", provider=provider_public)
        ws = rpc(server, "workspace.open", {"path": str(workspace)}, counter)["workspace"]
        mark("workspace.opened", workspaceId=ws["id"])
        store.update_workspace_focus(
            {
                "workspaceId": ws["id"],
                "focus": (
                    f"{ANCHOR_FOCUS}: implement a single-model incident rules engine with "
                    "rules, SLA escalation, persistence, import/export, reporting, tests, and audit."
                ),
            }
        )
        session = rpc(
            server,
            "session.create",
            {"workspaceId": ws["id"], "title": "Single-model incident rules smoke"},
            counter,
        )["session"]
        mark("session.created", sessionId=session["id"])
        memory_manager.remember(
            workspace_id=ws["id"],
            session_id=session["id"],
            content=(
                f"{ANCHOR_MEMORY}: use explicit tmp_path-friendly persistence, pytest, py_compile, "
                "and keep the incident rules engine module backend-only."
            ),
            kind=MemoryKind.SESSION,
            metadata={"category": "implementation_note", "pinned": True, "confidence": 0.95},
        )
        mark("memory.seeded", anchor=ANCHOR_MEMORY)
        goal = _goal()
        mark("message.send.start", goalChars=len(goal))
        response = rpc(server, "message.send", {"sessionId": session["id"], "content": goal, "newTask": True}, counter)
        task_id = response["task"]["id"]
        mark("message.send.returned", taskId=task_id, status=response["task"].get("status"))

        task = rpc(server, "task.get", {"taskId": task_id}, counter)["task"]
        all_task_ids = task_ids_for_session(store, session["id"])
        mark("session_tasks.loaded", count=len(all_task_ids), taskIds=all_task_ids)
        turns: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        budgets: list[dict[str, Any]] = []
        for observed_task_id in all_task_ids:
            turns.extend(rpc(server, "provider_turn.list", {"taskId": observed_task_id}, counter)["turns"])
            snapshots.extend(rpc(server, "context_snapshot.list", {"taskId": observed_task_id}, counter)["snapshots"])
            budgets.append(rpc(server, "context.budget", {"taskId": observed_task_id}, counter))
        all_compactions = [
            compaction
            for observed_budget in budgets
            for compaction in (observed_budget.get("compactions") or [])
        ]
        token_trend = [
            item
            for observed_budget in budgets
            for item in (observed_budget.get("tokenTrend") or [])
        ]
        commands_result = rpc(server, "command_log.list", {"sessionId": session["id"], "limit": 100}, counter)
        commands = commands_result.get("commandLogs") or commands_result.get("commands") or []
        collab_tasks = rpc(server, "collab.task.list", {"sessionId": session["id"], "limit": 100}, counter).get("tasks", [])
        report = aggregate_autonomy_reports(store, all_task_ids)
        proposals_by_kind = _proposal_kinds(store, all_task_ids)
        mark(
            "runtime_artifacts.loaded",
            providerTurns=len(turns),
            contextSnapshots=len(snapshots),
            compactions=len(all_compactions),
            commands=len(commands),
            collaborationTasks=len(collab_tasks),
            proposalKinds=proposals_by_kind,
        )

        direct_pytest = run([sys.executable, "-m", "pytest", "-q"], workspace, timeout=240)
        git_status = run(["git", "status", "--short"], workspace)
        git_diff_stat = run(["git", "diff", "--stat"], workspace)
        files = sorted(
            str(path.relative_to(workspace)).replace("\\", "/")
            for path in workspace.rglob("*")
            if path.is_file() and ".git" not in path.parts
        )
        direct_py_compile = _compile_modules(workspace, files)
        mark(
            "direct_validation.finished",
            pytestReturnCode=direct_pytest.returncode,
            pyCompileReturnCode=direct_py_compile["returnCode"],
        )

        expected_files = {
            "README.md",
            "incident_models.py",
            "incident_storage.py",
            "incident_rules.py",
            "incident_reporting.py",
            "incident_import_export.py",
        }
        python_modules = {
            name for name in files
            if name.startswith("incident_") and name.endswith(".py") and not name.startswith("test_")
        }
        test_files = {
            name for name in files
            if name.endswith(".py") and (name.startswith("test_") or "/test_" in name or "\\test_" in name)
        }
        command_text = "\n".join(str(command.get("command") or "") for command in commands).casefold()
        evidence = _completion_evidence(task)
        counts = evidence.get("counts") if isinstance(evidence.get("counts"), dict) else {}
        completion_advisor = evidence.get("completionAdvisor") if isinstance(evidence.get("completionAdvisor"), dict) else {}
        transport_counts = Counter(str(turn.get("response_transport") or "unknown") for turn in turns)
        cache_hit_turns = 0
        cached_tokens_total = 0
        for turn in turns:
            cache_usage = turn.get("cacheUsage") if isinstance(turn, dict) else None
            if not isinstance(cache_usage, dict):
                continue
            try:
                cached_tokens = max(0, int(cache_usage.get("cachedTokens") or 0))
            except (TypeError, ValueError):
                cached_tokens = 0
            if cached_tokens > 0:
                cache_hit_turns += 1
                cached_tokens_total += cached_tokens
        failures: list[str] = []
        if task.get("status") != "completed":
            failures.append("root task did not complete")
        if len(collab_tasks) < 2:
            failures.append("fewer than two child collaboration tasks were used")
        if len(turns) < 4:
            failures.append("provider turn count was too low for a long-flow smoke")
        if not snapshots:
            failures.append("no context snapshots were recorded")
        max_context_tokens = int(provider_public.get("maxContextTokens") or provider_config.get("maxContextTokens") or 0)
        if not all_compactions and max_context_tokens <= 12000:
            failures.append("no context compaction was recorded")
        if report.get("memoryRecallCount", 0) < 1:
            failures.append("no memory recall was recorded")
        if not expected_files.issubset(set(files)):
            failures.append("expected incident module files were not all produced")
        if len(python_modules) < 5:
            failures.append("incident implementation is not split into at least five modules")
        if len(test_files) < 2:
            failures.append("fewer than two pytest files were produced")
        if direct_pytest.returncode != 0:
            failures.append("direct pytest failed")
        if direct_py_compile["returnCode"] != 0:
            failures.append("direct py_compile failed")
        if "pytest" not in command_text:
            failures.append("agent command log did not include pytest")
        if "py_compile" not in command_text and "compileall" not in command_text:
            failures.append("agent command log did not include py_compile or compileall")
        if proposals_by_kind.get("routing_strategy", 0) > 0:
            failures.append("routing_strategy proposal should not be created in model-first flow")
        if proposals_by_kind.get("completion_decision", 0) < 1:
            failures.append("no completion_decision proposal record was created")
        if evidence.get("evidenceLevel") != "verified":
            failures.append("completion evidence was not verified")
        if int(counts.get("passedTestsRun") or 0) < 1:
            failures.append("completion evidence did not record passed tests")
        if not completion_advisor.get("proposalRecordId"):
            failures.append("completion advisor proposal id missing from evidence")
        if transport_counts.get("stream", 0) + transport_counts.get("fallback_non_stream", 0) < 1:
            failures.append("no provider turn recorded a streaming or fallback transport")
        summary = str(task.get("resultSummary") or "")
        if ANCHOR_FOCUS not in summary:
            failures.append("final summary lost focus anchor")
        if ANCHOR_MEMORY not in summary:
            failures.append("final summary lost memory anchor")

        failure_class = classify_failure(task, turns, events)
        output.update(
            {
                "durationSeconds": round(time.monotonic() - start, 2),
                "flowSteps": flow_steps,
                "coverageStatus": "passed" if not failures else "failed",
                "coverageFailures": failures,
                "failureClass": failure_class,
                "task": {
                    "id": task.get("id"),
                    "status": task.get("status"),
                    "errorCode": task.get("errorCode"),
                    "routing": task.get("routing"),
                    "summary": summary,
                    "completionEvidence": {
                        "evidenceLevel": evidence.get("evidenceLevel"),
                        "counts": counts,
                        "verificationRequirements": evidence.get("verificationRequirements"),
                        "completionAdvisor": completion_advisor,
                    },
                },
                "metrics": {
                    "providerTurns": len(turns),
                    "contextSnapshots": len(snapshots),
                    "compactions": len(all_compactions),
                    "memoryRecallCount": report.get("memoryRecallCount", 0),
                    "collaborationTasks": len(collab_tasks),
                    "commands": len(commands),
                    "proposalKinds": proposals_by_kind,
                    "responseTransport": dict(transport_counts),
                    "providerCache": {
                        "cacheHitTurns": cache_hit_turns,
                        "cachedTokensTotal": cached_tokens_total,
                    },
                    "changedStatusLines": len([line for line in git_status.stdout.splitlines() if line.strip()]),
                },
                "turns": [
                    {
                        "index": turn.get("turn_index"),
                        "status": turn.get("status"),
                        "decision": turn.get("turn_decision"),
                        "toolCalls": turn.get("response_tool_call_count"),
                        "transport": turn.get("response_transport"),
                        "cachedTokens": ((turn.get("cacheUsage") or {}).get("cachedTokens") if isinstance(turn.get("cacheUsage"), dict) else 0),
                        "phase": (turn.get("toolPolicyDecision") or {}).get("phase")
                        if isinstance(turn.get("toolPolicyDecision"), dict)
                        else None,
                        "error": turn.get("error_summary"),
                    }
                    for turn in turns
                ],
                "commands": [
                    {
                        "command": command.get("command"),
                        "status": command.get("status"),
                        "exitCode": command.get("exitCode") if command.get("exitCode") is not None else command.get("exit_code"),
                    }
                    for command in commands
                ],
                "collaborationTasks": [
                    {
                        "id": child.get("id"),
                        "status": child.get("status"),
                        "title": child.get("title"),
                        "agentType": (child.get("metadata") or {}).get("agentType")
                        if isinstance(child.get("metadata"), dict)
                        else None,
                    }
                    for child in collab_tasks
                ],
                "contextBudget": {
                    "tokenTrend": token_trend,
                    "compactions": [
                        {
                            "tokensBefore": item.get("tokens_before"),
                            "tokensAfter": item.get("tokens_after"),
                            "handoffSummary": item.get("handoffSummary"),
                            "summary": str(item.get("summary") or "")[:500],
                        }
                        for item in all_compactions
                    ],
                },
                "workspaceFiles": files,
                "gitStatus": git_status.stdout.strip(),
                "gitDiffStat": git_diff_stat.stdout.strip(),
                "directPytest": {
                    "returnCode": direct_pytest.returncode,
                    "stdout": direct_pytest.stdout[-3000:],
                    "stderr": direct_pytest.stderr[-3000:],
                },
                "directPyCompile": direct_py_compile,
                "events": {"counts": dict(Counter(str(event.get("type")) for event in events))},
            }
        )
        exit_code = 0 if not failures else 2
    except Exception as exc:  # noqa: BLE001
        mark("smoke.exception", error=str(exc)[:500])
        failure_class = _classify_runner_exception(exc)
        output.update(
            {
                "durationSeconds": round(time.monotonic() - start, 2),
                "flowSteps": flow_steps,
                "coverageStatus": "failed",
                "coverageFailures": [f"smoke runner exception: {exc}"],
                "failureClass": failure_class,
                "events": {"counts": dict(Counter(str(event.get("type")) for event in events))},
            }
        )
        exit_code = 1
    finally:
        if store is not None:
            store.close()

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
