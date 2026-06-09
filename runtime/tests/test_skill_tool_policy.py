"""Tests for Skill ToolPolicy filtering and skill.tools.filtered event.

Test categories:
  A. ToolPolicy enum and SkillPreset defaults
  B. SkillRegistry stores/retrieves tool_policy
  C. ContextBuilder filtering by policy (strict_whitelist / inherit_all / inherit_mcp)
  D. E2E: skill.tools.filtered event published via orchestrator
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from local_agent_runtime.context.builder import ContextBuilder
from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.skills.registry import SkillRegistry
from local_agent_runtime.skills.types import BUILTIN_SKILLS, SkillPreset, ToolPolicy
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import BUILTIN_TOOL_SCHEMAS, ToolRegistry


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ?# A. ToolPolicy enum and SkillPreset defaults
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ?

class TestToolPolicyEnum:
    def test_values(self) -> None:
        assert ToolPolicy.STRICT_WHITELIST.value == "strict_whitelist"
        assert ToolPolicy.INHERIT_ALL.value == "inherit_all"
        assert ToolPolicy.INHERIT_MCP.value == "inherit_mcp"

    def test_from_string(self) -> None:
        assert ToolPolicy("strict_whitelist") == ToolPolicy.STRICT_WHITELIST
        assert ToolPolicy("inherit_all") == ToolPolicy.INHERIT_ALL
        assert ToolPolicy("inherit_mcp") == ToolPolicy.INHERIT_MCP

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolPolicy("unknown_policy")


class TestSkillPresetDefaultPolicy:
    def test_builtin_skills_default_to_strict_whitelist(self) -> None:
        for skill in BUILTIN_SKILLS:
            assert skill.tool_policy == ToolPolicy.STRICT_WHITELIST, (
                f"Skill {skill.id} should default to strict_whitelist"
            )

    def test_custom_skill_default_policy(self) -> None:
        skill = SkillPreset(
            id="test",
            name="Test",
            description="desc",
            system_prompt="",
            tool_whitelist=["read_file"],
            parameter_constraints={},
            category="custom",
        )
        assert skill.tool_policy == ToolPolicy.STRICT_WHITELIST

    def test_custom_skill_explicit_policy(self) -> None:
        skill = SkillPreset(
            id="test",
            name="Test",
            description="desc",
            system_prompt="",
            tool_whitelist=[],
            parameter_constraints={},
            category="custom",
            tool_policy=ToolPolicy.INHERIT_ALL,
        )
        assert skill.tool_policy == ToolPolicy.INHERIT_ALL


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ?# B. SkillRegistry stores/retrieves tool_policy
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ?

class TestSkillRegistryPolicyPersistence:
    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.registry = SkillRegistry(self.store)

    def test_builtin_skills_persist_policy(self) -> None:
        for skill in BUILTIN_SKILLS:
            fetched = self.registry.get(skill.id)
            assert fetched is not None
            assert fetched.tool_policy == ToolPolicy.STRICT_WHITELIST

    def test_create_custom_skill_with_policy(self) -> None:
        skill = self.registry.create_custom_skill({
            "id": "custom_mcp",
            "name": "MCP Skill",
            "description": "Uses all MCP tools",
            "system_prompt": "",
            "tool_whitelist": [],
            "parameter_constraints": {},
            "category": "custom",
            "tool_policy": "inherit_mcp",
        })
        assert skill.tool_policy == ToolPolicy.INHERIT_MCP

        # Round-trip through store
        fetched = self.registry.get("custom_mcp")
        assert fetched is not None
        assert fetched.tool_policy == ToolPolicy.INHERIT_MCP

    def test_update_skill_policy(self) -> None:
        self.registry.create_custom_skill({
            "id": "updatable",
            "name": "Updatable",
            "description": "",
            "system_prompt": "",
            "tool_whitelist": ["read_file"],
            "parameter_constraints": {},
            "category": "custom",
        })
        updated = self.registry.update_skill("updatable", {"tool_policy": "inherit_all"})
        assert updated.tool_policy == ToolPolicy.INHERIT_ALL

    def test_invalid_policy_defaults_to_strict_whitelist(self) -> None:
        """An unrecognized tool_policy value in the DB falls back gracefully."""
        # Insert directly via store with bad value
        self.store.upsert_skill(
            "bad_policy_skill",
            name="Bad Policy",
            description="",
            system_prompt="",
            tool_whitelist=[],
            parameter_constraints={},
            category="custom",
            tool_policy="nonexistent_policy",
        )
        fetched = self.registry.get("bad_policy_skill")
        assert fetched is not None
        assert fetched.tool_policy == ToolPolicy.STRICT_WHITELIST


def test_legacy_skill_presets_table_migrates_tool_policy(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_skill_presets.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE skill_presets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            system_prompt TEXT,
            tool_whitelist TEXT DEFAULT '[]',
            parameter_constraints TEXT DEFAULT '{}',
            category TEXT DEFAULT 'custom',
            is_builtin INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO skill_presets (
            id, name, description, system_prompt, tool_whitelist,
            parameter_constraints, category, is_builtin, created_at, updated_at
        )
        VALUES (
            'legacy_skill', 'Legacy Skill', '', '', '["read_file"]',
            '{}', 'custom', 0, 1, 1
        )
        """
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(str(db_path))
    try:
        columns = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(skill_presets)").fetchall()
        }
        assert "tool_policy" in columns

        registry = SkillRegistry(store)
        legacy = registry.get("legacy_skill")
        assert legacy is not None
        assert legacy.tool_policy == ToolPolicy.STRICT_WHITELIST
        for skill in BUILTIN_SKILLS:
            assert registry.get(skill.id) is not None
    finally:
        store.close()


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ?# C. ContextBuilder filtering by policy
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ?
# Build a set of fake tool schemas including MCP-like tools
_FAKE_BUILTIN_TOOLS = [
    {"name": "read_file", "description": "Read a file"},
    {"name": "write_file", "description": "Write a file"},
    {"name": "run_command", "description": "Run a command"},
    {"name": "search_files", "description": "Search files"},
    {"name": "ask_user_question", "description": "Ask the user for input"},
    {"name": "enter_plan_mode", "description": "Enter plan mode"},
    {"name": "exit_plan_mode", "description": "Submit a plan for approval"},
]

