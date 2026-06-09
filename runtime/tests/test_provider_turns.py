from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Iterator

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.provider.openai_compatible import ProviderAdapterError
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools.registry import ToolRegistry


class ScriptedProvider:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "context": context})
        if not self._responses:
            raise AssertionError("Provider called more times than scripted")
        return self._responses.pop(0)


class FailingProvider:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError("Provider API timeout")
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "context": context})
        raise self.error


class PartialStreamFailureProvider:
    def __init__(self) -> None:
        self.stream_calls: list[dict[str, Any]] = []
        self.generate_calls: list[dict[str, Any]] = []

    def stream(self, prompt: str, context: dict[str, Any]) -> Iterator[dict[str, Any]]:
        self.stream_calls.append({"prompt": prompt, "context": context})
        yield {"type": "content_delta", "delta": "Partial answer before failure."}
        raise ProviderAdapterError("Provider streaming response exceeded 30s before completion.")

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.generate_calls.append({"prompt": prompt, "context": context})
        return {"final": "Recovered through non-stream fallback."}


class TextDeltaStreamProvider:
    def stream(self, prompt: str, context: dict[str, Any]) -> Iterator[dict[str, Any]]:
        yield {"type": "content_delta", "delta": "Hello"}
        yield {"type": "content_delta", "delta": " there"}
        yield {
            "type": "final",
            "response": {
                "message": {"role": "assistant", "content": "Hello there", "tool_calls": []},
                "finish_reason": "completed",
                "raw": {},
            },
        }

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("Streaming test should not fall back to generate")


class ThinkingDeltaStreamProvider:
    def stream(self, prompt: str, context: dict[str, Any]) -> Iterator[dict[str, Any]]:
        yield {"type": "thinking_delta", "delta": "Checking ", "source": "reasoning_summary"}
        yield {"type": "thinking_delta", "delta": "files.", "source": "reasoning_summary"}
        yield {"type": "content_delta", "delta": "Done"}
        yield {
            "type": "final",
            "response": {
                "message": {"role": "assistant", "content": "Done", "tool_calls": []},
                "finish_reason": "completed",
                "raw": {},
            },
        }

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("Streaming test should not fall back to generate")


class ToolDeltaStreamProvider:
    def stream(self, prompt: str, context: dict[str, Any]) -> Iterator[dict[str, Any]]:
        yield {
            "type": "tool_call_delta",
            "index": 0,
            "id": "call_child",
            "name": "read_file",
            "parentToolUseId": "call_parent",
            "arguments_delta": "{\"path\":",
        }
        yield {"type": "tool_call_delta", "index": 0, "arguments_delta": "\"README.md\"}"}
        yield {
            "type": "final",
            "response": {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_child",
                            "type": "function",
                            "name": "read_file",
                            "arguments": {"path": "README.md"},
                            "parentToolUseId": "call_parent",
                        }
                    ],
                },
                "finish_reason": "tool_calls",
                "raw": {},
            },
        }

    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("Streaming test should not fall back to generate")


class UsageAwareProvider:
    def generate(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "final": "done",
            "raw": {
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                }
            },
        }


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
    return SimpleNamespace(server=server, store=store, events=events, orchestrator=orchestrator)


def _rpc(runtime: SimpleNamespace, method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = f"req_{len(runtime.events)}_{method}"
    envelope = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    response = runtime.server.handle_line(json.dumps(envelope, ensure_ascii=False))
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == request_id
    return response


def _call_result(response: dict[str, Any], key: str) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"][key]


def _open_session(runtime: SimpleNamespace, tmp_path: Any) -> dict[str, Any]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    workspace = _call_result(_rpc(runtime, "workspace.open", {"path": str(workspace_root)}), "workspace")
    return _call_result(
        _rpc(runtime, "session.create", {"workspaceId": workspace["id"], "title": "Provider Turn"}),
        "session",
    )


def test_provider_turn_records_simple_final_answer(tmp_path: Any) -> None:
    provider = ScriptedProvider([{"final": "Hello from model."}])
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "hello"}),
        "task",
    )
    turns = runtime.store.list_provider_turns(task["id"])

    assert task["status"] == "completed"
    assert task["resultSummary"] == "Hello from model."
    assert len(turns) == 1
    assert turns[0]["status"] == "completed"
    assert turns[0]["turn_decision"] == "final_answer"


def test_provider_tool_loop_records_multiple_turns(tmp_path: Any) -> None:
    def echo(params: dict[str, Any]) -> dict[str, Any]:
        return {"echo": params.get("text", "")}

    provider = ScriptedProvider(
        [
            {"message": "I will use echo.", "tool_calls": [{"id": "call_echo", "name": "echo", "arguments": {"text": "hi"}}]},
            {"final": "Echoed hi."},
        ]
    )
    runtime = _make_runtime(tmp_path, provider, {"echo": echo})
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "echo hi"}),
        "task",
    )

    assert task["status"] == "completed"
    assert task["resultSummary"] == "Echoed hi."
    assert len(runtime.store.list_provider_turns(task["id"])) == 2
    assert [event["type"] for event in runtime.events].count("tool.completed") == 1


