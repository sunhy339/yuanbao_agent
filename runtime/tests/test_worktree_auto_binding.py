from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.router.meta_router import MetaRouter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


class ScriptedProvider:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if not self._responses:
            raise AssertionError("Provider called more times than scripted")
        return self._responses.pop(0)


class FakeWorktreeService:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store
        self.created: list[dict[str, Any]] = []

    def create_for_task(self, params: dict[str, Any]) -> dict[str, Any]:
        self.created.append(dict(params))
        record = self._store.create_worktree(params)
        worktree_path = params["worktreePath"]
        from pathlib import Path

        Path(worktree_path).mkdir(parents=True, exist_ok=True)
        return self._store.update_worktree({
            "worktreeId": record["worktree"]["id"],
            "status": "active",
        })


class FailingWorktreeService(FakeWorktreeService):
    def create_for_task(self, params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("simulated worktree failure")


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    envelope = {
        "jsonrpc": "2.0",
        "id": f"req_{len(runtime.events)}",
        "method": method,
        "params": params,
    }
    response = runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))
    assert response["jsonrpc"] == "2.0"
    return response


def _make_runtime(tmp_path: Any, provider: Any, tools: dict[str, Any]) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    worktree_service = FakeWorktreeService(store)
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=ToolRegistry(tools),
        provider=provider,
        meta_router=MetaRouter(provider=None),
        worktree_service=worktree_service,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, events=events, worktree_service=worktree_service)


def test_code_edit_task_auto_binds_worktree_and_routes_workspace_tools(tmp_path: Any) -> None:
    seen_write_args: list[dict[str, Any]] = []

    def write_file(params: dict[str, Any]) -> dict[str, Any]:
        seen_write_args.append(dict(params))
        return {"status": "written", "path": params["path"], "bytesWritten": len(params.get("content", ""))}

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    provider = ScriptedProvider([
        {
            "tool_calls": [
                {
                    "id": "call_write",
                    "name": "write_file",
                    "arguments": {
                        "workspaceRoot": str(workspace_root),
                        "path": "generated.txt",
                        "content": "hello",
                    },
                },
            ],
        },
        {"final": "Done."},
    ])
    runtime = _make_runtime(tmp_path, provider, {"write_file": write_file})

    workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["result"]["workspace"]
    session = _rpc(runtime, "session.create", {
        "workspaceId": workspace["id"],
        "title": "Worktree",
        "repository": {"branch": "main", "worktree": True},
    })["result"]["session"]

    result = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "fix code by writing a generated file"})
    task = result["result"]["task"]
    worktree = runtime.store.get_worktree_by_task({"taskId": task["id"]})["worktree"]

    assert worktree is not None
    assert task["routing"]["activeWorktree"]["id"] == worktree["id"]
    assert runtime.worktree_service.created[0]["taskId"] == task["id"]
    assert worktree["status"] == "active"
    assert seen_write_args
    assert seen_write_args[0]["workspaceRoot"] == worktree["worktreePath"]
    assert seen_write_args[0]["originalWorkspaceRoot"] == str(workspace_root)
    assert seen_write_args[0]["activeWorktreeId"] == worktree["id"]

    bound_events = [event for event in runtime.events if event["type"] == "task.worktree.bound"]
    assert bound_events

    persisted_task = runtime.store.get_task({"taskId": task["id"]})["task"]
    assert persisted_task["routing"]["activeWorktree"]["worktreePath"] == worktree["worktreePath"]

    snapshots = runtime.store.list_context_snapshots(task["id"])
    assert snapshots
    snapshot = runtime.store._serialize_context_snapshot(snapshots[0])
    assert "activeWorktree" not in snapshot or snapshot["activeWorktree"].get("path") in {None, worktree["worktreePath"]}


def test_read_only_readme_summary_does_not_require_worktree(tmp_path: Any) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "README.md").write_text("# Demo\n\nRead-only fixture.\n", encoding="utf-8")
    runtime = _make_runtime(tmp_path, ScriptedProvider([{"final": "README says this is a demo."}]), {})
    runtime.server._orchestrator._worktree_service = FailingWorktreeService(runtime.store)

    workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["result"]["workspace"]
    session = _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "Read-only"})["result"]["session"]

    response = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "Read README.md and summarize it"})

    assert "error" not in response
    task = response["result"]["task"]
    assert task["status"] == "completed"
    assert task["routing"]["scenario"] == "free_form"
    assert task["routing"]["intentHints"]["ruleCandidate"]["scenario"] == "code_search"
    assert task["routing"].get("worktreeBindingRequired") is False
    assert runtime.worktree_service.created == []
    assert not [event for event in runtime.events if event["type"] == "task.worktree.bind_failed"]