_FAKE_MCP_TOOLS = [
    {"name": "mcp__github__create_issue", "description": "Create GitHub issue"},
    {"name": "mcp__github__list_prs", "description": "List pull requests"},
    {"name": "mcp__jira__create_ticket", "description": "Create Jira ticket"},
]

_FAKE_MEMORY_TOOLS = [
    {"name": "memory.recall", "description": "Recall memory"},
    {"name": "memory.remember", "description": "Remember something"},
]

_FAKE_SCRATCHPAD_TOOLS = [
    {"name": "scratchpad.write", "description": "Write to scratchpad"},
]

_ALL_FAKE_TOOLS = _FAKE_BUILTIN_TOOLS + _FAKE_MCP_TOOLS + _FAKE_MEMORY_TOOLS + _FAKE_SCRATCHPAD_TOOLS


def _make_builder(store: SQLiteStore, tools: list[dict[str, Any]] | None = None) -> ContextBuilder:
    return ContextBuilder(
        store=store,
        tool_schemas=tools,
        skill_registry=SkillRegistry(store),
    )


def _setup_session(store: SQLiteStore) -> str:
    """Create a workspace and session, return session_id."""
    ws = store.upsert_workspace("/tmp/test")
    sess = store.create_session(workspace_id=ws["id"], title="Test")
    return sess["id"]


class TestContextBuilderStrictWhitelist:
    """strict_whitelist: only whitelisted + memory/scratchpad tools pass."""

    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.builder = _make_builder(self.store, _ALL_FAKE_TOOLS)
        self.session_id = _setup_session(self.store)

    def test_only_whitelisted_tools_pass(self) -> None:
        """Skill with read_file whitelist should only keep read_file + memory/scratchpad."""
        self.store.create_skill({
            "id": "strict_skill",
            "name": "Strict",
            "description": "",
            "system_prompt": "",
            "tool_whitelist": ["read_file"],
            "parameter_constraints": {},
            "category": "custom",
            "tool_policy": "strict_whitelist",
        })
        ctx = self.builder.build(session_id=self.session_id, goal="test", skill_id="strict_skill")
        tool_names = [t["name"] for t in ctx["tools"]]
        assert "read_file" in tool_names
        assert "memory.recall" in tool_names
        assert "memory.remember" in tool_names
        assert "scratchpad.write" in tool_names
        assert "ask_user_question" in tool_names
        assert "enter_plan_mode" not in tool_names
        assert "exit_plan_mode" not in tool_names
        # These should NOT be present
        assert "write_file" not in tool_names
        assert "run_command" not in tool_names
        assert "mcp__github__create_issue" not in tool_names

    def test_mcp_tools_filtered_out(self) -> None:
        """strict_whitelist should filter out MCP tools unless explicitly whitelisted."""
        self.store.create_skill({
            "id": "no_mcp",
            "name": "No MCP",
            "description": "",
            "system_prompt": "",
            "tool_whitelist": ["read_file", "search_files"],
            "parameter_constraints": {},
            "category": "custom",
        })
        ctx = self.builder.build(session_id=self.session_id, goal="test", skill_id="no_mcp")
        tool_names = [t["name"] for t in ctx["tools"]]
        for mcp_name in [t["name"] for t in _FAKE_MCP_TOOLS]:
            assert mcp_name not in tool_names

    def test_filtered_tool_names_in_snapshot_metadata(self) -> None:
        self.store.create_skill({
            "id": "meta_skill",
            "name": "Meta",
            "description": "",
            "system_prompt": "",
            "tool_whitelist": ["read_file"],
            "parameter_constraints": {},
            "category": "custom",
        })
        ctx = self.builder.build(session_id=self.session_id, goal="test", skill_id="meta_skill")
        filtered = ctx["snapshot_metadata"]["filtered_tool_names"]
        assert filtered is not None
        assert "read_file" in filtered
        assert "write_file" not in filtered


