from __future__ import annotations

import base64
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


def _message_with_content_prefix(context: dict[str, Any], prefix: str) -> dict[str, Any]:
    for message in context["messages"]:
        if str(message.get("content") or "").startswith(prefix):
            return message
    raise AssertionError(f"missing message starting with {prefix!r}")


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
    assert "User-facing text:" in text
    assert "before first tool" in text
    assert "after tool batches" in text
    assert "Backend status is not thinking" in text
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


def test_context_builder_minimal_context_skips_workspace_pack_and_tools(store: SQLiteStore, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "README.md").write_text("# Demo\n" + ("large project notes\n" * 2000), encoding="utf-8")

    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Minimal")
    context = ContextBuilder(store).build(session_id=session["id"], goal="你好", minimal=True)

    text = _message_text(context)
    assert context["minimal"] is True
    assert context["tools"] == []
    assert context["openai_tools"] == []
    assert context["budgetStats"]["toolSchemaTokens"] == 0
    assert context["budgetStats"]["estimatedInputTokens"] < 1000
    assert "stable_workspace_context" not in context["budgetStats"]["includedSections"]
    assert "large project notes" not in text


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


def test_context_builder_includes_referenced_file_content(store: SQLiteStore, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    source_dir = workspace_root / "src"
    source_dir.mkdir()
    (source_dir / "target.ts").write_text(
        "export const referencedValue = 'visible in context';\n",
        encoding="utf-8",
    )
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("must not leak\n", encoding="utf-8")
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="References")
    store.create_message(
        session_id=session["id"],
        role="user",
        content=f"Please inspect @src/target.ts and @{outside_file}",
        metadata={
            "attachments": ["src/target.ts", str(outside_file)],
            "fileReferences": ["src/target.ts", str(outside_file)],
        },
    )

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Continue with the referenced file",
        lightweight=False,
    )

    text = _message_text(context)
    assert "Referenced files: src/target.ts" in text
    assert "Referenced file content: src/target.ts" in text
    assert "referencedValue" in text
    assert "visible in context" in text
    assert "must not leak" not in text


def test_context_builder_includes_text_attachment_content(store: SQLiteStore, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    notes_dir = workspace_root / "notes"
    notes_dir.mkdir()
    (notes_dir / "attached.md").write_text(
        "Attached workspace text should reach the model.\n",
        encoding="utf-8",
    )
    outside_file = tmp_path / "outside-attachment.md"
    outside_file.write_text("outside attachment must not leak\n", encoding="utf-8")
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Attachment context")
    store.create_message(
        session_id=session["id"],
        role="user",
        content="Please use the attached notes.",
        metadata={"attachments": ["notes/attached.md", str(outside_file)]},
    )

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Continue with the attached notes",
        lightweight=False,
    )

    text = _message_text(context)
    assert "Referenced files: notes/attached.md" in text
    assert "Referenced file content: notes/attached.md" in text
    assert "Attached workspace text should reach the model." in text
    assert "outside attachment must not leak" not in text


def test_context_builder_includes_current_message_references_before_persist(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "todo.md").write_text("- wire current references\n", encoding="utf-8")
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Current reference")

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Please use @todo.md",
        lightweight=False,
        current_message_metadata={"fileReferences": ["todo.md"], "attachments": ["todo.md"]},
    )

    text = _message_text(context)
    assert "Referenced file content: todo.md" in text
    assert "wire current references" in text


def test_context_builder_attaches_current_workspace_image_before_persist(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    image_path = workspace_root / "shot.png"
    image_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR42mP8z8BQDwAFgwJ/lwOxPgAAAABJRU5ErkJggg=="
    )
    image_path.write_bytes(image_bytes)
    outside_image = tmp_path / "outside.png"
    outside_image.write_bytes(image_bytes)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Current image")

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Please inspect the screenshot.",
        lightweight=False,
        current_message_metadata={"attachments": ["shot.png", str(outside_image)]},
    )

    user_message = context["messages"][-1]
    assert user_message["content"] == "Current user request:\nPlease inspect the screenshot."
    assert user_message["imageAttachments"] == [
        {
            "source": "base64",
            "path": "shot.png",
            "mimeType": "image/png",
            "data": base64.b64encode(image_bytes).decode("ascii"),
            "sizeBytes": len(image_bytes),
        },
        {
            "source": "base64",
            "path": outside_image.as_posix(),
            "mimeType": "image/png",
            "data": base64.b64encode(image_bytes).decode("ascii"),
            "sizeBytes": len(image_bytes),
        },
    ]


