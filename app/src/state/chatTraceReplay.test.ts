import { describe, expect, it } from "vitest";
import type { TraceEventRecord } from "@shared";
import { getVisibleChatMessages, type ChatMessageView } from "./chatMessages";
import { replayTraceEventsToChatMessages } from "./chatTraceReplay";

function trace(
  id: string,
  type: string,
  payload: unknown,
  sequence: number,
  visibility: TraceEventRecord["visibility"] = "chat",
): TraceEventRecord {
  return {
    id,
    sessionId: "sess_1",
    taskId: "task_1",
    type,
    source: type.split(".")[0] || "runtime",
    payload,
    createdAt: sequence,
    sequence,
    visibility,
  };
}

describe("chat trace replay", () => {
  it("restores tool activity blocks from persisted trace events", () => {
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
      trace("evt_tool_start", "tool.started", {
        toolCallId: "tool_read",
        toolName: "read_file",
        arguments: { path: "snake_game/README.md" },
        target: "snake_game/README.md",
        inputSummary: "snake_game/README.md",
      }, 2),
      trace("evt_tool_done", "tool.completed", {
        toolCallId: "tool_read",
        toolName: "read_file",
        target: "snake_game/README.md",
        inputSummary: "snake_game/README.md",
        resultSummary: "read snake_game/README.md",
        durationMs: 12,
      }, 3),
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
});
