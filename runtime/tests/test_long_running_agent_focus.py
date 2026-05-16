from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.memory import MemoryManager, MemoryRetriever, MemoryStore
from local_agent_runtime.memory.types import MemoryKind
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.router.types import ExecutionStrategy, RoutingDecision, Scenario
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.services.worker_runner import WorkerRunner
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools
from local_agent_runtime.tools.registry import ToolRegistry


ANCHOR_FOCUS = "ANCHOR_FOCUS_FEEDBACK_HUB"
ANCHOR_MEMORY = "ANCHOR_MEMORY_SQLITE_PYTEST_API"


class LongRunScriptedProvider:
    """Provider stub for a long ReAct run with compaction-aware summaries."""

    def __init__(self) -> None:
        self.work_calls: list[dict[str, Any]] = []
        self.summary_calls: list[str] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if prompt.startswith("Decide whether to compact"):
            return {
                "message": json.dumps(
                    {
                        "shouldCompact": True,
                        "reason": "long run contains bulky intermediate results",
                    }
                )
            }
        if prompt.startswith("Summarize the following conversation history"):
            self.summary_calls.append(prompt)
            summary = (
                f"Long-run summary preserved {ANCHOR_FOCUS} and {ANCHOR_MEMORY}. "
                "Completed phases: discovery, frontend entry, api contract, storage, "
                "validation, tests, docs. Continue toward the Feedback Hub goal."
            )
            return {"message": summary}

        self.work_calls.append({"prompt": prompt, "context": context})
        step = int(context.get("step") or len(self.work_calls))
        text = _visible_context_text(context)
        if step >= 2:
            assert ANCHOR_FOCUS in text
            assert ANCHOR_MEMORY in text

        if step == 1:
            assert ANCHOR_MEMORY in text
            return {
                "message": "Splitting the full-stack Feedback Hub into parallel probes.",
                "tool_calls": [
                    {
                        "id": "call_frontend_agent",
                        "name": "task",
                        "arguments": {
                            "prompt": (
                                "Inspect the frontend surface for the Feedback Hub. "
                                f"Keep {ANCHOR_FOCUS} in the result."
                            ),
                            "title": "Frontend feedback entry",
                            "agentType": "frontend-agent",
                            "budget": {"maxToolCalls": 3},
                        },
                    },
                    {
                        "id": "call_api_agent",
                        "name": "task",
                        "arguments": {
                            "prompt": (
                                "Inspect backend API and data requirements for Feedback Hub. "
                                f"Keep {ANCHOR_MEMORY} in the result."
                            ),
                            "title": "Backend API contract",
                            "agentType": "backend-agent",
                            "budget": {"maxToolCalls": 3},
                        },
                    },
                ],
            }
        if step in {2, 3, 4, 5}:
            phase = {
                2: "storage",
                3: "validation",
                4: "tests",
                5: "docs",
            }[step]
            return {
                "message": f"Recording {phase} phase.",
                "tool_calls": [
                    {
                        "id": f"call_record_{phase}",
                        "name": "record_progress",
                        "arguments": {
                            "phase": phase,
                            "anchor": ANCHOR_FOCUS,
                            "notes": f"{phase} keeps {ANCHOR_MEMORY} and full-stack scope.",
                        },
                    }
                ],
            }
        return {
            "final": (
                f"Completed sustained Feedback Hub plan with {ANCHOR_FOCUS}, "
                f"{ANCHOR_MEMORY}, frontend entry, API, SQLite storage, "
                "validation, tests, and docs still in focus."
            )
        }


class StaticLongRunRouter:
    last_advice = None

    def route(self, goal: str, context: dict[str, Any] | None = None) -> RoutingDecision:
        return RoutingDecision(
            scenario=Scenario.SWARM_TASK,
            strategy=ExecutionStrategy.PLAN_SWARM,
            confidence=0.99,
            max_steps=10,
            enable_planning=False,
            reasoning="test fixture: long full-stack run with child agents and ReAct continuation",
        )


def _visible_context_text(context: dict[str, Any]) -> str:
    messages = context.get("messages") or []
    return "\n".join(str(message.get("content") or "") for message in messages)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    envelope = {
        "jsonrpc": "2.0",
        "id": f"req_{len(runtime.events)}_{method}",
        "method": method,
        "params": params,
    }
    response = runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == envelope["id"]
    assert "error" not in response, response
    return response["result"]


