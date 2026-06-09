from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from local_agent_runtime.event_bus import EventBus
from local_agent_runtime.orchestrator.service import Orchestrator
from local_agent_runtime.policy.guard import PolicyGuard
from local_agent_runtime.provider.adapter import ProviderAdapter
from local_agent_runtime.rpc.server import JsonRpcServer
from local_agent_runtime.services import CollaborationService, SubagentService
from local_agent_runtime.services.worker_runner import WorkerRunner
from local_agent_runtime.store.sqlite_store import SQLiteStore
from local_agent_runtime.tools import build_builtin_tools
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
        self.requests.append({**kwargs, "json": json.loads(kwargs["body"].decode("utf-8"))})
        if not self._responses:
            raise AssertionError("Fake provider received more POST requests than scripted")
        return 200, json.dumps(self._responses.pop(0), ensure_ascii=False).encode("utf-8")


def _chat_response(message: dict[str, Any], *, finish_reason: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl_fake",
        "model": "fake-chat",
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": {"total_tokens": 17},
    }


def _task_tool_call_response() -> dict[str, Any]:
    return _chat_response(
        {
            "role": "assistant",
            "content": "I will ask two focused agents to inspect structure and quality.",
            "tool_calls": [
                {
                    "id": "call_structure_agent",
                    "type": "function",
                    "function": {
                        "name": "task",
                        "arguments": json.dumps(
                            {
                                "prompt": "Inspect source structure and summarize key modules.",
                                "title": "Inspect structure",
                                "agentType": "structure-agent",
                                "budget": {"maxToolCalls": 3},
                            },
                            ensure_ascii=False,
                        ),
                    },
                },
                {
                    "id": "call_quality_agent",
                    "type": "function",
                    "function": {
                        "name": "task",
                        "arguments": json.dumps(
                            {
                                "prompt": "Inspect tests and summarize quality risks.",
                                "title": "Inspect quality",
                                "agentType": "quality-agent",
                                "budget": {"maxToolCalls": 3},
                            },
                            ensure_ascii=False,
                        ),
                    },
                },
            ],
        },
        finish_reason="tool_calls",
    )


def _final_response() -> dict[str, Any]:
    return _chat_response(
        {
            "role": "assistant",
            "content": (
                "Project health report: structure-agent found runtime/src modules; "
                "quality-agent found provider and e2e test coverage signals."
            ),
        },
        finish_reason="stop",
    )


def _make_runtime(tmp_path: Path, provider: Any) -> SimpleNamespace:
    event_bus = EventBus()
    store = SQLiteStore(str(tmp_path / "runtime.sqlite3"))
    store.update_config({"config": {"provider": FAKE_PROVIDER_CONFIG}})
    config = store.get_config({})["config"]
    policy_guard = PolicyGuard(approval_mode=config["policy"]["approvalMode"])
    collaboration = CollaborationService(store, event_bus)
    child_runs: list[dict[str, Any]] = []

    def child_executor(context: Any) -> dict[str, Any]:
        child_runs.append(
            {
                "agentType": context.request.agent_type,
                "title": context.request.title,
                "prompt": context.request.prompt,
            }
        )
        summary = f"{context.request.agent_type} completed {context.request.title}."
        return {
            "summary": summary,
            "executionMode": "inline-test",
            "result": {"summary": summary, "agentType": context.request.agent_type},
        }

    runner = WorkerRunner(collaboration, executor=child_executor)
    subagent_service = SubagentService(store, collaboration, runner=runner)
    tool_registry = ToolRegistry(
        build_builtin_tools(
            policy_guard=policy_guard,
            store=store,
            subagent_service=subagent_service,
        )
    )
    orchestrator = Orchestrator(
        store=store,
        event_bus=event_bus,
        tool_registry=tool_registry,
        provider=provider,
    )
    orchestrator._subagent_service = subagent_service  # noqa: SLF001
    orchestrator._worker_runner = runner  # noqa: SLF001
    server = JsonRpcServer(orchestrator=orchestrator, store=store, event_bus=event_bus)
    events: list[dict[str, Any]] = []
    event_bus.subscribe(lambda event: events.append(event_bus.as_payload(event)))
    return SimpleNamespace(server=server, store=store, events=events, child_runs=child_runs)


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


def test_react_task_tool_delegates_child_agents_and_synthesizes_report(tmp_path: Path) -> None:
    provider = ScriptedHttpPostProvider([_task_tool_call_response(), _final_response()])
    runtime = _make_runtime(tmp_path, provider)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    try:
        workspace = _rpc(runtime, "workspace.open", {"path": str(workspace_root)})["workspace"]
        session = _rpc(
            runtime,
            "session.create",
            {"workspaceId": workspace["id"], "title": "multi-agent health report"},
        )["session"]
        task = _rpc(
            runtime,
            "message.send",
            {"sessionId": session["id"], "content": "Create a project health report with two agents."},
        )["task"]
        child_tasks = runtime.store.list_collaboration_tasks(
            {"sessionId": session["id"], "parentTaskId": task["id"]}
        )["tasks"]
        trace = _rpc(runtime, "trace.list", {"taskId": task["id"], "limit": 200})["traceEvents"]
    finally:
        runtime.store.close()

    assert task["status"] == "completed"
    assert "Project health report" in task["resultSummary"]
    assert {run["agentType"] for run in runtime.child_runs} == {"structure-agent", "quality-agent"}
    assert {child["metadata"]["agentType"] for child in child_tasks} == {"structure-agent", "quality-agent"}
    assert {child["status"] for child in child_tasks} == {"completed"}

    event_types = [event["type"] for event in runtime.events]
    assert event_types.count("collab.task.created") == 2
    assert event_types.count("collab.task.completed") == 2

    trace_types = [event["type"] for event in trace]
    assert "provider.request" in trace_types
    assert "tool.started" in trace_types
    assert "tool.completed" in trace_types
    assert "task.completed" in trace_types
    assert provider.requests[0]["json"]["tools"]
    assert {tool["function"]["name"] for tool in provider.requests[0]["json"]["tools"]} >= {"task"}
    followup_tools = provider.requests[1]["json"].get("tools") or []
    followup_tool_names = {tool["function"]["name"] for tool in followup_tools}
    assert {"read_file", "run_command", "git_status", "git_diff"}.issubset(followup_tool_names)
