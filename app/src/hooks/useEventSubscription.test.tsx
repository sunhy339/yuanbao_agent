import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentEventEnvelope, SessionRecord, TaskRecord, TraceEventRecord } from "@shared";

const runtimeMocks = vi.hoisted(() => ({
  subscribeEvents: vi.fn(),
  handler: null as ((event: AgentEventEnvelope) => void) | null,
}));

const commandLogSnapshots: Record<string, any>[] = [];
const traceSnapshots: TraceEventRecord[][] = [];

vi.mock("../lib/runtimeClient", () => ({
  RuntimeClient: vi.fn(function RuntimeClient() {
    return {
      subscribeEvents: runtimeMocks.subscribeEvents.mockImplementation(
        async (handler: (event: AgentEventEnvelope) => void) => {
          runtimeMocks.handler = handler;
          return vi.fn();
        },
      ),
    };
  }),
}));

import { useEventSubscription } from "./useEventSubscription";
import type { ChatMessageView } from "../state/chatMessages";

function Harness() {
  const [messages, setChatMessages] = useState<ChatMessageView[]>([]);
  const [, setEvents] = useState<any[]>([]);
  const [, setSession] = useState<SessionRecord | null>(null);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [, setTask] = useState<TaskRecord | null>(null);
  const [, setActiveTaskId] = useState<string | null>(null);
  const [, setTaskHistory] = useState<TaskRecord[]>([]);
  const [, setTraceEventsState] = useState<TraceEventRecord[]>([]);
  const [, setCommandLogCacheByIdState] = useState<Record<string, any>>({});
  const [, setTraceError] = useState<string | null>(null);
  const [, setPatchCacheById] = useState<Record<string, any>>({});
  const [, setPatchBusyId] = useState<string | null>(null);
  const [, setApprovalBusyId] = useState<string | null>(null);
  const sessionActiveTaskMapRef = useRef(new Map<string, string>());
  const childTaskIdsRef = useRef(new Set<string>());
  const pendingAssistantTokenEventsRef = useRef<AgentEventEnvelope[]>([]);
  const assistantTokenFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEventSubscription({
    setActiveTaskForSession: vi.fn(),
    setEvents,
    setSession,
    setSessions,
    setTask,
    setActiveTaskId,
    setTaskHistory,
    setChatMessages,
    setTraceEvents: (value) => {
      setTraceEventsState((current) => {
        const next = typeof value === "function" ? value(current) : value;
        traceSnapshots.push(next);
        return next;
      });
    },
    setCommandLogCacheById: (value) => {
      setCommandLogCacheByIdState((current) => {
        const next = typeof value === "function" ? value(current) : value;
        commandLogSnapshots.push(next);
        return next;
      });
    },
    setTraceError,
    setPatchCacheById,
    setPatchBusyId,
    setApprovalBusyId,
    setError: vi.fn(),
    sessionActiveTaskMapRef,
    childTaskIdsRef,
    pendingAssistantTokenEventsRef,
    assistantTokenFlushTimerRef,
  });

  return (
    <div>
      {messages.map((message) => (
        <p
          key={message.id}
          data-kind={String(message.metadata?.kind ?? "")}
          data-status={String(message.status ?? "")}
          data-semantic-parent={String(message.metadata?.toolSemanticParentId ?? "")}
          data-semantic-label={String(message.metadata?.toolSemanticParentLabel ?? "")}
          data-operation-id={String(message.metadata?.toolOperationId ?? "")}
          data-operation-label={String(message.metadata?.toolOperationLabel ?? "")}
          data-source={String(message.metadata?.source ?? "")}
          data-tool-use-id={String(message.metadata?.toolUseId ?? "")}
          data-input-text={String(message.metadata?.inputText ?? "")}
        >
          {message.content}
          {typeof message.metadata?.resultText === "string" ? message.metadata.resultText : ""}
        </p>
      ))}
      {sessions.map((session) => (
        <span key={session.id} data-testid="session-title">{session.title}</span>
      ))}
    </div>
  );
}