class TestContextBuilderInheritAll:
    """inherit_all: all tools available, no filtering."""

    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.builder = _make_builder(self.store, _ALL_FAKE_TOOLS)
        self.session_id = _setup_session(self.store)

    def test_all_tools_available(self) -> None:
        self.store.create_skill({
            "id": "all_tools",
            "name": "All Tools",
            "description": "",
            "system_prompt": "",
            "tool_whitelist": ["read_file"],  # whitelist is ignored for inherit_all
            "parameter_constraints": {},
            "category": "custom",
            "tool_policy": "inherit_all",
        })
        ctx = self.builder.build(session_id=self.session_id, goal="test", skill_id="all_tools")
        tool_names = [t["name"] for t in ctx["tools"]]
        # All tools should be present
        for tool in _ALL_FAKE_TOOLS:
            assert tool["name"] in tool_names

    def test_filtered_tool_names_is_none(self) -> None:
        """inherit_all doesn't set filtered_tool_names since nothing was filtered."""
        self.store.create_skill({
            "id": "all_tools",
            "name": "All Tools",
            "description": "",
            "system_prompt": "",
            "tool_whitelist": [],
            "parameter_constraints": {},
            "category": "custom",
            "tool_policy": "inherit_all",
        })
        ctx = self.builder.build(session_id=self.session_id, goal="test", skill_id="all_tools")
        assert ctx["snapshot_metadata"]["filtered_tool_names"] is None


class TestContextBuilderInheritMcp:
    """inherit_mcp: whitelist for built-in tools, but all MCP tools pass through."""

    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.builder = _make_builder(self.store, _ALL_FAKE_TOOLS)
        self.session_id = _setup_session(self.store)

    def test_mcp_tools_always_available(self) -> None:
        self.store.create_skill({
            "id": "mcp_skill",
            "name": "MCP Skill",
            "description": "",
            "system_prompt": "",
            "tool_whitelist": ["read_file"],  # only read_file from builtins
            "parameter_constraints": {},
            "category": "custom",
            "tool_policy": "inherit_mcp",
        })
        ctx = self.builder.build(session_id=self.session_id, goal="test", skill_id="mcp_skill")
        tool_names = [t["name"] for t in ctx["tools"]]
        # MCP tools always available
        for tool in _FAKE_MCP_TOOLS:
            assert tool["name"] in tool_names
        # memory/scratchpad always available
        assert "memory.recall" in tool_names
        assert "scratchpad.write" in tool_names
        # Only whitelisted builtins
        assert "read_file" in tool_names
        assert "write_file" not in tool_names
        assert "run_command" not in tool_names

    def test_mcp_with_empty_whitelist(self) -> None:
        """inherit_mcp with empty whitelist: only MCP + memory/scratchpad."""
        self.store.create_skill({
            "id": "mcp_only",
            "name": "MCP Only",
            "description": "",
            "system_prompt": "",
            "tool_whitelist": [],
            "parameter_constraints": {},
            "category": "custom",
            "tool_policy": "inherit_mcp",
        })
        ctx = self.builder.build(session_id=self.session_id, goal="test", skill_id="mcp_only")
        tool_names = [t["name"] for t in ctx["tools"]]
        # Only MCP + memory/scratchpad
        for tool in _FAKE_MCP_TOOLS:
            assert tool["name"] in tool_names
        assert "memory.recall" in tool_names
        assert "scratchpad.write" in tool_names
        # No builtins
        assert "read_file" not in tool_names
        assert "write_file" not in tool_names


class TestContextBuilderNoSkill:
    """Without a skill, all tools pass through (no filtering)."""

    def setup_method(self) -> None:
        self.store = SQLiteStore(":memory:")
        self.builder = _make_builder(self.store, _ALL_FAKE_TOOLS)
        self.session_id = _setup_session(self.store)

    def test_no_skill_no_filtering(self) -> None:
        ctx = self.builder.build(session_id=self.session_id, goal="test")
        tool_names = [t["name"] for t in ctx["tools"]]
        for tool in _ALL_FAKE_TOOLS:
            assert tool["name"] in tool_names
        assert ctx["snapshot_metadata"]["filtered_tool_names"] is None


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ?# D. E2E: skill.tools.filtered event
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ?

