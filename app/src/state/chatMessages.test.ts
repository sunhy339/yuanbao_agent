import { describe, expect, it } from "vitest";
import {
  appendAssistantPlaceholder,
  appendUserMessage,
  getVisibleChatMessages,
  isOperationalAssistantDelta,
  removeChatMessage,
  replaceSessionMessages,
  updatePendingMessageTask,
} from "./chatMessages";
import type { ChatMessageView } from "./chatMessages";
import type { MessageRecord } from "@shared";

const messages: ChatMessageView[] = [
  {
    id: "m1",
    sessionId: "sess_1",
    taskId: "task_1",
    role: "user",
    content: "你好",
    createdAt: 1,
    updatedAt: 1,
  },
  {
    id: "m2",
    sessionId: "sess_1",
    taskId: "task_1",
    role: "assistant",
    content: "你好，有什么可以帮你？",
    createdAt: 2,
    updatedAt: 2,
  },
  {
    id: "m3",
    sessionId: "sess_2",
    taskId: "task_2",
    role: "user",
    content: "Other session",
    createdAt: 3,
    updatedAt: 3,
  },
];

describe("chatMessages", () => {
  it("keeps earlier messages when appending a new user message", () => {
    const next = appendUserMessage(messages, {
      id: "m4",
      sessionId: "sess_1",
      content: "看看当前文件夹",
      now: 4,
    });

    expect(next.map((message) => message.content)).toEqual([
      "你好",
      "你好，有什么可以帮你？",
      "Other session",
      "看看当前文件夹",
    ]);
  });

  it("shows all messages for the selected session instead of only the active task", () => {
    const next = appendUserMessage(messages, {
      id: "m4",
      sessionId: "sess_1",
      content: "看看当前文件夹",
      now: 4,
    });
    const committed = updatePendingMessageTask(next, "m4", "task_3");

    expect(getVisibleChatMessages(committed, "sess_1").map((message) => message.content)).toEqual([
      "你好",
      "你好，有什么可以帮你？",
      "看看当前文件夹",
    ]);
  });

  it("adds and updates a pending assistant thinking placeholder", () => {
    const next = appendAssistantPlaceholder(messages, {
      id: "thinking_1",
      sessionId: "sess_1",
      content: "思考中...",
      now: 4,
    });
    const committed = updatePendingMessageTask(next, "thinking_1", "task_3");

    expect(committed.at(-1)).toMatchObject({
      id: "thinking_1",
      taskId: "task_3",
      role: "assistant",
      content: "思考中...",
      streaming: true,
      placeholder: true,
    });
  });

  it("can remove the assistant placeholder after a send failure", () => {
    const next = appendAssistantPlaceholder(messages, {
      id: "thinking_1",
      sessionId: "sess_1",
      content: "思考中...",
      now: 4,
    });

    expect(removeChatMessage(next, "thinking_1")).toEqual(messages);
  });

  it("classifies runtime progress tokens as non-chat assistant deltas", () => {
    expect(isOperationalAssistantDelta("Building context and preparing the first tool calls...")).toBe(true);
    expect(isOperationalAssistantDelta("Running tool: list_dir")).toBe(true);
    expect(isOperationalAssistantDelta("Running post-task git status validation...")).toBe(true);
    expect(isOperationalAssistantDelta("Approval accepted. Applying the patch now...")).toBe(true);
    expect(isOperationalAssistantDelta("Completed the minimal tool loop and preparing a summary...")).toBe(true);
    expect(isOperationalAssistantDelta("我已经创建好了文件。")).toBe(false);
  });
  it("replaces one session with persisted messages while keeping live streaming placeholders", () => {
    const persisted: MessageRecord[] = [
      {
        id: "stored_user",
        sessionId: "sess_1",
        role: "user",
        content: "persisted request",
        createdAt: 10,
      },
      {
        id: "stored_assistant",
        sessionId: "sess_1",
        role: "assistant",
        content: "persisted answer",
        createdAt: 11,
      },
    ];
    const liveMessages: ChatMessageView[] = [
      ...messages,
      {
        id: "live_assistant",
        sessionId: "sess_1",
        taskId: "task_live",
        role: "assistant",
        content: "thinking...",
        createdAt: 12,
        updatedAt: 12,
        streaming: true,
        placeholder: true,
      },
    ];

    const next = replaceSessionMessages(liveMessages, "sess_1", persisted);

    expect(next.map((message) => message.id)).toEqual([
      "m3",
      "stored_user",
      "stored_assistant",
      "live_assistant",
    ]);
    expect(getVisibleChatMessages(next, "sess_1").map((message) => message.content)).toEqual([
      "persisted request",
      "persisted answer",
      "thinking...",
    ]);
  });
});