describe("useEventSubscription", () => {
  beforeEach(() => {
    commandLogSnapshots.length = 0;
    traceSnapshots.length = 0;
    runtimeMocks.subscribeEvents.mockImplementation(
      async (handler: (event: AgentEventEnvelope) => void) => {
        runtimeMocks.handler = handler;
        return vi.fn();
      },
    );
  });

  afterEach(() => {
    cleanup();
    runtimeMocks.subscribeEvents.mockClear();
    runtimeMocks.handler = null;
  });

  it("keeps legacy assistant_progress events out of the chat transcript", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_progress",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "assistant_progress",
        ts: 10,
        visibility: "chat",
        payload: {
          text: "正在整理上下文",
          phase: "context_prepare",
          toolSemanticParentId: "phase:context_read",
          toolSemanticParentLabel: "读取上下文",
        },
      } as unknown as AgentEventEnvelope);
    });

    expect(screen.queryByText("正在整理上下文")).toBeNull();
  });

  it("preserves the single flat server message on the live trace cache", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_delta",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "content_delta",
        ts: 10,
        seq: 3,
        visibility: "chat",
        payload: { text: "hello" },
        yuanbao: { type: "content_delta", text: "hello" },
      });
    });

    await waitFor(() => expect(traceSnapshots.at(-1)?.[0]?.yuanbao).toEqual({
      type: "content_delta",
      text: "hello",
    }));
    expect("hahaCc" in (traceSnapshots.at(-1)?.[0] ?? {})).toBe(false);
  });

  it("renders chat-compat message.delta as the canonical live text stream", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_msg_delta_1",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "message.delta",
        ts: 10,
        seq: 10,
        visibility: "chat",
        payload: {
          messageId: "msg_1",
          delta: "hello ",
          _chatCompat: true,
        },
        yuanbao: { type: "content_delta", text: "hello " },
      });
      runtimeMocks.handler?.({
        eventId: "evt_msg_delta_2",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "message.delta",
        ts: 11,
        seq: 11,
        visibility: "chat",
        payload: {
          messageId: "msg_1",
          delta: "world",
          _chatCompat: true,
        },
        yuanbao: { type: "content_delta", text: "world" },
      });
      runtimeMocks.handler?.({
        eventId: "evt_legacy_token",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "assistant.token",
        ts: 12,
        seq: 12,
        visibility: "chat",
        payload: {
          messageId: "msg_1",
          delta: " ignored",
          _chatCompat: true,
        },
      });
    });

    expect(screen.getByText("hello world")).not.toBeNull();
    expect(screen.queryByText(/ignored/)).toBeNull();
  });

  it("renders a new assistant text block after a live tool boundary", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_text_start_0",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "content_start",
        ts: 10,
        seq: 10,
        visibility: "chat",
        payload: {
          blockType: "text",
          messageId: "msg_1",
          contentBlockId: "msg_1:text:0",
          blockIndex: 0,
          _chatCompat: true,
        },
      } as AgentEventEnvelope);
      runtimeMocks.handler?.({
        eventId: "evt_text_delta_0",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "message.delta",
        ts: 11,
        seq: 11,
        visibility: "chat",
        payload: {
          messageId: "msg_1",
          contentBlockId: "msg_1:text:0",
          blockIndex: 0,
          delta: "先读项目。",
          _chatCompat: true,
        },
      } as AgentEventEnvelope);
      runtimeMocks.handler?.({
        eventId: "evt_tool_start",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "content_start",
        ts: 12,
        seq: 12,
        visibility: "chat",
        payload: {
          blockType: "tool_use",
          toolName: "read_file",
          toolUseId: "tool_read",
          target: "README.md",
        },
      } as AgentEventEnvelope);
      runtimeMocks.handler?.({
        eventId: "evt_tool_result",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool_result",
        ts: 13,
        seq: 13,
        visibility: "chat",
        payload: {
          toolUseId: "tool_read",
          toolName: "read_file",
          target: "README.md",
          resultSummary: "read README.md",
          _chatCompat: true,
        },
      } as AgentEventEnvelope);
      runtimeMocks.handler?.({
        eventId: "evt_text_start_1",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "content_start",
        ts: 14,
        seq: 14,
        visibility: "chat",
        payload: {
          blockType: "text",
          messageId: "msg_1",
          contentBlockId: "msg_1:text:1",
          blockIndex: 1,
          _chatCompat: true,
        },
      } as AgentEventEnvelope);
      runtimeMocks.handler?.({
        eventId: "evt_text_delta_1",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "message.delta",
        ts: 15,
        seq: 15,
        visibility: "chat",
        payload: {
          messageId: "msg_1",
          contentBlockId: "msg_1:text:1",
          blockIndex: 1,
          delta: "然后继续实现。",
          _chatCompat: true,
        },
      } as AgentEventEnvelope);
    });

    const rows = screen.getAllByText(/先读项目|README\.md|然后继续实现/);
    expect(rows.map((row) => row.textContent)).toEqual([
      "先读项目。",
      "README.mdread README.md",
      "然后继续实现。",
    ]);
    expect(rows.map((row) => row.getAttribute("data-kind"))).toEqual([
      "",
      "tool_activity",
      "",
    ]);
  });

  it("renders envelope tool output deltas into the matching tool row", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_tool_start",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "content_start",
        ts: 10,
        seq: 10,
        visibility: "chat",
        payload: {
          blockType: "tool_use",
          toolName: "read_file",
          toolUseId: "call_read",
          target: "README.md",
        },
        yuanbao: {
          type: "content_start",
          blockType: "tool_use",
          toolName: "read_file",
          toolUseId: "call_read",
        } as any,
      });
      runtimeMocks.handler?.({
        eventId: "evt_tool_output",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "content_delta",
        ts: 11,
        seq: 11,
        visibility: "chat",
        payload: {
          toolUseId: "call_read",
          toolName: "read_file",
          target: "README.md",
          toolOutput: "read README.md\n",
          outputStream: "result_preview",
        },
      });
    });

    const row = screen.getByText(/read README.md/);
    expect(row.getAttribute("data-kind")).toBe("tool_use");
    expect(row.getAttribute("data-tool-use-id")).toBe("call_read");
  });

  it("renders thinking events and preserves their source metadata", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_thinking",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "thinking",
        ts: 10,
        visibility: "chat",
        payload: {
          text: "先确认相关文件。",
          source: "non_stream_thought_summary",
        },
      });
    });

    const row = screen.getByText("先确认相关文件。");
    expect(row.getAttribute("data-kind")).toBe("assistant_thinking");
    expect(row.getAttribute("data-source")).toBe("non_stream_thought_summary");
  });

  it("updates sessions from session.created without adding chat output", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_session_created",
        sessionId: "sess_created",
        taskId: "sess_created",
        type: "session.created",
        ts: 10,
        seq: 2,
        visibility: "panel",
        payload: {
          session: {
            id: "sess_created",
            workspaceId: "workspace_1",
            title: "Created from backend",
            status: "active",
            createdAt: 10,
            updatedAt: 10,
          },
        },
      });
    });

    expect((await screen.findByTestId("session-title")).textContent).toBe("Created from backend");
    expect(screen.queryByText("sess_created")).toBeNull();
  });

  it("keeps runtime status events out of the chat transcript", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_status_thinking",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "status",
        ts: 10,
        visibility: "chat",
        payload: {
          state: "thinking",
          verb: "model",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_status_streaming",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "status",
        ts: 11,
        visibility: "chat",
        payload: {
          state: "streaming",
          verb: "model",
        },
      });
    });

    expect(document.querySelector('[data-kind="assistant_thinking"]')).toBeNull();
  });

  it("keeps provider thinking on either side of a live tool boundary", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_thinking_1",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "thinking",
        ts: 10,
        visibility: "chat",
        payload: {
          text: "Inspect first. ",
          source: "provider_reasoning_delta",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_tool_start",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool.started",
        ts: 11,
        visibility: "chat",
        payload: {
          toolCallId: "tool_read",
          toolName: "read_file",
          arguments: { path: "snake_game/README.md" },
          target: "snake_game/README.md",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_tool_done",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool.completed",
        ts: 12,
        visibility: "chat",
        payload: {
          toolCallId: "tool_read",
          toolName: "read_file",
          resultSummary: "read snake_game/README.md",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_thinking_2",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "thinking",
        ts: 13,
        visibility: "chat",
        payload: {
          text: "Then summarize.",
          source: "provider_reasoning_delta",
        },
      });
    });

    const rows = screen.getAllByText(/Inspect first|snake_game\/README\.md|Then summarize/);
    expect(rows.map((row) => row.getAttribute("data-kind"))).toEqual([
      "assistant_thinking",
      "tool_activity",
      "assistant_thinking",
    ]);
    expect(rows[0].textContent).toContain("Inspect first.");
    expect(rows[2].textContent).toContain("Then summarize.");
  });

  it("renders system notifications as system transcript nodes", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_system",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "system_notification",
        ts: 10,
        visibility: "chat",
        payload: {
          title: "Provider notice",
          summary: "Switched to fallback model",
          model: "fallback-model",
        },
      });
    });

    const row = screen.getByText("Switched to fallback model");
    expect(row.getAttribute("data-kind")).toBe("system");
  });

  it("renders live agent task groups as special transcript nodes", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_agent_group",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "agent_task_group",
        ts: 10,
        visibility: "chat",
        payload: {
          title: "派遣了 2 个代理",
          summary: "2 个完成",
          status: "completed",
          agentTasks: [
            { id: "ctask_1", title: "Analyze codebase", status: "completed" },
            { id: "ctask_2", title: "Verify results", status: "completed" },
          ],
        },
      });
    });

    const row = screen.getByText("2 个完成");
    expect(row.getAttribute("data-kind")).toBe("agent_task_group");
    expect(row.getAttribute("data-status")).toBe("completed");
  });

  it("turns resolved permission_request events into completed approval cards", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_permission_resolved",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "permission_request",
        ts: 10,
        visibility: "chat",
        payload: {
          requestId: "approval_1",
          toolName: "write_file",
          input: { path: "src/new.ts", risk: "writes file" },
          preview: [{ label: "文件", value: "src/new.ts" }],
          filesChanged: 1,
          changedPaths: ["src/new.ts"],
          diffText: "--- /dev/null\n+++ b/src/new.ts\n",
          resolved: true,
          decision: "approved",
        },
      });
    });

    const row = screen.getByText(/src\/new\.ts/);
    expect(row.getAttribute("data-kind")).toBe("permission_request");
    expect(row.getAttribute("data-status")).toBe("completed");
  });

  it("does not render internal approval aliases as permission cards", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_review_permission",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "permission_request",
        ts: 10,
        visibility: "chat",
        payload: {
          requestId: "approval_review",
          kind: "completion_review",
          input: { summary: "internal completion review" },
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_advisor_resolved",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "approval.resolved",
        ts: 11,
        visibility: "chat",
        payload: {
          approvalId: "approval_advisor",
          approvalKind: "advisor_tool",
          request: { kind: "advisor_tool", summary: "internal advisor" },
        },
      });
    });

    expect(screen.queryByText(/internal completion review/)).not.toBeInTheDocument();
    expect(screen.queryByText(/internal advisor/)).not.toBeInTheDocument();
    expect(document.querySelector('[data-kind="permission_request"]')).toBeNull();
  });

  it("streams command.output events into matching tool messages when toolUseId is present", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_start",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "content_start",
        ts: 10,
        visibility: "chat",
        payload: {
          blockType: "tool_use",
          toolUseId: "call_command",
          toolName: "run_command",
          toolSemanticParentId: "phase:verification",
          toolSemanticParentLabel: "验证",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_output",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "command.output",
        ts: 11,
        payload: {
          commandId: "cmd_1",
          toolUseId: "call_command",
          toolName: "run_command",
          toolSemanticParentId: "phase:verification",
          toolSemanticParentLabel: "验证",
          stream: "stdout",
          chunk: "alpha\n",
        },
      });
    });

    const row = screen.getByText(/stdout\s+alpha/);
    expect(row.getAttribute("data-kind")).toBe("tool_use");
    expect(row.getAttribute("data-semantic-parent")).toBe("phase:verification");
    expect(row.getAttribute("data-semantic-label")).toBe("验证");
  });

  it("renders raw tool.started events as running tool rows", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_tool_started_raw",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool.started",
        ts: 10,
        visibility: "chat",
        payload: {
          toolCallId: "call_read",
          toolName: "read_file",
          arguments: { path: "src/app.ts" },
          target: "src/app.ts",
          inputSummary: "read src/app.ts",
          toolOperationId: "context:path:src/app.ts",
          toolOperationLabel: "读取上下文",
          toolSemanticParentId: "phase:context",
          toolSemanticParentLabel: "Context",
        },
      });
    });

    const row = document.querySelector('[data-tool-use-id="call_read"]') as HTMLElement;
    expect(row).toBeTruthy();
    expect(row.getAttribute("data-kind")).toBe("tool_use");
    expect(row.getAttribute("data-status")).toBe("streaming");
    expect(row.getAttribute("data-semantic-parent")).toBe("phase:context");
    expect(row.getAttribute("data-operation-id")).toBe("context:path:src/app.ts");
    expect(row.getAttribute("data-operation-label")).toBe("读取上下文");
    expect(row.getAttribute("data-input-text")).toContain("src/app.ts");
  });

  it("ignores trace-visible raw tool lifecycle events in chat", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_tool_started_trace",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool.started",
        ts: 10,
        visibility: "trace",
        payload: {
          toolCallId: "call_read_trace",
          toolName: "read_file",
          arguments: { path: "src/app.ts" },
          target: "src/app.ts",
        },
      });
    });

    expect(document.querySelector('[data-tool-use-id="call_read_trace"]')).toBeNull();
  });

  it("renders raw blocked tool lifecycle events as terminal tool rows", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_tool_blocked_raw",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool.blocked",
        ts: 10,
        visibility: "chat",
        payload: {
          toolCallId: "call_write",
          toolName: "write_file",
          target: "src/app.ts",
          inputSummary: "write src/app.ts",
          resultSummary: "blocked by policy",
          reason: "blocked by policy",
          failureKind: "permission_denied",
          recoveryHint: "request approval",
          durationMs: 17,
        },
      });
    });

    const row = screen.getByText(/blocked by policy/);
    expect(row.getAttribute("data-kind")).toBe("tool_result");
    expect(row.getAttribute("data-status")).toBe("blocked");
    expect(row.getAttribute("data-tool-use-id")).toBe("call_write");
  });

  it("streams raw tool.progress and tool.output events into matching tool rows", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_tool_progress_raw",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool.progress",
        ts: 10,
        visibility: "chat",
        payload: {
          toolCallId: "call_search",
          toolName: "search_files",
          target: "src",
          toolOperationId: "context:search:needle",
          toolOperationLabel: "搜索",
          message: "searching src\n",
          outputStream: "activity",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_tool_output_raw",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool.output",
        ts: 11,
        visibility: "chat",
        payload: {
          toolUseId: "call_search",
          toolName: "search_files",
          target: "src",
          toolOperationId: "context:search:needle",
          toolOperationLabel: "搜索",
          chunk: "2 matches\n",
          outputStream: "result_preview",
        },
      });
    });

    const row = screen.getByText(/searching src/);
    expect(row.textContent).toMatch(/2 matches/);
    expect(row.getAttribute("data-kind")).toBe("tool_use");
    expect(row.getAttribute("data-status")).toBe("streaming");
    expect(row.getAttribute("data-tool-use-id")).toBe("call_search");
    expect(row.getAttribute("data-operation-id")).toBe("context:search:needle");
  });

  it("skips raw task/provider/tool progress json in live chat-compatible streams", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());
    const rawProgress = JSON.stringify({
      taskId: "task_1",
      provider: "yuanbao",
      tool_progress: { toolCallId: "call_search", status: "running" },
    });

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_raw_message_delta",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "message.delta",
        ts: 10,
        visibility: "chat",
        payload: {
          messageId: "msg_final",
          delta: rawProgress,
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_tool_progress_json",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "tool.progress",
        ts: 11,
        visibility: "chat",
        payload: {
          toolUseId: "call_search",
          toolName: "search_files",
          message: rawProgress,
          outputStream: "activity",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_visible_delta",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "message.delta",
        ts: 12,
        visibility: "chat",
        payload: {
          messageId: "msg_final",
          delta: "Final answer.",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_complete",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "message_complete",
        ts: 13,
        visibility: "chat",
        payload: {
          messageId: "msg_final",
          content: "Final answer.",
        },
      });
    });

    const bodyText = document.body.textContent ?? "";
    expect(bodyText).toContain("Final answer.");
    expect(bodyText.match(/Final answer\./g)).toHaveLength(1);
    expect(bodyText).not.toContain("tool_progress");
    expect(bodyText).not.toContain("\"provider\"");
  });

  it("creates a visible tool row from command.started before output arrives", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_command_started",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "command.started",
        ts: 10,
        visibility: "chat",
        payload: {
          commandId: "cmd_1",
          toolUseId: "call_command",
          toolName: "run_command",
          command: "npm run dev",
          target: "npm run dev",
          inputSummary: "npm run dev",
          status: "running",
          background: true,
          toolOperationId: "verification",
          toolOperationLabel: "验证",
          toolSemanticParentId: "phase:command",
          toolSemanticParentLabel: "命令",
        },
      });
    });

    const row = document.querySelector('[data-tool-use-id="call_command"]') as HTMLElement;
    expect(row).toBeTruthy();
    expect(row.getAttribute("data-kind")).toBe("tool_use");
    expect(row.getAttribute("data-status")).toBe("streaming");
    expect(row.getAttribute("data-semantic-parent")).toBe("phase:command");
    expect(row.getAttribute("data-semantic-label")).toBe("命令");
    expect(row.getAttribute("data-operation-id")).toBe("verification");
  });

  it("completes background command tool messages on command lifecycle terminal events", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_start",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "content_start",
        ts: 10,
        visibility: "chat",
        payload: {
          blockType: "tool_use",
          toolUseId: "call_command",
          toolName: "run_command",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_output",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "command.output",
        ts: 11,
        payload: {
          commandId: "cmd_1",
          toolUseId: "call_command",
          toolName: "run_command",
          stream: "stdout",
          chunk: "alpha\n",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_done",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "command.completed",
        ts: 12,
        payload: {
          commandId: "cmd_1",
          toolUseId: "call_command",
          toolName: "run_command",
          status: "completed",
          exitCode: 0,
          background: true,
        },
      });
    });

    const row = screen.getByText(/命令已完成：exit 0/);
    expect(row.getAttribute("data-kind")).toBe("tool_activity");
    expect(row.getAttribute("data-status")).toBe("completed");
  });

  it("keeps command log cache in sync with command lifecycle output metadata", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_command_started_cache",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "command.started",
        ts: 10,
        visibility: "trace",
        payload: {
          commandId: "cmd_1",
          toolUseId: "call_command",
          toolName: "run_command",
          command: "npm test",
          cwd: "app",
          shell: "powershell",
          target: "npm test",
          inputSummary: "npm test",
          status: "running",
          background: true,
          toolGroupId: "tgrp_1",
          toolIndex: 0,
          toolTotal: 1,
          toolCategory: "verification",
          toolSemanticParentId: "phase:verification",
          toolSemanticParentLabel: "验证",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_command_output_cache",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "command.output",
        ts: 11,
        visibility: "trace",
        payload: {
          commandId: "cmd_1",
          toolUseId: "call_command",
          target: "npm test",
          inputSummary: "npm test",
          stream: "stdout",
          chunk: "alpha\n",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_command_completed_cache",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "command.completed",
        ts: 12,
        visibility: "trace",
        payload: {
          commandId: "cmd_1",
          toolUseId: "call_command",
          status: "completed",
          exitCode: 0,
          durationMs: 2,
          stdoutPath: "cmd_1_stdout.log",
          background: true,
        },
      });
    });

    expect(commandLogSnapshots.at(-1)?.cmd_1).toMatchObject({
      id: "cmd_1",
      taskId: "task_1",
      toolUseId: "call_command",
      toolGroupId: "tgrp_1",
      toolIndex: 0,
      toolTotal: 1,
      toolCategory: "verification",
      toolSemanticParentId: "phase:verification",
      toolSemanticParentLabel: "验证",
      target: "npm test",
      inputSummary: "npm test",
      command: "npm test",
      cwd: "app",
      shell: "powershell",
      background: true,
      status: "completed",
      exitCode: 0,
      durationMs: 2,
      stdout: "alpha\n",
      stdoutPath: "cmd_1_stdout.log",
    });
  });

  it("marks background command tool messages as cancelled on command.cancelled", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_start_cancel",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "content_start",
        ts: 10,
        visibility: "chat",
        payload: {
          blockType: "tool_use",
          toolUseId: "call_command",
          toolName: "run_command",
        },
      });
      runtimeMocks.handler?.({
        eventId: "evt_cancel",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "command.cancelled",
        ts: 12,
        visibility: "chat",
        payload: {
          commandId: "cmd_1",
          toolUseId: "call_command",
          toolName: "run_command",
          status: "cancelled",
          background: true,
        },
      });
    });

    const row = screen.getByText(/命令已取消：cancelled/);
    expect(row.getAttribute("data-kind")).toBe("tool_activity");
    expect(row.getAttribute("data-status")).toBe("cancelled");
  });

  it("renders live flat team_update as an agent group panel", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_team_update",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "collab.task.created",
        ts: 10,
        visibility: "chat",
        payload: { rawJson: { should: "not render" } },
        yuanbao: {
          type: "team_update",
          teamName: "swarm",
          members: [
            {
              agentId: "planner-1",
              role: "planner",
              status: "running",
              currentTask: "Inspect backend flow",
            },
          ],
        },
      } as unknown as AgentEventEnvelope);
    });

    const row = screen.getByText("1 member");
    expect(row.getAttribute("data-kind")).toBe("agent_task_group");
    expect(row.getAttribute("data-status")).toBe("streaming");
    expect(screen.queryByText(/rawJson/)).toBeNull();
  });

  it("keeps live flat task_update out of the chat transcript", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_task_update",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "task.updated",
        ts: 10,
        visibility: "chat",
        payload: { rawJson: { should: "not render" } },
        yuanbao: {
          type: "task_update",
          taskId: "task_1",
          status: "running",
          progress: "Inspect current workflow",
        },
      });
    });

    expect(screen.queryByText("Inspect current workflow")).toBeNull();
    expect(screen.queryByText(/rawJson/)).toBeNull();
  });

  it("keeps live flat task progress notifications out of the chat transcript", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_task_progress",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "system_notification",
        ts: 10,
        visibility: "chat",
        payload: { summary: "Panel-only task progress" },
        yuanbao: {
          type: "system_notification",
          subtype: "task_progress",
          message: "Panel-only task progress",
          data: { summary: "Panel-only task progress" },
        },
      });
    });

    expect(screen.queryByText("Panel-only task progress")).toBeNull();
  });

  it("keeps legacy live task and plan state events out of the chat transcript", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    act(() => {
      runtimeMocks.handler?.({
        eventId: "evt_legacy_task_summary",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "task_summary",
        ts: 10,
        visibility: "chat",
        payload: { summary: "Legacy task summary should stay out" },
      });
      runtimeMocks.handler?.({
        eventId: "evt_legacy_plan_update",
        sessionId: "sess_1",
        taskId: "task_1",
        type: "plan_update",
        ts: 11,
        visibility: "chat",
        payload: { summary: "Legacy plan update should stay out" },
      });
    });

    expect(screen.queryByText("Legacy task summary should stay out")).toBeNull();
    expect(screen.queryByText("Legacy plan update should stay out")).toBeNull();
  });

  it("drops pending and late chat output after task.cancelled", async () => {
    render(<Harness />);
    await waitFor(() => expect(runtimeMocks.subscribeEvents).toHaveBeenCalled());

    vi.useFakeTimers();
    try {
      act(() => {
        runtimeMocks.handler?.({
          eventId: "evt_token_pending",
          sessionId: "sess_1",
          taskId: "task_cancelled",
          type: "assistant.token",
          ts: 10,
          visibility: "chat",
          payload: {
            delta: "late pending assistant text",
          },
        });
        runtimeMocks.handler?.({
          eventId: "evt_task_cancelled",
          sessionId: "sess_1",
          taskId: "task_cancelled",
          type: "task.cancelled",
          ts: 11,
          visibility: "chat",
          payload: {
            status: "cancelled",
          },
        });
        runtimeMocks.handler?.({
          eventId: "evt_late_message",
          sessionId: "sess_1",
          taskId: "task_cancelled",
          type: "message.delta",
          ts: 12,
          visibility: "chat",
          payload: {
            messageId: "msg_late",
            delta: "late message delta",
          },
        });
        runtimeMocks.handler?.({
          eventId: "evt_late_content",
          sessionId: "sess_1",
          taskId: "task_cancelled",
          type: "content_delta",
          ts: 13,
          visibility: "chat",
          payload: {
            text: "late content delta",
          },
        });
        runtimeMocks.handler?.({
          eventId: "evt_late_tool",
          sessionId: "sess_1",
          taskId: "task_cancelled",
          type: "tool_result",
          ts: 14,
          visibility: "chat",
          payload: {
            toolUseId: "call_late",
            toolName: "read_file",
            content: "late tool result",
          },
        });
      });

      act(() => {
        vi.advanceTimersByTime(50);
      });
    } finally {
      vi.useRealTimers();
    }

    expect(screen.queryByText(/late pending assistant text/)).toBeNull();
    expect(screen.queryByText(/late message delta/)).toBeNull();
    expect(screen.queryByText(/late content delta/)).toBeNull();
    expect(screen.queryByText(/late tool result/)).toBeNull();
  });
});
