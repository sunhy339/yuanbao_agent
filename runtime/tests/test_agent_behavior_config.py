from __future__ import annotations

from typing import Any

from local_agent_runtime.context.builder import ContextBuilder
from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


def _make_session(store: SQLiteStore, tmp_path: Any) -> str:
    workspace = store.upsert_workspace(str(tmp_path))
    session = store.create_session(workspace_id=workspace["id"], title="behavior config")
    return str(session["id"])


def _first_system_message(context: dict[str, Any]) -> str:
    for message in context["messages"]:
        if message.get("role") == "system":
            return str(message.get("content") or "")
    return ""


def test_default_config_includes_agent_behavior_profiles(tmp_path: Any) -> None:
    store = SQLiteStore(str(tmp_path / "test.sqlite3"))

    config = store.get_config({})["config"]

    assert config["provider"]["maxContextTokens"] == 256000
    assert config["autonomy"]["activeProfileId"] == "balanced"
    assert [profile["id"] for profile in config["autonomy"]["profiles"]] == [
        "locked_down",
        "conservative",
        "balanced",
        "autonomous",
    ]
    assert config["agentSoul"]["activeProfileId"] == "default"
    assert config["agentSoul"]["profiles"][0]["enabled"] is True


def test_agent_soul_prompt_layers_are_injected_and_audited(tmp_path: Any) -> None:
    store = SQLiteStore(str(tmp_path / "test.sqlite3"))
    session_id = _make_session(store, tmp_path)
    store.update_config({
        "config": {
            "agentSoul": {
                "activeProfileId": "architect",
                "workspaceInstructions": "Prefer migration notes in the final summary.",
                "profiles": [
                    {
                        "id": "architect",
                        "name": "Architect",
                        "enabled": True,
                        "identity": "You are a careful systems architect.",
                        "communicationStyle": "Concise and direct.",
                        "reasoningStyle": "Explain tradeoffs before risky edits.",
                        "collaborationStyle": "Surface assumptions early.",
                        "principles": ["Keep runtime safety gates authoritative."],
                        "domainPreferences": ["local agent runtime"],
                        "customSystemPrompt": "Never skip task snapshots.",
                    }
                ],
            }
        }
    })

    context = ContextBuilder(store=store).build(session_id=session_id, goal="inspect routing", role="planner")

    system_message = _first_system_message(context)
    assert "planner agent" in system_message
    assert "Agent soul:" in system_message
    assert "You are a careful systems architect." in system_message
    assert "Never skip task snapshots." in system_message
    assert "Workspace instructions:" in system_message
    assert "Prefer migration notes in the final summary." in system_message
    assert "Safety boundaries:" in system_message

    metadata = context["snapshot_metadata"]
    assert metadata["agent_soul_profile"]["id"] == "architect"
    layer_names = [layer["name"] for layer in metadata["prompt_layers"]]
    assert layer_names == [
        "role",
        "agent_soul",
        "workspace_instructions",
        "runtime_safety",
    ]


def test_orchestrator_captures_behavior_snapshot_and_limits(tmp_path: Any) -> None:
    store = SQLiteStore(str(tmp_path / "test.sqlite3"))
    session_id = _make_session(store, tmp_path)
    store.update_config({
        "config": {
            "autonomy": {"activeProfileId": "conservative"},
            "agentSoul": {
                "workspaceInstructions": "Keep reviews auditable.",
            },
        }
    })
    context = ContextBuilder(store=store).build(session_id=session_id, goal="prepare a task")

    orchestrator = Orchestrator(
        store=store,
        event_bus=EventBus(),
        tool_registry=ToolRegistry(),
        provider=None,
    )
    snapshot = orchestrator._runtime_profile_snapshot(context)  # noqa: SLF001

    assert snapshot["autonomyProfile"]["id"] == "conservative"
    assert snapshot["agentSoulProfile"]["id"] == "default"
    assert snapshot["promptLayers"]
    context["routing"] = {"profile_snapshot": snapshot}
    assert orchestrator._max_task_steps(context) == 10  # noqa: SLF001
    assert orchestrator._max_parallel_subtasks(context) == 2  # noqa: SLF001
