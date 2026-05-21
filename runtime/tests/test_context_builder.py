from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_agent_runtime.context.builder import ContextBuilder
from local_agent_runtime.store.sqlite_store import SQLiteStore


def _make_builder_context(
    store: SQLiteStore,
    workspace_root: Path,
    *,
    title: str = "Context tests",
    goal: str = "Inspect the workspace",
) -> dict[str, Any]:
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title=title)
    return ContextBuilder(store).build(session_id=session["id"], goal=goal)


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    db_path = tmp_path / "runtime.sqlite3"
    runtime_store = SQLiteStore(str(db_path))
    try:
        yield runtime_store
    finally:
        runtime_store.close()


def _message_text(context: dict[str, Any]) -> str:
    return "\n".join(str(message["content"]) for message in context["messages"])


def test_context_builder_injects_messages_tools_and_safety_prompt(store: SQLiteStore, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "README.md").write_text("# Demo\n", encoding="utf-8")

    context = _make_builder_context(store, workspace_root, goal="Update README.md")

    assert context["workspace_root"] == str(workspace_root)
    assert context["goal"] == "Update README.md"
    assert context["project_focus"] is None
    assert context["project_memory"] is None
    assert [message["role"] for message in context["messages"]] == ["system", "user", "user"]
    assert {tool["name"] for tool in context["tools"]} >= {
        "list_dir",
        "search_files",
        "read_file",
        "run_command",
        "apply_patch",
        "write_file",
        "git_status",
        "git_diff",
    }

    text = _message_text(context)
    assert "write files only through apply_patch or write_file" in text
    assert "use write_file for new/full files" in text
    assert "run commands only through run_command" in text
    assert "stay within the workspace root" in text
    assert "do not bypass the provided tools" in text
    assert "README.md" in text
    assert context["budgetStats"]["maxContextTokens"] > 0
    assert context["budgetStats"]["estimatedTokens"] <= context["budgetStats"]["maxContextTokens"]
    assert "top-level entries:" not in text
    assert "Workspace root is accessible and non-empty." in text


def test_context_builder_summarizes_recent_history(store: SQLiteStore, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="History")
    store._conn.execute(  # noqa: SLF001
        "UPDATE sessions SET summary = ? WHERE id = ?",
        ("The user prefers focused pytest runs.", session["id"]),
    )
    old_task = store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Old task goal",
        plan=[{"id": "old", "title": "Old step", "status": "completed"}],
    )
    store.update_task(task_id=old_task["id"], status="completed", result_summary="Old task completed.")
    new_task = store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Recent task goal",
        plan=[{"id": "new", "title": "New step", "status": "running"}],
    )
    store.update_task(task_id=new_task["id"], status="failed", result_summary="Recent task failed.")

    context = ContextBuilder(store).build(session_id=session["id"], goal="Continue the work", lightweight=False)

    text = _message_text(context)
    assert "The user prefers focused pytest runs." in text
    assert "Recent task goal" in text
    assert "Recent task failed." in text
    assert "task failed" in text


