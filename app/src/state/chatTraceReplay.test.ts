import { describe, expect, it } from "vitest";
import type { TraceEventRecord } from "@shared";
import { getVisibleChatMessages, type ChatMessageView } from "./chatMessages";
import { replayTraceEventsToChatMessages } from "./chatTraceReplay";

function trace(
  id: string,
  type: string,
  payload: unknown,
  sequence: number,
  visibility: TraceEventRecord["visibility"] | undefined = undefined,
  taskId = "task_1",
): TraceEventRecord {
  return {
    id,
    sessionId: "sess_1",
    taskId,
    type,
    source: type.split(".")[0] || "runtime",
    payload,
    createdAt: sequence,
    sequence,
    visibility,
  };
}

function flatToolFrames(
  toolUseId: string,
  toolName: string,
  sequence: number,
  target: string,
  resultSummary: string,
): TraceEventRecord[] {
  return [
    trace(`evt_${toolUseId}_content_start`, "content_start", {
      blockType: "tool_use",
      toolUseId,
      toolName,
      target,
      inputSummary: target,
      _chatCompat: true,
      _bridge: { persistTraceMirror: true },
    }, sequence, "chat"),
    trace(`evt_${toolUseId}_tool_use_complete`, "tool_use_complete", {
      toolUseId,
      toolName,
      input: { target },
      target,
      inputSummary: target,
      _chatCompat: true,
      _bridge: { persistTraceMirror: true },
    }, sequence + 1, "chat"),
    trace(`evt_${toolUseId}_tool_result`, "tool_result", {
      toolUseId,
      toolName,
      content: { status: "completed" },
      target,
      inputSummary: target,
      resultSummary,
      _chatCompat: true,
      _bridge: { persistTraceMirror: true },
    }, sequence + 2, "chat"),
  ];
}

