from __future__ import annotations

from pathlib import Path

from local_agent_runtime.policy.tool_policy_resolver import ToolPolicyResolver
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import BUILTIN_TOOL_SCHEMAS


def _tools(*names: str) -> list[dict]:
    schemas = {schema["name"]: schema for schema in BUILTIN_TOOL_SCHEMAS}
    return [schemas[name] for name in names]


def test_root_synthesis_after_task_result_exposes_no_tools() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_root", "role": "root"},
        context={"routing": {"strategy": "plan_swarm"}},
        tool_results=[{"name": "task", "result": {"status": "completed"}}],
        registered_tools=_tools("task", "read_file", "write_file", "run_command"),
    )

    assert decision.phase == "synthesis"
    assert decision.allowed_tool_names == []
    assert set(decision.denied_tool_names) == {"task", "read_file", "write_file", "run_command"}
    assert decision.role_snapshot["runtimeRole"] == "root"


def test_reviewer_role_is_read_only() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_review", "role": "reviewer"},
        context={},
        tool_results=[],
        registered_tools=_tools("read_file", "git_diff", "write_file", "run_command"),
    )

    assert decision.phase == "review"
    assert set(decision.allowed_tool_names) == {"read_file", "git_diff"}
    assert set(decision.denied_tool_names) == {"write_file", "run_command"}


def test_child_worker_uses_agent_type_metadata_and_allowlist() -> None:
    resolver = ToolPolicyResolver()
    decision = resolver.resolve(
        task={"id": "task_child", "role": "worker"},
        context={
            "_child_worker": True,
            "agentType": "structure-agent",
            "runtimeRole": "worker",
            "_child_tool_allowlist": ["read_file", "git_status"],
        },
        tool_results=[],
        registered_tools=_tools("read_file", "git_status", "write_file", "task"),
    )

    assert decision.role_snapshot["runtimeRole"] == "worker"
    assert decision.role_snapshot["agentType"] == "structure-agent"
    assert set(decision.allowed_tool_names) == {"read_file", "git_status"}
    assert set(decision.denied_tool_names) == {"write_file", "task"}


def test_context_snapshot_persists_tool_policy_and_role_snapshot(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    try:
        workspace = store.upsert_workspace(str(tmp_path / "workspace"))
        session = store.create_session(workspace_id=workspace["id"], title="tool policy")
        task = store.create_task(session_id=session["id"], task_type="chat", goal="inspect", plan=[])
        snapshot = store.create_context_snapshot(
            session_id=session["id"],
            task_id=task["id"],
            tool_policy_decision={
                "phase": "synthesis",
                "allowedToolNames": [],
                "deniedToolNames": ["task"],
            },
            role_snapshot={
                "runtimeRole": "root",
                "agentType": "root",
            },
        )

        serialized = store._serialize_context_snapshot(snapshot)  # noqa: SLF001
        assert serialized["toolPolicyDecision"]["phase"] == "synthesis"
        assert serialized["toolPolicyDecision"]["deniedToolNames"] == ["task"]
        assert serialized["roleSnapshot"]["runtimeRole"] == "root"
    finally:
        store.close()