def test_context_builder_includes_recent_chat_messages(store: SQLiteStore, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Chat memory")
    store.create_message(session_id=session["id"], role="user", content="Keep the UI compact.")
    store.create_message(session_id=session["id"], role="assistant", content="I will preserve compact layout.")

    context = ContextBuilder(store).build(session_id=session["id"], goal="Continue the interface work", lightweight=False)

    text = _message_text(context)
    assert "Recent conversation:" in text
    assert "User: Keep the UI compact." in text
    assert "Assistant: I will preserve compact layout." in text
    assert text.index("Recent conversation:") < text.index("Current user request:\nContinue the interface work")


def test_context_builder_preserves_large_recent_conversation_for_cache_prefix(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Large chat memory")
    for index in range(96):
        store.create_message(
            session_id=session["id"],
            role="user" if index % 2 == 0 else "assistant",
            content=(
                f"historical turn {index}\n"
                f"line one keeps formatting {index}\n"
                + (f"detail-{index} " * 60)
            ),
        )
    store.update_config({"config": {"provider": {"maxContextTokens": 80000}}})

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Continue using the full session context",
        lightweight=False,
    )

    text = _message_text(context)
    assert "historical turn 0" in text
    assert "historical turn 95" in text
    assert "line one keeps formatting 20" in text
    assert context["budgetStats"]["promptCache"]["targetFillRatio"] == 0.92
    assert context["budgetStats"]["promptCache"]["maxStableContextTokens"] == 80000
    assert context["budgetStats"]["stablePrefixTokens"] > 2000


def test_context_builder_expands_cache_friendly_history_and_stable_prefix(
    store: SQLiteStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    for index in range(12):
        (workspace_root / f"module_{index:02d}.py").write_text(
            f"# stable module {index}\nVALUE_{index} = '{'x' * 300}'\n",
            encoding="utf-8",
        )
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Cache policy")
    for index in range(20):
        store.create_message(
            session_id=session["id"],
            role="user" if index % 2 == 0 else "assistant",
            content=f"historical message {index} {'detail ' * 80}",
        )
    monkeypatch.setattr(
        ContextBuilder,
        "_git_summary",
        lambda self, workspace_root: "Git status summary:\n- working tree appears clean.",
    )
    store.update_config(
        {
            "config": {
                "provider": {
                    "maxContextTokens": 12000,
                    "promptCache": {
                        "enabled": True,
                        "targetFillRatio": 0.6,
                        "maxStableContextTokens": 8000,
                        "recentMessages": 20,
                    },
                }
            }
        }
    )

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Continue implementation",
        lightweight=False,
    )

    text = _message_text(context)
    assert "historical message 0" in text
    assert "historical message 19" in text
    assert "Stable workspace context pack:" in text
    assert "module_00.py" in text
    assert context["messages"][-1]["content"] == "Current user request:\nContinue implementation"
    assert context["budgetStats"]["promptCache"]["enabled"] is True
    assert context["budgetStats"]["stablePrefixTokens"] > 0
    assert context["budgetStats"]["estimatedTokens"] <= 12000


def test_context_builder_prompt_cache_policy_can_be_disabled(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "module.py").write_text("print('stable')\n", encoding="utf-8")
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Cache disabled")
    store.update_config({"config": {"provider": {"promptCache": {"enabled": False}}}})

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Continue implementation",
        lightweight=False,
    )

    text = _message_text(context)
    assert "Stable workspace context pack:" not in text
    assert context["budgetStats"]["promptCache"]["enabled"] is False


def test_context_builder_summarizes_task_run_artifacts(store: SQLiteStore, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Task run artifacts")
    task = store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="Build bead art generator",
        plan=[{"id": "verify", "title": "Verify CLI", "status": "completed"}],
        acceptance_criteria=["CLI writes an output file"],
        out_of_scope=["No GUI"],
    )
    store.update_task(
        task_id=task["id"],
        status="completed",
        changed_files=[
            {
                "path": "tools/bead_art_generator.py",
                "status": "added",
                "additions": 148,
                "deletions": 0,
            }
        ],
        commands=[
            {
                "command": "python tools/bead_art_generator.py --help",
                "status": "completed",
                "exitCode": 0,
            }
        ],
        verification=[
            {
                "command": "python tools/bead_art_generator.py --help",
                "status": "passed",
                "summary": "Help text prints.",
            }
        ],
        summary="Created a CLI generator and verified help output.",
        result_summary="Created a CLI generator and verified help output.",
    )

    context = ContextBuilder(store).build(session_id=session["id"], goal="Continue bead tool work", lightweight=False)

    text = _message_text(context)
    assert "Task artifacts:" in text
    assert "tools/bead_art_generator.py" in text
    assert "python tools/bead_art_generator.py --help" in text
    assert "Help text prints." in text
    assert "CLI writes an output file" in text
    assert "No GUI" in text