def _make_runtime(tmp_path: Path, provider: LongRunScriptedProvider) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    store.update_config(
        {
            "config": {
                "provider": {
                    "mode": "mock",
                    "model": "long-run-scripted",
                    "maxContextTokens": 6000,
                },
                "autonomy": {
                    "activeProfileId": "long-run-test",
                    "profiles": [
                        {
                            "id": "long-run-test",
                            "name": "Long Run Test",
                            "maxSteps": 10,
                            "maxParallelSubtasks": 4,
                            "compactionThreshold": 900,
                        }
                    ],
                },
                "policy": {
                    "approvalMode": "none",
                    "maxTaskSteps": 10,
                },
                "worktree": {"autoBindWriteTasks": False},
            }
        }
    )
    config = store.get_config({})["config"]
    memory_store = MemoryStore(store)
    memory_manager = MemoryManager(
        store=memory_store,
        retriever=MemoryRetriever(memory_store),
    )
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    collaboration = CollaborationService(store, event_bus)
    child_runs: list[dict[str, Any]] = []

    def child_executor(context: Any) -> dict[str, Any]:
        agent_type = context.request.agent_type
        child_runs.append({"agentType": agent_type, "prompt": context.request.prompt})
        summary = (
            f"{agent_type} completed a focused slice for {ANCHOR_FOCUS}. "
            f"Memory convention retained: {ANCHOR_MEMORY}. "
            + ("parallel child evidence " * 80)
        )
        return {
            "summary": summary,
            "executionMode": "inline-long-run-test",
            "result": {"summary": summary, "agentType": agent_type},
        }

    runner = WorkerRunner(collaboration, executor=child_executor)
    subagent_service = SubagentService(store, collaboration, runner=runner)
    tools = build_builtin_tools(
        policy_guard=policy_guard,
        store=store,
        subagent_service=subagent_service,
        memory_manager=memory_manager,
    )
    progress_records: list[dict[str, Any]] = []

    def record_progress(params: dict[str, Any]) -> dict[str, Any]:
        phase = str(params.get("phase") or "")
        record = {
            "phase": phase,
            "anchor": params.get("anchor"),
            "notes": params.get("notes"),
        }
        progress_records.append(record)
        return {
            "status": "ok",
            "record": record,
            "evidence": (
                f"{phase} phase evidence for {ANCHOR_FOCUS} and {ANCHOR_MEMORY}. "
                + ("large intermediate implementation detail " * 140)
            ),
        }

    tools["record_progress"] = record_progress
    schemas = {
        "record_progress": {
            "name": "record_progress",
            "description": "Record durable phase progress for a long-running full-stack task.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "phase": {"type": "string"},
                    "anchor": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["phase"],
            },
            "safety": {
                "level": "safe",
                "requires_approval": False,
                "category": "task",
                "sandboxed": True,
            },
            "metadata": {"rate_limit": None},
        }
    }
    tool_registry = ToolRegistry(tools, schemas=schemas)
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
        meta_router=StaticLongRunRouter(),
        memory_manager=memory_manager,
        _skip_orphan_cleanup=True,
    )
    orchestrator._subagent_service = subagent_service  # noqa: SLF001
    orchestrator._worker_runner = runner  # noqa: SLF001
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(
        server=server,
        store=store,
        memory_manager=memory_manager,
        events=events,
        child_runs=child_runs,
        progress_records=progress_records,
    )


def test_long_running_fullstack_agent_preserves_focus_through_memory_and_compaction(
    tmp_path: Path,
) -> None:
    provider = LongRunScriptedProvider()
    runtime = _make_runtime(tmp_path, provider)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "README.md").write_text("# Feedback Hub\n", encoding="utf-8")

    workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["workspace"]
    runtime.store.update_workspace_focus(
        {
            "workspaceId": workspace["id"],
            "focus": (
                f"{ANCHOR_FOCUS}: build a sustained full-stack Feedback Hub with "
                "frontend entry, API, SQLite storage, validation, tests, and docs."
            ),
        }
    )
    session = _rpc(
        runtime,
        "session.create",
        {"workspaceId": workspace["id"], "title": "long-running full-stack focus"},
    )["session"]
    runtime.memory_manager.remember(
        workspace_id=workspace["id"],
        session_id=session["id"],
        content=(
            f"{ANCHOR_MEMORY}: use SQLite persistence, pytest coverage, and "
            "a /api/feedback contract for Feedback Hub."
        ),
        kind=MemoryKind.SESSION,
        metadata={"category": "implementation_note", "pinned": True, "confidence": 0.95},
    )

    task = _rpc(
        runtime,
        "message.send",
        {
            "sessionId": session["id"],
            "newTask": True,
            "content": (
                "Build the largest practical slice of a full-stack Feedback Hub: "
                "frontend feedback entry, backend API, SQLite persistence, input "
                "validation, error handling, tests, docs, and progress synthesis. "
                f"Keep {ANCHOR_FOCUS} and {ANCHOR_MEMORY} visible across the run."
            ),
        },
    )["task"]

    try:
        assert task["status"] == "completed"
        assert ANCHOR_FOCUS in task["resultSummary"]
        assert ANCHOR_MEMORY in task["resultSummary"]
        assert len(provider.work_calls) >= 6

        child_tasks = runtime.store.list_collaboration_tasks(
            {"sessionId": session["id"], "parentTaskId": task["id"]}
        )["tasks"]
        assert {run["agentType"] for run in runtime.child_runs} == {
            "frontend-agent",
            "backend-agent",
        }
        assert len(child_tasks) == 2
        assert {child["status"] for child in child_tasks} == {"completed"}

        assert [record["phase"] for record in runtime.progress_records] == [
            "storage",
            "validation",
            "tests",
            "docs",
        ]

        snapshots = runtime.store.list_context_snapshots(task["id"])
        turns = runtime.store.list_provider_turns(task["id"])
        assert len(snapshots) == len(turns) >= 6
        assert any(json.loads(s["memory_ids_json"] or "[]") for s in snapshots)

        budget = _rpc(runtime, "context.budget", {"taskId": task["id"]})
        compactions = budget["compactions"]
        assert compactions, "long run should force at least one context compaction"
        assert any(ANCHOR_FOCUS in str(c.get("summary") or "") for c in compactions)
        assert any(ANCHOR_MEMORY in str(c.get("summary") or "") for c in compactions)
        assert budget["tokenTrend"]

        event_types = [event["type"] for event in runtime.events]
        assert event_types.count("collab.task.completed") == 2
        assert "agent.decision.react_turn" in event_types
        assert runtime.store.get_autonomy_report({"taskId": task["id"]})["memoryRecall"]["count"] >= 1
    finally:
        runtime.store.close()