def test_read_only_launch_worktree_session_does_not_fail_before_provider(tmp_path: Any) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "README.md").write_text("# Demo\n", encoding="utf-8")
    provider = ScriptedProvider([{"final": "README.md 存在。"}])
    runtime = _make_runtime(tmp_path, provider, {})
    runtime.server._orchestrator._worktree_service = FailingWorktreeService(runtime.store)

    workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["result"]["workspace"]
    session = _rpc(runtime, "session.create", {
        "workspaceId": workspace["id"],
        "title": "Read-only launch worktree",
        "repository": {"branch": "main", "worktree": True},
    })["result"]["session"]

    response = _rpc(
        runtime,
        "message.send",
        {"sessionId": session["id"], "content": "只读检查：确认当前工作区 README.md 是否存在，然后用一句中文回答，不要修改文件。"},
    )

    assert "error" not in response
    task = response["result"]["task"]
    assert task["status"] == "completed"
    assert task["routing"]["preferredWorktree"] is True
    assert task["routing"].get("worktreeBindingRequired") is False
    assert runtime.worktree_service.created == []
    assert not [event for event in runtime.events if event["type"] == "task.worktree.bind_failed"]
    assert runtime.store.list_provider_turns(task["id"])
    assert provider._responses == []


def test_model_tool_swarm_does_not_auto_bind_worktree(tmp_path: Any) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    runtime = _make_runtime(tmp_path, ScriptedProvider([{"final": "Swarm analysis done."}]), {})
    runtime.store.update_config({"config": {"worktree": {"autoBindWriteTasks": True}}})

    workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["result"]["workspace"]
    session = _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "Model tools swarm"})["result"]["session"]

    response = _rpc(
        runtime,
        "message.send",
        {
            "sessionId": session["id"],
            "content": "\u8bf7\u7528\u591a\u4e2a agent \u53ea\u8bfb\u5206\u6790\u9879\u76ee\u3002\u4e0d\u8981\u4fee\u6539\u6587\u4ef6\u3002",
        },
    )

    assert "error" not in response
    task = response["result"]["task"]
    assert task["routing"]["scenario"] == "swarm_task"
    assert task["routing"]["orchestrationMode"] == "model_tools"
    assert task["routing"].get("worktreeBindingRequired") is False
    assert runtime.worktree_service.created == []
    assert runtime.store.get_worktree_by_task({"taskId": task["id"]})["worktree"] is None


def test_queued_code_edit_persists_active_worktree_before_execution(tmp_path: Any) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    runtime = _make_runtime(tmp_path, ScriptedProvider([]), {})

    workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["result"]["workspace"]
    session = _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "Queued worktree"})["result"]["session"]
    runtime.store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="already running",
        plan=[],
        status="running",
    )

    result = _rpc(
        runtime,
        "message.send",
        {"sessionId": session["id"], "content": "fix code later", "mode": "queued"},
    )
    queued_task = result["result"]["task"]
    assert queued_task["status"] == "queued"
    assert runtime.store.get_worktree_by_task({"taskId": queued_task["id"]})["worktree"] is None

    persisted_task = runtime.store.get_task({"taskId": queued_task["id"]})["task"]
    assert persisted_task["routing"]["scenario"] == "free_form"
    assert persisted_task["routing"]["intentHints"]["ruleCandidate"]["scenario"] == "code_edit"
    assert "activeWorktree" not in persisted_task["routing"]


def test_session_launch_repository_controls_task_worktree_base_ref(tmp_path: Any) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    runtime = _make_runtime(tmp_path, ScriptedProvider([{"final": "Done."}]), {})

    workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["result"]["workspace"]
    session = _rpc(runtime, "session.create", {
        "workspaceId": workspace["id"],
        "title": "Launch",
        "workDir": str(workspace_root),
        "repository": {"branch": "feature/parity", "worktree": True},
    })["result"]["session"]

    assert session["launch"]["workDir"] == str(workspace_root)
    assert session["repository"]["branch"] == "feature/parity"

    result = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "fix code by editing files"})
    task = result["result"]["task"]
    worktree = runtime.store.get_worktree_by_task({"taskId": task["id"]})["worktree"]

    assert worktree is not None
    assert runtime.worktree_service.created[0]["baseRef"] == "feature/parity"
    assert task["routing"]["repository"] == {"branch": "feature/parity", "worktree": True}
    assert task["routing"]["activeWorktree"]["id"] == worktree["id"]


def test_session_launch_repository_can_use_current_worktree(tmp_path: Any) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    runtime = _make_runtime(tmp_path, ScriptedProvider([{"final": "Done."}]), {})

    workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["result"]["workspace"]
    session = _rpc(runtime, "session.create", {
        "workspaceId": workspace["id"],
        "title": "Current tree",
        "workDir": str(workspace_root),
        "repository": {"branch": "master", "worktree": False},
    })["result"]["session"]

    result = _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "fix code by editing files"})
    task = result["result"]["task"]

    assert runtime.worktree_service.created == []
    assert task["routing"]["disableWorktreeBinding"] is True
    assert task["routing"]["repository"] == {"branch": "master", "worktree": False}