def test_context_builder_injects_workspace_project_memory_across_sessions(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    store.update_workspace_summary(
        workspace["id"],
        "Project memory:\n- completed: chose SQLite for local project memory.",
    )
    session = store.create_session(workspace_id=workspace["id"], title="Next session")

    context = ContextBuilder(store).build(session_id=session["id"], goal="Continue the product iteration", lightweight=False)

    text = _message_text(context)
    assert "Project memory:" in text
    assert "chose SQLite for local project memory" in text


def test_context_builder_injects_workspace_project_focus_across_sessions(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    store.update_workspace_focus(
        {
            "workspaceId": workspace["id"],
            "focus": "Build a coding agent that can sustain large product iterations.",
        }
    )
    session = store.create_session(workspace_id=workspace["id"], title="Focused session")

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Continue implementation",
        lightweight=False,
    )

    text = _message_text(context)
    assert "Project focus:" in text
    assert "sustain large product iterations" in text
    assert context["project_focus"] == "Build a coding agent that can sustain large product iterations."


def test_context_builder_injects_canonical_memory_files(store: SQLiteStore, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "YUANBAO.md").write_text(
        "Project rule: use python -m pytest for verification.\n",
        encoding="utf-8",
    )
    (workspace_root / "MEMORY.local.md").write_text(
        "Local note: reviewer tasks stay read-only.\n",
        encoding="utf-8",
    )

    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Canonical memory")

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Continue implementation",
        lightweight=False,
    )

    text = _message_text(context)
    assert "Canonical memory:" in text
    assert "--- YUANBAO.md ---" in text
    assert "use python -m pytest for verification" in text
    assert "--- MEMORY.local.md ---" in text
    assert "reviewer tasks stay read-only" in text


def test_context_builder_orders_stable_memory_before_dynamic_history_and_repo_noise(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "README.md").write_text("# Stable workspace\n", encoding="utf-8")
    (workspace_root / "YUANBAO.md").write_text(
        "Canonical rule: use python -m pytest for verification.\n",
        encoding="utf-8",
    )

    workspace = store.upsert_workspace(str(workspace_root))
    store.update_workspace_summary(
        workspace["id"],
        "Project memory:\n- prefer focused pytest runs for backend changes.",
    )
    session = store.create_session(workspace_id=workspace["id"], title="Ordering")
    store.create_message(session_id=session["id"], role="user", content="Keep the history concise.")
    store.create_message(session_id=session["id"], role="assistant", content="I will keep the history concise.")

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Continue implementation",
        lightweight=False,
    )

    text = _message_text(context)
    assert "Canonical memory:" in text
    assert "Project memory:" in text
    assert "Recent conversation:" in text
    assert "--- README.md ---" in text
    assert "Git status summary:" in text
    assert text.index("Canonical memory:") < text.index("Project memory:")
    assert text.index("Project memory:") < text.index("--- README.md ---")
    assert text.index("--- README.md ---") < text.index("Recent conversation:")
    assert text.index("--- README.md ---") < text.index("Git status summary:")


def test_context_builder_moves_child_role_after_stable_workspace_prefix(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "README.md").write_text("# Stable workspace\n", encoding="utf-8")
    (workspace_root / "YUANBAO.md").write_text(
        "Canonical rule: runtime guards stay authoritative.\n",
        encoding="utf-8",
    )
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Child cache")

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Implement the worker slice",
        lightweight=False,
        role="worker",
    )

    messages = context["messages"]
    system_text = str(messages[0]["content"])
    all_text = _message_text(context)
    assert "worker agent" not in system_text
    assert "worker agent" in all_text
    assert "--- README.md ---" in all_text
    assert all_text.index("--- README.md ---") < all_text.index("You are a worker agent")
    assert all_text.index("You are a worker agent") < all_text.index("Current user request:")
    assert context["budgetStats"]["stablePrefixTokens"] > 0


