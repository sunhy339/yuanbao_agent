from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.builtin import build_builtin_tools
from local_agent_runtime.tools.registry import ToolRegistry


FAKE_PROVIDER_CONFIG = {
    "mode": "openai-compatible",
    "apiKey": "sk-local-fake",
    "baseUrl": "https://fake-provider.local/v1",
    "model": "fake-chat",
}


class ScriptedHttpPostProvider:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self._adapter = ProviderAdapter(
            config={"provider": FAKE_PROVIDER_CONFIG},
            http_post=self._http_post,
        )

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        return self._adapter.generate(prompt, context)

    def _http_post(self, **kwargs: Any) -> tuple[int, bytes]:
        self.requests.append(
            {
                **kwargs,
                "json": json.loads(kwargs["body"].decode("utf-8")),
            }
        )
        if not self._responses:
            raise AssertionError("Fake provider received more POST requests than scripted")
        return 200, json.dumps(self._responses.pop(0), ensure_ascii=False).encode("utf-8")


class ScriptedSseProvider:
    def __init__(self, responses: list[list[bytes]]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self._adapter = ProviderAdapter(
            config={"provider": FAKE_PROVIDER_CONFIG},
            http_post=self._unexpected_post,
            http_stream=self._http_stream,
        )

    def generate(self, _prompt: str, _context: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("Streaming smoke should use the fake SSE transport")

    def stream(self, prompt: str, context: dict[str, Any]) -> Iterator[dict[str, Any]]:
        yield from self._adapter.stream(prompt, context)

    def _unexpected_post(self, **_kwargs: Any) -> tuple[int, bytes]:
        raise AssertionError("Streaming smoke should not use non-streaming POST")

    def _http_stream(self, **kwargs: Any) -> tuple[int, Iterable[bytes]]:
        self.requests.append(
            {
                **kwargs,
                "json": json.loads(kwargs["body"].decode("utf-8")),
            }
        )
        if not self._responses:
            raise AssertionError("Fake provider received more SSE requests than scripted")
        return 200, iter(self._responses.pop(0))


def _make_runtime(tmp_path: Path, provider: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    store.update_config({"config": {"provider": FAKE_PROVIDER_CONFIG}})
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    collaboration = CollaborationService(store, event_bus)
    subagent_service = SubagentService(store, collaboration)
    tool_registry = ToolRegistry(
        build_builtin_tools(policy_guard=policy_guard, store=store, subagent_service=subagent_service)
    )
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
    envelope = {
        "jsonrpc": "2.0",
        "id": f"req_{len(runtime.events)}_{method}",
        "method": method,
        "params": params,
    }
    response = runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == envelope["id"]
    return response


def _result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"][key]


def _open_session(runtime: SimpleNamespace, workspace_root: Path) -> dict[str, Any]:
    workspace = _result(_rpc(runtime, "workspace.open", {"path": str(workspace_root)}), "workspace")
    return _result(
        _rpc(
            runtime,
            "session.create",
            {"workspaceId": workspace["id"], "title": "E2E smoke"},
        ),
        "session",
    )


def _chat_response(message: dict[str, Any], *, finish_reason: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl_fake",
        "model": "fake-chat",
        "choices": [
            {
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"total_tokens": 11},
    }


def _tool_call_response(arguments: dict[str, Any]) -> dict[str, Any]:
    return _chat_response(
        {
            "role": "assistant",
            "content": "I will edit the requested file.",
            "tool_calls": [
                {
                    "id": "call_apply_patch",
                    "type": "function",
                    "function": {
                        "name": "apply_patch",
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            ],
        },
        finish_reason="tool_calls",
    )


def _final_response() -> dict[str, Any]:
    return _chat_response(
        {"role": "assistant", "content": "Patch applied after approval."},
        finish_reason="stop",
    )


def _sse(payload: dict[str, Any] | str) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n".encode("utf-8")


def _streaming_tool_call_response(arguments: dict[str, Any]) -> list[bytes]:
    return [
        _sse(
            {
                "id": "chatcmpl_fake",
                "model": "fake-chat",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "I will edit the requested file."},
                    }
                ],
            }
        ),
        _sse(
            {
                "id": "chatcmpl_fake",
                "model": "fake-chat",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_apply_patch",
                                    "type": "function",
                                    "function": {
                                        "name": "apply_patch",
                                        "arguments": json.dumps(arguments, ensure_ascii=False),
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        ),
        _sse("[DONE]"),
    ]


def _streaming_final_response() -> list[bytes]:
    return [
        _sse(
            {
                "id": "chatcmpl_fake",
                "model": "fake-chat",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "Patch applied after approval."},
                        "finish_reason": "stop",
                    }
                ],
            }
        ),
        _sse("[DONE]"),
    ]


def _assert_trace_covers_e2e(runtime: SimpleNamespace, task_id: str) -> None:
    trace = _rpc(runtime, "trace.list", {"taskId": task_id})["result"]["traceEvents"]
    trace_types = [event["type"] for event in trace]
    trace_sources = {event["source"] for event in trace}

    assert "provider.request" in trace_types
    assert "provider.response" in trace_types
    assert "tool.started" in trace_types
    assert "tool.completed" in trace_types
    assert "patch.proposed" in trace_types
    assert "patch.applied" in trace_types
    assert "approval.requested" in trace_types
    assert "approval.resolved" in trace_types
    assert "task.completed" in trace_types
    assert {"provider", "tool", "patch", "approval", "task"}.issubset(trace_sources)


def _run_patch_approval_smoke(runtime: SimpleNamespace, workspace_root: Path) -> dict[str, Any]:
    workspace_root.mkdir()
    target_file = workspace_root / "todo.txt"
    target_file.write_text("status: old\n", encoding="utf-8")
    session = _open_session(runtime, workspace_root)

    task = _result(
        _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "Change todo.txt status to new"},
        ),
        "task",
    )
    assert task["status"] == "waiting_approval"
    assert target_file.read_text(encoding="utf-8") == "status: old\n"

    approval_requested = next(event for event in runtime.events if event["type"] == "approval.requested")
    approval_id = approval_requested["payload"]["approvalId"]
    _rpc(runtime, "approval.submit", {"approvalId": approval_id, "decision": "approved"})

    final_task = _result(_rpc(runtime, "task.get", {"taskId": task["id"]}), "task")
    assert final_task["status"] == "completed"
    assert final_task["resultSummary"] == "Patch applied after approval."
    assert target_file.read_text(encoding="utf-8") == "status: new\n"
    _assert_trace_covers_e2e(runtime, task["id"])
    return final_task


