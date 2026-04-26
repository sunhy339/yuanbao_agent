import { describe, expect, it } from "vitest";
import { appendUserMessage, getVisibleChatMessages, updatePendingMessageTask } from "./chatMessages";
import type { ChatMessageView } from "./chatMessages";

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
});