def test_provider_failure_records_runtime_recovery_without_advisor(tmp_path: Any) -> None:
    provider = FailingProvider(RuntimeError("API rate limit exceeded"))
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "fail"}),
        "task",
    )
    turns = runtime.store.list_provider_turns(task["id"])

    assert task["status"] == "failed"
    assert turns[0]["status"] == "failed"
    assert turns[0]["failureRecovery"]["category"] == "rate_limit"
    assert turns[0]["failureRecovery"]["retryable"] is True
    assert any(event["type"] == "agent.decision.failure_recovery" for event in runtime.events)


def test_stream_fallback_turn_persists_transport(tmp_path: Any) -> None:
    provider = PartialStreamFailureProvider()
    runtime = _make_runtime(tmp_path, provider)
    runtime.store.update_config(
        {
            "config": {
                "provider": {
                    "mode": "openai-compatible",
                    "apiFormat": "openai-chat",
                    "streamingEnabled": True,
                    "model": "fake-stream",
                }
            }
        }
    )
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "stream then recover"}),
        "task",
    )
    turns = runtime.store.list_provider_turns(task["id"])

    assert task["status"] == "completed"
    assert len(provider.stream_calls) == 1
    assert len(provider.generate_calls) == 1
    assert turns[0]["response_transport"] == "fallback_non_stream"
    assert any(event["type"] == "provider.stream.fallback_non_stream" for event in runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"])


def test_stream_text_delta_emits_single_text_start(tmp_path: Any) -> None:
    provider = TextDeltaStreamProvider()
    runtime = _make_runtime(tmp_path, provider)
    task = runtime.store.create_task(session_id="sess_1", task_type="chat", goal="answer", plan=[])
    config = {
        "provider": {
            "mode": "openai-compatible",
            "apiFormat": "openai-chat",
            "streamingEnabled": True,
            "model": "fake-stream",
        }
    }
    runtime.store.update_config({"config": config})

    response = runtime.orchestrator._request_provider_response(
        session_id="sess_1",
        task={**task, "role": "root", "activeAssistantMessageId": "msg_1"},
        goal="answer",
        provider_context={
            "config": config,
            "messages": [{"role": "user", "content": "answer"}],
            "openai_tools": [],
            "step": 1,
        },
    )

    starts = [event for event in runtime.events if event["type"] == "content_start"]
    deltas = [event for event in runtime.events if event.get("yuanbao", {}).get("type") == "content_delta"]
    assert response["final_answer"] == "Hello there"
    assert len(starts) == 1
    assert [event["yuanbao"]["text"] for event in deltas] == ["Hello", " there"]


def test_stream_thinking_delta_emits_thinking_event(tmp_path: Any) -> None:
    provider = ThinkingDeltaStreamProvider()
    runtime = _make_runtime(tmp_path, provider)
    task = runtime.store.create_task(session_id="sess_1", task_type="chat", goal="think", plan=[])
    config = {
        "provider": {
            "mode": "openai-compatible",
            "apiFormat": "openai-responses",
            "streamingEnabled": True,
            "model": "fake-stream",
        }
    }

    response = runtime.orchestrator._request_provider_response(
        session_id="sess_1",
        task={**task, "role": "root", "activeAssistantMessageId": "msg_1"},
        goal="think then answer",
        provider_context={
            "config": config,
            "messages": [{"role": "user", "content": "think then answer"}],
            "openai_tools": [],
            "step": 1,
        },
    )

    thinking_events = [event for event in runtime.events if event["type"] == "thinking"]
    assert [event["payload"]["text"] for event in thinking_events] == ["Checking files."]
    assert thinking_events[0]["payload"]["_chatCompat"] is True
    assert thinking_events[0].get("yuanbao", {}).get("type") == "thinking"
    assert response["final_answer"] == "Done"


def test_stream_tool_call_delta_is_trace_only_not_chat_tool_input(tmp_path: Any) -> None:
    provider = ToolDeltaStreamProvider()
    runtime = _make_runtime(tmp_path, provider)
    task = runtime.store.create_task(session_id="sess_1", task_type="chat", goal="read", plan=[])
    config = {
        "provider": {
            "mode": "openai-compatible",
            "apiFormat": "openai-chat",
            "streamingEnabled": True,
            "model": "fake-stream",
        }
    }
    runtime.store.update_config({"config": config})

    response = runtime.orchestrator._request_provider_response(
        session_id="sess_1",
        task={**task, "role": "root", "activeAssistantMessageId": "msg_1"},
        goal="read README",
        provider_context={
            "config": config,
            "messages": [{"role": "user", "content": "read README"}],
            "openai_tools": [],
            "step": 1,
        },
    )

    assert response["tool_calls"][0]["parentToolUseId"] == "call_parent"
    assert not [event for event in runtime.events if event["type"] == "content_delta" and event["payload"].get("toolInput")]
    trace = runtime.store.list_trace_events({"taskId": task["id"]})["traceEvents"]
    assert [event["type"] for event in trace].count("provider.stream.tool_call_delta") == 2


def test_turn_persists_usage_from_raw_response(tmp_path: Any) -> None:
    provider = UsageAwareProvider()
    runtime = _make_runtime(tmp_path, provider)
    session = _open_session(runtime, tmp_path)

    task = _call_result(
        _rpc(runtime, "message.send", {"sessionId": session["id"], "content": "hello"}),
        "task",
    )

    turns = runtime.store.list_provider_turns(task["id"])
    assert json.loads(turns[0]["response_usage_json"]) == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    assert turns[0]["response_transport"] == "non_stream"
