"""Integration tests: PermissionEngine wired through tools and pipeline."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.policy.permission_engine import (
    PermissionEngine,
    PermissionRequest,
)
from local_agent_runtime.tools import build_builtin_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store() -> MagicMock:
    store = MagicMock()
    store.create_approval.return_value = {
        "id": "appr-1",
        "taskId": "t-1",
        "kind": "run_command",
        "requestJson": "{}",
        "decision": "pending",
    }
    store.create_patch.return_value = {
        "id": "p-1",
        "taskId": "t-1",
        "summary": "test",
        "diffText": "",
        "filesChanged": 0,
        "status": "proposed",
    }
    # Provide runtime config needed by _shared helpers
    store.get_config.return_value = {
        "config": {
            "tools": {"runCommand": {"allowedShell": "bash"}},
            "policy": {"approvalMode": "on_write_or_command"},
        },
    }
    return store


def test_engine_refreshes_store_config_inside_tool(tmp_path: Any) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    store = _make_store()
    store.get_config.return_value = {
        "config": {
            "permissions": {
                "preset": "balanced",
                "capabilities": {
                    "webFetch": {"mode": "allow", "scope": "*"},
                },
            },
            "tools": {"runCommand": {"allowedShell": "bash"}},
            "policy": {"approvalMode": "on_write_or_command"},
        },
    }
    tools = build_builtin_tools(
        policy_guard=_make_policy_guard(),
        store=store,
        subagent_service=None,
        permission_engine=PermissionEngine(config={"permissions": {"preset": "balanced"}}, store=store),
    )

    with patch("local_agent_runtime.tools.web_fetch.urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "text/plain; charset=utf-8"}
        mock_resp.read.return_value = b"ok"
        mock_urlopen.return_value = mock_resp

        result = tools["web_fetch"]({
            "url": "https://example.com",
            "taskId": "t-1",
            "workspaceRoot": str(workspace_root),
        })

    assert result["status"] == "ok"
    store.create_approval.assert_not_called()


def _make_policy_guard() -> MagicMock:
    guard = MagicMock()
    guard.requires_approval.return_value = True
    guard.validate_command.return_value = None
    guard.ensure_within_workspace.return_value = "/ws"
    return guard


def _tools(preset: str = "balanced", *, overrides: dict | None = None):
    caps = overrides or {}
    config = {"permissions": {"preset": preset, "capabilities": caps}}
    engine = PermissionEngine(config=config)
    store = _make_store()
    guard = _make_policy_guard()
    return build_builtin_tools(
        policy_guard=guard,
        store=store,
        subagent_service=None,
        permission_engine=engine,
    )


# ---------------------------------------------------------------------------
# run_command integration
# ---------------------------------------------------------------------------

class TestRunCommandIntegration:
    """Run_command has many pre-validation steps, so we test at the engine level
    and verify the tool integration via a focused mock approach."""

    def test_balanced_approval_required_via_engine(self):
        """Verify the engine decision feeds through to run_command approval flow."""
        engine = PermissionEngine(config={"permissions": {"preset": "balanced"}})
        decision = engine.evaluate(PermissionRequest(capability="runCommand", tool_name="run_command"))
        assert decision.decision == "approval_required"
        assert decision.approval_kind == "run_command"

    def test_blocked_returns_deny_via_engine(self):
        engine = PermissionEngine(config={
            "permissions": {"preset": "balanced", "capabilities": {"runCommand": {"mode": "blocked", "scope": "*"}}}
        })
        decision = engine.evaluate(PermissionRequest(capability="runCommand", tool_name="run_command"))
        assert decision.decision == "deny"

    def test_autonomous_still_asks_via_engine(self):
        engine = PermissionEngine(config={"permissions": {"preset": "autonomous"}})
        decision = engine.evaluate(PermissionRequest(capability="runCommand", tool_name="run_command"))
        assert decision.decision == "approval_required"


# ---------------------------------------------------------------------------
# web_fetch integration
# ---------------------------------------------------------------------------

class TestWebFetchIntegration:
    def test_safe_blocks_fetch(self):
        tools = _tools("safe")
        # Mock urlopen to avoid real network call
        with patch("local_agent_runtime.tools.web_fetch.urllib.request.urlopen"):
            result = tools["web_fetch"]({
                "url": "https://example.com",
                "taskId": "t-1",
                "sessionId": "s-1",
            })
            assert result["status"] == "blocked"

    def test_balanced_returns_approval_required(self):
        tools = _tools()
        with patch("local_agent_runtime.tools.web_fetch.urllib.request.urlopen"):
            result = tools["web_fetch"]({
                "url": "https://example.com",
                "taskId": "t-1",
                "sessionId": "s-1",
            })
            assert result["status"] == "approval_required"

    def test_autonomous_ask_still_needs_approval(self):
        tools = _tools("autonomous")
        with patch("local_agent_runtime.tools.web_fetch.urllib.request.urlopen"):
            result = tools["web_fetch"]({
                "url": "https://example.com",
                "taskId": "t-1",
                "sessionId": "s-1",
            })
            # webFetch is "ask" in autonomous → approval_required
            assert result["status"] == "approval_required"


# ---------------------------------------------------------------------------
# task (subagent) integration
# ---------------------------------------------------------------------------

class TestTaskIntegration:
    def test_safe_returns_approval_required(self):
        mock_subagent = MagicMock()
        tools = _tools("safe")
        # Override the subagent_service after build
        tools_built = build_builtin_tools(
            policy_guard=_make_policy_guard(),
            store=_make_store(),
            subagent_service=mock_subagent,
            permission_engine=PermissionEngine(
                config={"permissions": {"preset": "safe"}},
            ),
        )
        result = tools_built["task"]({
            "prompt": "do something",
            "taskId": "t-1",
            "sessionId": "s-1",
        })
        assert result["status"] == "approval_required"

    def test_autonomous_allows_dispatch(self):
        mock_subagent = MagicMock()
        mock_subagent.dispatch.return_value = {"status": "completed", "result": "ok"}
        tools = build_builtin_tools(
            policy_guard=_make_policy_guard(),
            store=_make_store(),
            subagent_service=mock_subagent,
            permission_engine=PermissionEngine(
                config={"permissions": {"preset": "autonomous"}},
            ),
        )
        result = tools["task"]({
            "prompt": "do something",
            "taskId": "t-1",
            "sessionId": "s-1",
        })
        assert result["status"] == "completed"
        mock_subagent.dispatch.assert_called_once()

    def test_balanced_blocks_with_no_taskId(self):
        mock_subagent = MagicMock()
        tools = build_builtin_tools(
            policy_guard=_make_policy_guard(),
            store=_make_store(),
            subagent_service=mock_subagent,
            permission_engine=PermissionEngine(
                config={"permissions": {"preset": "balanced"}},
            ),
        )
        with pytest.raises(ValueError, match="taskId is required"):
            tools["task"]({
                "prompt": "do something",
                "sessionId": "s-1",
            })


# ---------------------------------------------------------------------------
# Backward compatibility — no permission_engine
# ---------------------------------------------------------------------------

class TestNotebookIntegration:
    def test_execute_cell_requires_run_command_approval_in_balanced(self, tmp_path: Any):
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir()
        (workspace_root / "analysis.ipynb").write_text(
            json.dumps(
                {
                    "cells": [
                        {
                            "cell_type": "code",
                            "execution_count": None,
                            "metadata": {},
                            "outputs": [],
                            "source": ["print('needs approval')\n"],
                        }
                    ],
                    "metadata": {},
                    "nbformat": 4,
                    "nbformat_minor": 5,
                }
            ),
            encoding="utf-8",
        )
        store = _make_store()
        store.find_approval.return_value = None
        tools = build_builtin_tools(
            policy_guard=PolicyGuard(),
            store=store,
            subagent_service=None,
            permission_engine=PermissionEngine(config={"permissions": {"preset": "balanced"}}),
        )

        result = tools["notebook"]({
            "workspaceRoot": str(workspace_root),
            "path": "analysis.ipynb",
            "action": "execute_cell",
            "cell_index": 0,
            "taskId": "t-1",
        })

        assert result["status"] == "approval_required"
        approval_call = store.create_approval.call_args
        assert approval_call.kwargs["kind"] == "run_command"
        request = approval_call.kwargs["request"]
        assert request["toolName"] == "notebook"
        assert request["notebookAction"] == "execute_cell"
        assert request["command"] == "notebook execute_cell analysis.ipynb #cell 0"
        assert request["path"] == "analysis.ipynb"
        assert request["cellIndex"] == 0
        assert request["sourceSha256"]


class TestBackwardCompatibility:
    def test_no_engine_uses_legacy_path(self):
        """When permission_engine is None, legacy policy_guard path runs."""
        guard = _make_policy_guard()
        guard.requires_approval.return_value = False
        store = _make_store()
        tools = build_builtin_tools(
            policy_guard=guard,
            store=store,
            subagent_service=None,
            # No permission_engine
        )
        # web_fetch should not crash; it just does the fetch
        with patch("local_agent_runtime.tools.web_fetch.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.headers = {"Content-Type": "text/html"}
            mock_resp.read.return_value = b"hello"
            mock_urlopen.return_value = mock_resp
            result = tools["web_fetch"]({
                "url": "https://example.com",
                "taskId": "t-1",
                "sessionId": "s-1",
            })
            assert result["status"] == "ok"