describe("chat trace replay", () => {
  it("restores tool activity blocks from persisted haha-style flat frames", () => {
    const current: ChatMessageView[] = [
      {
        id: "stored_user",
        sessionId: "sess_1",
        taskId: "task_1",
        role: "user",
        content: "Inspect the project",
        createdAt: 1,
        updatedAt: 1,
      },
    ];
    const traces = [
      trace("evt_content_start", "content_start", {
        blockType: "tool_use",
        toolUseId: "tool_read",
        toolName: "read_file",
        target: "snake_game/README.md",
        inputSummary: "snake_game/README.md",
        _chatCompat: true,
        _bridge: { persistTraceMirror: true },
      }, 2, "chat"),
      trace("evt_tool_complete", "tool_use_complete", {
        toolUseId: "tool_read",
        toolName: "read_file",
        input: { path: "snake_game/README.md" },
        target: "snake_game/README.md",
        inputSummary: "snake_game/README.md",
        _chatCompat: true,
        _bridge: { persistTraceMirror: true },
      }, 3, "chat"),
      trace("evt_tool_output", "content_delta", {
        toolUseId: "tool_read",
        toolName: "read_file",
        target: "snake_game/README.md",
        inputSummary: "snake_game/README.md",
        toolOutput: "read snake_game/README.md",
        outputStream: "result_preview",
        _chatCompat: true,
        _bridge: { persistTraceMirror: true },
      }, 5, "chat"),
      trace("evt_tool_result", "tool_result", {
        toolUseId: "tool_read",
        toolName: "read_file",
        content: { path: "snake_game/README.md", bytes: 42 },
        target: "snake_game/README.md",
        inputSummary: "snake_game/README.md",
        resultSummary: "read snake_game/README.md",
        durationMs: 12,
        _chatCompat: true,
        _bridge: { persistTraceMirror: true },
      }, 6, "chat"),
    ];

    const first = replayTraceEventsToChatMessages(current, traces);
    const replayed = replayTraceEventsToChatMessages(first, traces);
    const visible = getVisibleChatMessages(replayed, "sess_1");

    expect(visible.map((message) => message.id)).toEqual(["stored_user", "tool_activity:tool_read"]);
    expect(visible[1]).toMatchObject({
      toolName: "read_file",
      status: "completed",
      metadata: {
        kind: "tool_activity",
        toolUseId: "tool_read",
        target: "snake_game/README.md",
        inputSummary: "snake_game/README.md",
        resultSummary: "read snake_game/README.md",
        durationMs: 12,
      },
    });
  });

  it("does not restore raw trace-only tool lifecycle rows into chat", () => {
    const replayed = replayTraceEventsToChatMessages([], [
      trace("evt_tool_start", "tool.started", {
        toolCallId: "tool_raw",
        toolName: "read_file",
        arguments: { path: "debug.json" },
      }, 1, "trace"),
      trace("evt_tool_done", "tool.completed", {
        toolCallId: "tool_raw",
        toolName: "read_file",
        resultSummary: "raw lifecycle should stay in trace",
      }, 2, "trace"),
    ]);

    expect(getVisibleChatMessages(replayed, "sess_1")).toEqual([]);
  });

  it("does not append streamed tool output again when traces are replayed repeatedly", () => {
    const traces = [
      trace("evt_command_start", "command.started", {
        commandId: "cmd_1",
        toolUseId: "tool_cmd",
        toolName: "run_command",
        command: "npm test",
        target: "npm test",
      }, 1),
      trace("evt_command_output", "command.output", {
        commandId: "cmd_1",
        toolUseId: "tool_cmd",
        toolName: "run_command",
        stream: "stdout",
        chunk: "one line\n",
      }, 2),
      trace("evt_command_done", "command.completed", {
        commandId: "cmd_1",
        toolUseId: "tool_cmd",
        toolName: "run_command",
        status: "completed",
        exitCode: 0,
      }, 3),
    ];

    const first = replayTraceEventsToChatMessages([], traces);
    const replayed = replayTraceEventsToChatMessages(first, traces);
    const tool = getVisibleChatMessages(replayed, "sess_1").find((message) => message.id === "tool_activity:tool_cmd");

    expect(tool?.metadata?.resultText).toContain("one line");
    expect(String(tool?.metadata?.resultText).match(/one line/g)).toHaveLength(1);
  });

  it("keeps provider thinking, tool, and final message order stable across replay passes", () => {
    const traces = [
      trace("evt_think_1", "thinking", {
        text: "I will inspect the project first. ",
        source: "provider_reasoning_delta",
      }, 1, "chat"),
      ...flatToolFrames("tool_read", "read_file", 2, "snake_game/README.md", "read snake_game/README.md"),
      trace("evt_progress", "assistant_progress", {
        text: "正在合并候选文档。",
      }, 4, "chat"),
      trace("evt_think_2", "thinking", {
        text: "Now I can summarize the next step.",
        source: "provider_reasoning_delta",
      }, 5, "chat"),
      trace("evt_final_delta", "content_delta", {
        text: "下面是下一步优化路线图。",
        messageId: "assistant_1",
      }, 7, "chat"),
      trace("evt_complete", "message_complete", {
        messageId: "assistant_1",
        content: "下面是下一步优化路线图。",
      }, 8, "chat"),
    ];

    const first = replayTraceEventsToChatMessages([], traces);
    const replayed = replayTraceEventsToChatMessages(first, traces);
    const visible = getVisibleChatMessages(replayed, "sess_1");

    expect(visible.map((message) => message.id)).toEqual([
      "assistant_thinking:evt_think_1",
      "tool_activity:tool_read",
      "assistant_thinking:evt_think_2",
      "assistant_1",
    ]);
    expect(visible.map((message) => message.metadata?.kind ?? "assistant_text")).toEqual([
      "assistant_thinking",
      "tool_activity",
      "assistant_thinking",
      "assistant_text",
    ]);
    expect(visible.some((message) => message.metadata?.kind === "assistant_progress")).toBe(false);
    expect(visible.find((message) => message.id === "assistant_1")?.content).toBe("下面是下一步优化路线图。");
    expect(visible.filter((message) => message.id === "assistant_1")).toHaveLength(1);
  });

  it("replays chat-compat message.delta as the canonical assistant text stream", () => {
    const traces = [
      trace("evt_msg_delta_1", "message.delta", {
        messageId: "msg_1",
        delta: "hello ",
        _chatCompat: true,
      }, 1, "chat"),
      trace("evt_msg_delta_2", "message.delta", {
        messageId: "msg_1",
        delta: "world",
        _chatCompat: true,
      }, 2, "chat"),
      trace("evt_legacy_token", "assistant.token", {
        messageId: "msg_1",
        delta: " ignored",
        _chatCompat: true,
      }, 3, "chat"),
      trace("evt_complete", "message_complete", {
        messageId: "msg_1",
        content: "hello world",
      }, 4, "chat"),
    ];

    const first = replayTraceEventsToChatMessages([], traces);
    const second = replayTraceEventsToChatMessages(first, traces);
    const visible = getVisibleChatMessages(second, "sess_1");

    expect(visible.map((message) => message.id)).toEqual(["msg_1"]);
    expect(visible[0]?.content).toBe("hello world");
    expect(visible[0]?.streaming).toBe(false);
  });

  it("keeps replay idempotent under repeated thinking/tool cycles", () => {
    const traces: TraceEventRecord[] = [];
    let sequence = 1;
    for (let index = 0; index < 12; index += 1) {
      traces.push(trace(`evt_think_${index}`, "thinking", {
        text: `Thinking step ${index}.`,
        source: "provider_reasoning_delta",
      }, sequence += 1, "chat"));
      const toolName = index % 2 === 0 ? "read_file" : "search_files";
      const target = index % 2 === 0 ? `snake_game/file_${index}.py` : "snake_game";
      const toolFrames = flatToolFrames(`tool_${index}`, toolName, sequence + 1, target, `completed ${index}`);
      traces.push(...toolFrames);
      sequence += toolFrames.length;
      traces.push(trace(`evt_progress_${index}`, "assistant_progress", {
        text: `Merged evidence ${index}.`,
      }, sequence += 1, "chat"));
    }
    traces.push(trace("evt_final_complete", "message_complete", {
      messageId: "assistant_final",
      content: "Final synthesis.",
    }, sequence += 1, "chat"));

    const first = replayTraceEventsToChatMessages([], traces);
    const second = replayTraceEventsToChatMessages(first, traces);
    const third = replayTraceEventsToChatMessages(second, [...traces].reverse());
    const visible = getVisibleChatMessages(third, "sess_1");
    const ids = visible.map((message) => message.id);

    expect(ids).toEqual([
      ...Array.from({ length: 12 }).flatMap((_, index) => [
        `assistant_thinking:evt_think_${index}`,
        `tool_activity:tool_${index}`,
      ]),
      "assistant_final",
    ]);
    expect(new Set(ids).size).toBe(ids.length);
    expect(visible.filter((message) => message.id === "assistant_final")).toHaveLength(1);
  });

  it("restores ask-user cards and marks them answered from supplement consumed traces", () => {
    const traces = [
      trace("evt_ask", "ask_user_question", {
        requestId: "ask_123",
        toolCallId: "tool_ask",
        question: "Which task list format should I use?",
        summary: "Choose the task list format",
        options: [{ label: "Status list", value: "status_list" }],
      }, 2),
      trace("evt_answered", "task.supplement.consumed", {
        reason: "ask_user_question_answer",
        requestId: "ask_123",
        toolCallId: "tool_ask",
        answer: "Use the status list.",
        internalResponse: {
          kind: "ask_user_question",
          messageId: "ask_user_question:ask_123",
          requestId: "ask_123",
          toolCallId: "tool_ask",
        },
      }, 3),
    ];

    const replayed = replayTraceEventsToChatMessages([], traces);
    const visible = getVisibleChatMessages(replayed, "sess_1");

    expect(visible).toHaveLength(1);
    expect(visible[0]).toMatchObject({
      id: "ask_user_question:ask_123",
      status: "completed",
      metadata: {
        kind: "ask_user_question",
        requestId: "ask_123",
        toolCallId: "tool_ask",
        resolved: true,
        answered: true,
        status: "answered",
        answer: "Use the status list.",
      },
    });
  });

  it("ignores panel-only trace events when replaying the main transcript", () => {
    const replayed = replayTraceEventsToChatMessages([], [
      trace("evt_panel_tool", "tool.started", {
        toolCallId: "panel_tool",
        toolName: "read_file",
        arguments: { path: "debug.json" },
      }, 1, "panel"),
    ]);

    expect(getVisibleChatMessages(replayed, "sess_1")).toEqual([]);
  });

  it("does not replay backend progress chat mirrors from persisted traces", () => {
    const replayed = replayTraceEventsToChatMessages([], [
      trace("evt_backend_thinking", "thinking", {
        text: "Planning phase status",
        _bridge: {
          persistTraceMirror: true,
          suppressRealtimeFlat: true,
        },
      }, 1, "chat"),
      trace("evt_backend_progress", "assistant_progress", {
        text: "Internal phase update",
        _bridge: {
          suppressChatReplay: true,
        },
      }, 2, "chat"),
      trace("evt_visible_progress", "assistant_progress", {
        text: "Visible progress update",
      }, 3, "chat"),
    ]);

    const visible = getVisibleChatMessages(replayed, "sess_1");

    expect(visible).toEqual([]);
  });

  it("does not replay runtime status events as assistant thinking", () => {
    const replayed = replayTraceEventsToChatMessages([], [
      trace("evt_status_thinking", "status", {
        state: "thinking",
        verb: "model",
      }, 1, "chat"),
      trace("evt_status_streaming", "status", {
        state: "streaming",
        verb: "model",
      }, 2, "chat"),
    ]);

    expect(getVisibleChatMessages(replayed, "sess_1")).toEqual([]);
  });

  it("does not replay control-flow tools as normal chat tool rows", () => {
    const replayed = replayTraceEventsToChatMessages([], [
      trace("evt_plan_tool", "tool.started", {
        toolCallId: "call_plan",
        toolName: "enter_plan_mode",
        arguments: { reason: "Need a plan" },
      }, 1, "chat"),
      trace("evt_plan_done", "tool.completed", {
        toolCallId: "call_plan",
        toolName: "enter_plan_mode",
        resultSummary: "plan mode entered",
      }, 2, "chat"),
    ]);

    expect(getVisibleChatMessages(replayed, "sess_1")).toEqual([]);
  });

  it("does not replay approval-required tool completion as a finished tool row", () => {
    const replayed = replayTraceEventsToChatMessages([], [
      trace("evt_cmd_waiting", "tool.completed", {
        toolCallId: "call_command",
        toolName: "run_command",
        result: { status: "approval_required" },
        resultSummary: "approval required",
      }, 1, "chat"),
    ]);

    expect(getVisibleChatMessages(replayed, "sess_1")).toEqual([]);
  });

  it("does not replay late chat events after task cancellation", () => {
    const replayed = replayTraceEventsToChatMessages([], [
      trace("evt_cancel", "task.cancelled", { status: "cancelled" }, 1, "chat"),
      trace("evt_late_token", "assistant.token", {
        messageId: "msg_cancelled",
        delta: "late pending assistant text",
      }, 2, "chat"),
      trace("evt_late_message_completed", "message.completed", {
        messageId: "msg_cancelled",
        content: "late final answer",
      }, 3, "chat"),
      trace("evt_late_message_complete", "message_complete", {
        messageId: "msg_cancelled",
        content: "late legacy final answer",
      }, 4, "chat"),
      trace("evt_late_progress", "assistant_progress", { summary: "late internal work" }, 2, "chat"),
      trace("evt_late_tool", "tool.completed", {
        toolCallId: "tool_late",
        toolName: "run_command",
        resultSummary: "late result",
      }, 3, "chat"),
      trace("evt_late_review", "approval.resolved", {
        approvalId: "approval_review",
        kind: "completion_review",
        decision: "approved",
        request: { summary: "internal review" },
      }, 4, "chat"),
    ]);

    expect(getVisibleChatMessages(replayed, "sess_1")).toEqual([]);
  });

  it("does not replay completion review approvals as chat permission cards", () => {
    const replayed = replayTraceEventsToChatMessages([], [
      trace("evt_review_permission", "permission_request", {
        requestId: "approval_review",
        toolName: "completion_review",
        input: { summary: "internal review" },
      }, 1, "chat"),
      trace("evt_review_resolved", "approval.resolved", {
        approvalId: "approval_review",
        kind: "completion_review",
        decision: "approved",
        request: { summary: "internal review" },
      }, 2, "chat"),
    ]);

    expect(getVisibleChatMessages(replayed, "sess_1")).toEqual([]);
  });

  it("replays resolved plan approvals with structured sections instead of raw json", () => {
    const previewSections = [
      {
        kind: "items",
        title: "2 subtasks",
        items: [
          { id: "sub-0", title: "Inspect routing", description: "Check routing decisions." },
          { id: "sub-1", title: "Repair replay", description: "Keep live and replay aligned." },
        ],
      },
    ];
    const replayed = replayTraceEventsToChatMessages([], [
      trace("evt_plan_resolved", "approval.resolved", {
        approvalId: "approval_plan",
        kind: "plan",
        decision: "approved",
        request: {
          goal: "Optimize multi-agent flow",
          orchestrationMode: "swarm",
          subtaskCount: 2,
          previewSections,
        },
        previewSections,
      }, 1, "chat"),
    ]);

    const visible = getVisibleChatMessages(replayed, "sess_1");
    expect(visible).toHaveLength(1);
    expect(visible[0]?.id).toBe("permission_request:approval_plan");
    expect(visible[0]?.status).toBe("completed");
    expect(visible[0]?.content).toContain("Plan ready: 2 subtasks");
    expect(visible[0]?.content).not.toContain("\"previewSections\"");
    expect(visible[0]?.metadata?.previewSections).toEqual(previewSections);
  });

  it("replays haha-style team_update as an agent group without raw json content", () => {
    const replayed = replayTraceEventsToChatMessages([], [
      {
        ...trace("evt_team", "collab.task.created", {
          noisy: { raw: "do not render this object" },
        }, 1, "chat"),
        hahaCc: {
          type: "team_update",
          teamName: "swarm",
          members: [
            {
              agentId: "planner-1",
              role: "planner",
              status: "running",
              currentTask: "Inspect backend flow",
            },
            {
              agentId: "worker-1",
              role: "worker",
              status: "completed",
              currentTask: "Repair replay",
            },
          ],
        },
      },
    ]);

    const visible = getVisibleChatMessages(replayed, "sess_1");
    expect(visible).toHaveLength(1);
    expect(visible[0]?.metadata?.kind).toBe("agent_task_group");
    expect(visible[0]?.metadata?.teamName).toBe("swarm");
    expect(visible[0]?.metadata?.members).toEqual([
      {
        agentId: "planner-1",
        role: "planner",
        status: "running",
        currentTask: "Inspect backend flow",
      },
      {
        agentId: "worker-1",
        role: "worker",
        status: "completed",
        currentTask: "Repair replay",
      },
    ]);
    expect(visible[0]?.content).toBe("2 members");
    expect(visible[0]?.content).not.toContain("raw");
  });

  it("replays haha-style task_update as a compact task summary", () => {
    const replayed = replayTraceEventsToChatMessages([], [
      {
        ...trace("evt_task_update", "task.updated", {
          payload: { raw: "do not render" },
        }, 1, "chat"),
        hahaCc: {
          type: "task_update",
          taskId: "task_42",
          status: "running",
          progress: "Inspect current workflow",
        },
      },
    ]);

    const visible = getVisibleChatMessages(replayed, "sess_1");
    expect(visible).toHaveLength(1);
    expect(visible[0]?.metadata?.kind).toBe("task_summary");
    expect(visible[0]?.metadata?.sourceType).toBe("task_update");
    expect(visible[0]?.content).toBe("Inspect current workflow");
    expect(visible[0]?.content).not.toContain("payload");
  });

  it("hides child-worker trace events on session recovery unless they are explicitly chat-visible", () => {
    const replayed = replayTraceEventsToChatMessages(
      [],
      [
        trace("evt_child_tool", "tool.started", {
          toolCallId: "child_tool",
          toolName: "read_file",
          target: "child-notes.md",
        }, 1, undefined, "task_child"),
        trace("evt_root_tool", "tool.started", {
          toolCallId: "root_tool",
          toolName: "read_file",
          target: "README.md",
        }, 2, undefined, "task_root"),
        trace("evt_child_progress", "assistant_progress", {
          text: "Child status promoted to chat.",
        }, 3, "chat", "task_child"),
      ],
      {
        childTaskIds: new Set(["task_child"]),
      },
    );

    const visible = getVisibleChatMessages(replayed, "sess_1");

    expect(visible.map((message) => message.id)).toEqual([
      "tool_use:root_tool",
    ]);
  });
});