class ScriptedProvider:
    """Deterministic provider that returns pre-scripted responses."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        if not self._responses:
            raise AssertionError("Provider called more times than scripted")
        return self._responses.pop(0)


def _call_result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, f"Expected 'result', got: {response}"
    return response["result"][key]


def _make_runtime(tmp_path: Any, provider: Any, tools: dict[str, Any] | None = None) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    tool_registry = ToolRegistry(tools or {})
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
    )
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, events=events)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = f"req_{len(runtime.events)}_{method}"
    envelope = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    response = runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == request_id
    return response


def _open_session(runtime: SimpleNamespace, tmp_path: Any) -> dict[str, Any]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    workspace = _call_result(
        _rpc(runtime, "workspace.open", {"path": str(workspace_root)}),
        "workspace",
    )
    return _call_result(
        _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "Test"}),
        "session",
    )


class TestSkillToolsFilteredEvent:
    """E2E test: skill.tools.filtered event is published when skill filters tools."""

    def test_filtered_event_on_skill_routing(self, tmp_path: Any) -> None:

        provider = ScriptedProvider([{"final": "Done."}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)

        # Create a skill with strict whitelist
        runtime.store.create_skill({
            "id": "review",
            "name": "Review",
            "description": "",
            "system_prompt": "Review code.",
            "tool_whitelist": ["read_file"],
            "parameter_constraints": {},
            "category": "coding",
            "tool_policy": "strict_whitelist",
        })

        task = _call_result(
            _rpc(
                runtime,
                "message.send",
                {"sessionId": session["id"], "content": "review code", "skillId": "review"},
            ),
            "task",
        )

        assert task["status"] == "completed"

        # Check that skill.tools.filtered event was published
        filtered_events = [e for e in runtime.events if e.get("type") == "skill.tools.filtered"]
        assert len(filtered_events) >= 1
        payload = filtered_events[0].get("payload", {})
        assert payload.get("skillId") == "review"
        assert "read_file" in payload.get("allowedTools", [])
        assert "write_file" in payload.get("filteredOut", [])

    def test_no_filtered_event_without_skill(self, tmp_path: Any) -> None:

        provider = ScriptedProvider([{"final": "Done."}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "edit code"}),
            "task",
        )

        assert task["status"] == "completed"

        # No skill.tools.filtered event should be published
        filtered_events = [e for e in runtime.events if e.get("type") == "skill.tools.filtered"]
        assert len(filtered_events) == 0

    def test_missing_skill_publishes_fallback_event(self, tmp_path: Any) -> None:

        provider = ScriptedProvider([{"final": "Done."}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)

        task = _call_result(
            _rpc(
                runtime,
                "message.send",
                {"sessionId": session["id"], "content": "edit with missing skill", "skillId": "missing_skill"},
            ),
            "task",
        )

        assert task["status"] == "completed"
        fallback_events = [e for e in runtime.events if e.get("type") == "skill.fallback"]
        assert len(fallback_events) == 1
        payload = fallback_events[0].get("payload", {})
        assert payload["requestedSkillId"] == "missing_skill"
        assert payload["reason"] == "skill_not_found"
        stored = runtime.store.get_task({"taskId": task["id"]})["task"]
        assert stored["routing"]["skillFallback"]["fallback"] == "default_prompt_and_tools"
        usage = runtime.store.list_skill_usage({"skillId": "missing_skill"})["usage"]
        assert usage == []

    def test_no_filtered_event_with_inherit_all(self, tmp_path: Any) -> None:

        provider = ScriptedProvider([{"final": "Done."}])
        runtime = _make_runtime(tmp_path, provider)
        session = _open_session(runtime, tmp_path)

        # Create a skill with inherit_all policy
        runtime.store.create_skill({
            "id": "full_access",
            "name": "Full Access",
            "description": "",
            "system_prompt": "",
            "tool_whitelist": [],
            "parameter_constraints": {},
            "category": "custom",
            "tool_policy": "inherit_all",
        })

        task = _call_result(
            _rpc(
                runtime,
                "message.send",
                {"sessionId": session["id"], "content": "do anything", "skillId": "full_access"},
            ),
            "task",
        )

        assert task["status"] == "completed"

        # inherit_all doesn't filter, so no event
        filtered_events = [e for e in runtime.events if e.get("type") == "skill.tools.filtered"]
        assert len(filtered_events) == 0