def test_non_streaming_openai_compatible_e2e_smoke_applies_approved_patch(tmp_path: Path) -> None:
    provider = ScriptedHttpPostProvider(
        [
            _tool_call_response({"files": [{"path": "todo.txt", "content": "status: new\n"}]}),
            _final_response(),
        ]
    )
    runtime = _make_runtime(tmp_path, provider)
    try:
        _run_patch_approval_smoke(runtime, tmp_path / "workspace")
    finally:
        runtime.store.close()

    assert len(provider.requests) == 2
    assert "stream" not in provider.requests[0]["json"]
    assert provider.requests[0]["json"]["tools"]
    assert provider.requests[1]["json"]["messages"][-1]["role"] == "tool"


def test_streaming_openai_compatible_e2e_smoke_applies_approved_patch(tmp_path: Path) -> None:
    provider = ScriptedSseProvider(
        [
            _streaming_tool_call_response({"files": [{"path": "todo.txt", "content": "status: new\n"}]}),
            _streaming_final_response(),
        ]
    )
    runtime = _make_runtime(tmp_path, provider)
    try:
        _run_patch_approval_smoke(runtime, tmp_path / "workspace")
    finally:
        runtime.store.close()

    assert len(provider.requests) == 2
    assert provider.requests[0]["json"]["stream"] is True
    assert provider.requests[0]["headers"]["Accept"] == "text/event-stream"
    assert provider.requests[1]["json"]["messages"][-1]["role"] == "tool"