def test_context_builder_does_not_attach_external_file_references_as_images(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    image_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR42mP8z8BQDwAFgwJ/lwOxPgAAAABJRU5ErkJggg=="
    )
    outside_image = tmp_path / "outside-reference.png"
    outside_image.write_bytes(image_bytes)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="External reference")

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal=f"Please inspect @{outside_image}",
        lightweight=False,
        current_message_metadata={"fileReferences": [str(outside_image)]},
    )

    text = _message_text(context)
    assert "imageAttachments" not in context["messages"][-1]
    assert "Attached external file content" not in text


def test_context_builder_includes_current_external_text_attachment(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_file = tmp_path / "external-notes.txt"
    outside_file.write_text("external attachment should reach the model\n", encoding="utf-8")
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="External attachment")

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Use the attached external notes.",
        lightweight=False,
        current_message_metadata={"attachments": [str(outside_file)]},
    )

    text = _message_text(context)
    assert f"Attached external file content: {outside_file.as_posix()}" in text
    assert "external attachment should reach the model" in text


def test_context_builder_skips_external_text_file_references(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_file = tmp_path / "external-reference.txt"
    outside_file.write_text("external reference must not leak\n", encoding="utf-8")
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="External reference")

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal=f"Please inspect @{outside_file}",
        lightweight=False,
        current_message_metadata={"fileReferences": [str(outside_file)]},
    )

    text = _message_text(context)
    assert "external reference must not leak" not in text
    assert "Attached external file content" not in text


def test_context_builder_skips_historical_external_attachments(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_file = tmp_path / "historical-external.txt"
    outside_file.write_text("historical external attachment must not leak\n", encoding="utf-8")
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Historical external")
    store.create_message(
        session_id=session["id"],
        role="user",
        content="Earlier external attachment.",
        metadata={"attachments": [str(outside_file)]},
    )

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Continue without reattaching external files.",
        lightweight=False,
    )

    text = _message_text(context)
    assert "historical external attachment must not leak" not in text
    assert "Attached external file content" not in text


def test_context_builder_attaches_recent_workspace_images(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    image_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR42mP8z8BQDwAFgwJ/lwOxPgAAAABJRU5ErkJggg=="
    )
    (workspace_root / "previous.png").write_bytes(image_bytes)
    (workspace_root / "notes.md").write_text("not an image\n", encoding="utf-8")
    outside_image = tmp_path / "outside.png"
    outside_image.write_bytes(image_bytes)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Historical image")
    store.create_message(
        session_id=session["id"],
        role="user",
        content="Here is the screenshot from the previous turn.",
        metadata={"attachments": ["previous.png", "notes.md", str(outside_image)]},
    )

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Use the previous screenshot.",
        lightweight=False,
    )

    user_context_message = _message_with_content_prefix(context, "Dynamic context tail:")
    current_request_message = context["messages"][-1]
    assert "Recent conversation:" in str(user_context_message["content"])
    assert user_context_message["imageAttachments"] == [
        {
            "source": "base64",
            "path": "previous.png",
            "mimeType": "image/png",
            "data": base64.b64encode(image_bytes).decode("ascii"),
            "sizeBytes": len(image_bytes),
        }
    ]
    assert "imageAttachments" not in current_request_message


def test_context_builder_prefers_current_image_when_history_duplicates_path(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    image_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR42mP8z8BQDwAFgwJ/lwOxPgAAAABJRU5ErkJggg=="
    )
    (workspace_root / "same.png").write_bytes(image_bytes)
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Duplicate image")
    store.create_message(
        session_id=session["id"],
        role="user",
        content="Earlier attached the same image.",
        metadata={"attachments": ["same.png"]},
    )

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Use this screenshot again.",
        lightweight=False,
        current_message_metadata={"attachments": ["same.png"]},
    )

    user_context_message = _message_with_content_prefix(context, "Dynamic context tail:")
    current_request_message = context["messages"][-1]
    assert "imageAttachments" not in user_context_message
    assert current_request_message["imageAttachments"][0]["path"] == "same.png"


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
    stats = context["budgetStats"]
    assert stats["promptCache"]["targetFillRatio"] == 0.92
    assert stats["promptCache"]["maxStableContextTokens"] == 80000
    assert "recent_conversation" in stats["dynamicTailSections"]
    assert stats["stablePrefixTokens"] > 0
    assert stats["stablePrefixTokens"] < stats["messageTokens"]


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
                        "includeKeyFiles": True,
                        "includeStableWorkspaceContext": True,
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