def test_context_builder_keeps_workspace_project_focus_under_tight_budget(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    focus = "DO NOT LOSE THIS ATTENTION ANCHOR."
    store.update_workspace_focus({"workspaceId": workspace["id"], "focus": focus})
    store.update_workspace_summary(
        workspace["id"],
        "Project memory:\n" + ("historical implementation detail " * 80),
    )
    session = store.create_session(workspace_id=workspace["id"], title="Focused tight budget")
    store._conn.execute(  # noqa: SLF001
        "UPDATE sessions SET summary = ? WHERE id = ?",
        ("old session detail " * 120, session["id"]),
    )
    store.update_config({"config": {"provider": {"maxContextTokens": 210}}})

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Continue implementation",
        lightweight=False,
    )

    text = _message_text(context)
    assert "Project focus:" in text
    assert focus in text
    assert "Continue implementation" in text
    assert context["budgetStats"]["estimatedTokens"] <= 210
    assert context["budgetStats"]["droppedSections"] or context["budgetStats"]["trimmedSections"]
    assert "project_focus" not in context["budgetStats"]["droppedSections"]
    assert "project_focus" not in context["budgetStats"]["trimmedSections"]


def test_context_builder_reserves_tool_schema_tokens_when_trimming_messages(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Tool budget")
    store._conn.execute(  # noqa: SLF001
        "UPDATE sessions SET summary = ? WHERE id = ?",
        ("large historical detail " * 180, session["id"]),
    )
    store.update_config({"config": {"provider": {"maxContextTokens": 1600}}})
    tool_schemas = [
        {
            "name": "large_tool",
            "description": "Large tool description. " * 120,
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query. " * 80,
                    },
                },
                "required": ["query"],
            },
        }
    ]

    context = ContextBuilder(store, tool_schemas=tool_schemas).build(
        session_id=session["id"],
        goal="Continue implementation",
        lightweight=False,
    )

    stats = context["budgetStats"]
    assert stats["toolSchemaTokens"] > 0
    assert stats["messageTokens"] + stats["toolSchemaTokens"] <= 1600
    assert stats["estimatedInputTokens"] <= 1600
    assert stats["droppedSections"] or stats["trimmedSections"]


def test_context_builder_trims_low_priority_history_large_results_and_diff(
    store: SQLiteStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Budget")
    store.update_config({"config": {"provider": {"maxContextTokens": 280}}})
    old_task = store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="ancient history should be dropped",
        plan=[],
    )
    store.update_task(
        task_id=old_task["id"],
        status="completed",
        result_summary="ancient-result " * 300,
    )
    recent_task = store.create_task(
        session_id=session["id"],
        task_type="edit",
        goal="recent history should survive",
        plan=[],
    )
    store.update_task(
        task_id=recent_task["id"],
        status="completed",
        result_summary="recent-result " * 200,
    )
    store.create_patch(
        task_id=recent_task["id"],
        workspace_id=workspace["id"],
        summary="Large diff patch",
        diff_text="\n".join(f"+line {index} {'x' * 80}" for index in range(200)),
        files_changed=1,
    )
    monkeypatch.setattr(
        ContextBuilder,
        "_git_summary",
        lambda self, workspace_root: "Git status summary:\n- working tree appears clean.",
    )

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Implement the feature",
        lightweight=False,
    )

    text = _message_text(context)
    assert "Implement the feature" in text
    assert "recent history should survive" in text
    assert "ancient history should be dropped" not in text
    assert "[truncated" in text
    assert context["budgetStats"]["estimatedTokens"] <= 280
    assert context["budgetStats"]["droppedSections"]
    assert context["budgetStats"]["trimmedSections"]


def test_context_builder_handles_empty_and_missing_workspace_root(store: SQLiteStore, tmp_path: Path) -> None:
    empty_workspace_root = tmp_path / "empty"
    empty_workspace_root.mkdir()
    empty_context = _make_builder_context(store, empty_workspace_root, goal="What is here?")
    empty_text = _message_text(empty_context)
    assert "Workspace root is accessible but empty." in empty_text

    workspace_root = tmp_path / "missing"

    context = _make_builder_context(store, workspace_root, goal="What is here?")

    text = _message_text(context)
    assert "Workspace root is not accessible" in text
    assert context["workspace_root"] == str(workspace_root)
    assert context["messages"]
    assert context["budgetStats"]["estimatedTokens"] <= context["budgetStats"]["maxContextTokens"]