def test_context_preview_persists_into_session_metadata(tmp_path: Any) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    def echo(params: dict[str, Any]) -> dict[str, Any]:
      return {"echo": params.get("text", "")}

    runtime = _make_runtime(
        tmp_path,
        ScriptedProvider([
            {
                "message": "Inspecting workspace.",
                "tool_calls": [
                    {
                        "id": "call_echo",
                        "name": "echo",
                        "arguments": {"text": "hello"},
                    },
                ],
            },
            {"final": "Done."},
        ]),
        {"echo": echo},
    )

    workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["result"]["workspace"]
    session = _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "Persisted context"})["result"]["session"]

    _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "inspect the workspace"})

    persisted = runtime.store.require_session(session["id"])
    metadata = persisted.get("metadata") or {}
    preview = metadata.get("contextPreview") if isinstance(metadata, dict) else None

    assert isinstance(preview, dict)
    assert preview["workspaceRoot"]
    assert str(preview["workspaceRoot"]).startswith(str(workspace_root))
    assert preview["toolCount"] >= 1
    assert isinstance(preview.get("budgetStats"), dict)
    assert preview["budgetStats"]["maxContextTokens"] is not None
    assert preview["budgetStats"]["updatedAt"] is not None
    assert preview["budgetStats"]["estimated"] is False
    assert isinstance(preview.get("taskFocus"), dict)
    assert preview["taskFocus"]


def test_write_child_task_auto_binds_own_worktree_when_parent_does_not_pass_one(tmp_path: Any) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    runtime = _make_runtime(tmp_path, ScriptedProvider([{"final": "Child done."}]), {})
    runtime.store.update_config({"config": {"worktree": {"autoBindWriteTasks": True}}})

    workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["result"]["workspace"]
    session = _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "Child worktree"})["result"]["session"]

    result = runtime.server._orchestrator.run_child_task({
        "sessionId": session["id"],
        "prompt": "Edit README in an isolated child worktree.",
        "agentType": "worker",
        "parentRuntimeTaskId": "parent-task",
        "childToolAllowlist": ["read_file", "apply_patch"],
    })

    task = result["task"]
    worktree = runtime.store.get_worktree_by_task({"taskId": task["id"]})["worktree"]

    assert worktree is not None
    assert runtime.worktree_service.created[0]["taskId"] == task["id"]
    assert task["routing"]["parentRuntimeTaskId"] == "parent-task"
    assert task["routing"]["activeWorktree"]["id"] == worktree["id"]
    assert task["routing"]["activeWorktree"]["worktreePath"] == worktree["worktreePath"]

    persisted_task = runtime.store.get_task({"taskId": task["id"]})["task"]
    assert persisted_task["routing"]["activeWorktree"]["id"] == worktree["id"]
    bound_events = [event for event in runtime.events if event["type"] == "task.worktree.bound"]
    assert bound_events


def test_worktree_segment_sanitizer_does_not_escape_path_root(tmp_path: Any) -> None:
    runtime = _make_runtime(tmp_path, ScriptedProvider([]), {})
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    worktree_root = tmp_path / "worktrees"

    path = runtime.server._orchestrator._worktree_path_for_task(
        workspace_root,
        "../task/../../escape",
        {"pathRoot": str(worktree_root)},
    )
    safe_segment = runtime.server._orchestrator._safe_worktree_segment("agent/../unsafe branch")

    assert path == worktree_root.resolve() / "task" / "escape"
    assert Path(path).is_relative_to(worktree_root.resolve())
    assert safe_segment == "agent/unsafe-branch"


def test_write_oriented_task_fails_when_required_worktree_binding_cannot_be_created(tmp_path: Any) -> None:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    worktree_service = FailingWorktreeService(store)
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=ToolRegistry({
            "write_file": lambda params: {
                "status": "written",
                "path": params["path"],
                "bytesWritten": len(params.get("content", "")),
            },
        }),
        provider=ScriptedProvider([
            {
                "tool_calls": [
                    {
                        "id": "call_write",
                        "name": "write_file",
                        "arguments": {
                            "workspaceRoot": str(tmp_path / "workspace"),
                            "path": "generated.txt",
                            "content": "hello",
                        },
                    }
                ]
            }
        ]),
        meta_router=MetaRouter(provider=None),
        worktree_service=worktree_service,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = server.handle_line(json.dumps({
        "jsonrpc": "2.0",
        "id": "open",
        "method": "workspace.open",
        "params": {"path": str(workspace_root)},
    }, ensure_ascii=False))["result"]["workspace"]
    session = server.handle_line(json.dumps({
        "jsonrpc": "2.0",
        "id": "session",
        "method": "session.create",
        "params": {
            "workspaceId": workspace["id"],
            "title": "Needs worktree",
            "repository": {"branch": "main", "worktree": True},
        },
    }, ensure_ascii=False))["result"]["session"]

    response = server.handle_line(json.dumps({
        "jsonrpc": "2.0",
        "id": "send",
        "method": "message.send",
        "params": {"sessionId": session["id"], "content": "fix code by editing files"},
    }, ensure_ascii=False))

    assert "error" not in response
    assert response["result"]["task"]["status"] == "failed"
    assert response["result"]["task"]["errorCode"] == "WORKTREE_BINDING_FAILED"
    assert response["result"]["task"]["structuredResult"]["failureKind"] == "worktree_binding_failed"
    assert "worktree binding failed" in response["result"]["task"]["resultSummary"]