def test_context_builder_splits_stable_prefix_from_dynamic_tail(
    store: SQLiteStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "README.md").write_text("# Stable docs\n", encoding="utf-8")
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="Stable split")
    store.create_message(session_id=session["id"], role="user", content="Previous volatile turn.")
    monkeypatch.setattr(
        ContextBuilder,
        "_git_summary",
        lambda self, workspace_root: "Git status summary:\n- modified runtime file.",
    )
    store.update_config(
        {
            "config": {
                "provider": {
                    "promptCache": {
                        "enabled": True,
                        "includeKeyFiles": True,
                        "includeStableWorkspaceContext": True,
                    }
                }
            }
        }
    )

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Continue implementation",
        lightweight=False,
    )

    messages = context["messages"]
    stable_message = _message_with_content_prefix(context, "Stable context prefix:")
    dynamic_message = _message_with_content_prefix(context, "Dynamic context tail:")
    assert [message["role"] for message in messages] == ["system", "user", "user", "user"]
    assert "--- README.md ---" in str(stable_message["content"])
    assert "Recent conversation:" not in str(stable_message["content"])
    assert "Git status summary:" not in str(stable_message["content"])
    assert "Recent conversation:" in str(dynamic_message["content"])
    assert "Git status summary:" in str(dynamic_message["content"])
    assert str(messages[-1]["content"]) == "Current user request:\nContinue implementation"

    stats = context["budgetStats"]
    assert "key_file:README.md" in stats["stablePrefixSections"]
    assert "recent_conversation" in stats["dynamicTailSections"]
    assert "git_status" in stats["dynamicTailSections"]
    assert stats["stablePrefixTokens"] > 0
    assert stats["stablePrefixTokens"] < stats["messageTokens"]


def test_context_builder_default_cache_prefix_does_not_inline_workspace_files(
    store: SQLiteStore,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "README.md").write_text("# Project docs\nvisible only through tools\n", encoding="utf-8")
    (workspace_root / "module.py").write_text("VALUE = 'tool-visible'\n", encoding="utf-8")
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="No workspace pack by default")

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Read README.md and module.py",
        lightweight=False,
    )

    text = _message_text(context)
    assert "Stable workspace context pack:" not in text
    assert "visible only through tools" not in text
    assert "tool-visible" not in text
    assert "stable_workspace_context" not in context["budgetStats"]["stablePrefixSections"]
    assert "key_file:README.md" not in context["budgetStats"]["stablePrefixSections"]
    assert context["budgetStats"]["promptCache"]["includeStableWorkspaceContext"] is False
    assert context["budgetStats"]["promptCache"]["includeKeyFiles"] is False


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
    store.update_config(
        {
            "config": {
                "provider": {
                    "promptCache": {
                        "enabled": True,
                        "includeKeyFiles": True,
                    }
                }
            }
        }
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
    store.update_config(
        {
            "config": {
                "provider": {
                    "promptCache": {
                        "enabled": True,
                        "includeKeyFiles": True,
                    }
                }
            }
        }
    )
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


def test_context_builder_skips_git_process_for_non_repo_workspace(
    store: SQLiteStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "not-a-repo"
    workspace_root.mkdir()
    workspace = store.upsert_workspace(str(workspace_root))
    session = store.create_session(workspace_id=workspace["id"], title="No git")

    def fail_git(_self: ContextBuilder, _root: Path, _args: list[str]) -> str | None:
        raise AssertionError("non-repository context build should not launch git")

    monkeypatch.setattr(ContextBuilder, "_run_git", fail_git)

    context = ContextBuilder(store, tool_schemas=[]).build(
        session_id=session["id"],
        goal="Inspect this non-git workspace",
        lightweight=False,
    )

    assert "not a git repository" in _message_text(context)
