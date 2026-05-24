import { describe, expect, it } from "vitest";
import {
  appendAssistantPlaceholder,
  appendUserMessage,
  failAssistantMessage,
  getVisibleChatMessages,
  isOperationalAssistantDelta,
  reconcileBackendMessage,
  removeChatMessage,
  replaceSessionMessages,
  summarizeOperationalAssistantDelta,
  updateAssistantMessageByMessageId,
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

  it("turns a failed pending assistant placeholder into a visible error message", () => {
    const pending = appendAssistantPlaceholder(messages, {
      id: "thinking_1",
      sessionId: "sess_1",
      content: "鎬濊€冧腑...",
      now: 4,
    });

    const next = failAssistantMessage(pending, {
      messageId: "thinking_1",
      sessionId: "sess_1",
      taskId: "task_3",
      content: "发送失败：Provider request failed",
      now: 5,
    });

    expect(next.at(-1)).toMatchObject({
      id: "thinking_1",
      taskId: "task_3",
      role: "assistant",
      content: "发送失败：Provider request failed",
      streaming: false,
      placeholder: false,
    });
  });

  it("classifies runtime progress tokens as non-chat assistant deltas", () => {
    expect(isOperationalAssistantDelta("Building context and preparing the first tool calls...")).toBe(true);
    expect(isOperationalAssistantDelta("Running tool: list_dir")).toBe(true);
    expect(isOperationalAssistantDelta("Started subtask: Inspect workspace")).toBe(true);
    expect(isOperationalAssistantDelta("Subtask running tool: run_command")).toBe(true);
    expect(isOperationalAssistantDelta("Subtask tool completed: run_command")).toBe(true);
    expect(isOperationalAssistantDelta("Subtask waiting for approval: run_command")).toBe(true);
    expect(isOperationalAssistantDelta("Running post-task git status validation...")).toBe(true);
    expect(isOperationalAssistantDelta("Approval accepted. Applying the patch now...")).toBe(true);
    expect(isOperationalAssistantDelta("Completed the minimal tool loop and preparing a summary...")).toBe(true);
    expect(isOperationalAssistantDelta("我已经创建好了文件。")).toBe(false);
  });
  it("turns runtime progress tokens into readable chat progress summaries", () => {
    expect(summarizeOperationalAssistantDelta("Running tool: list_dir")).toBe("正在使用目录。");
    expect(summarizeOperationalAssistantDelta("Subtask waiting for approval: run_command")).toBe(
      "需要你审批后才能继续执行命令。",
    );
    expect(summarizeOperationalAssistantDelta("Subtask tool failed: run_command")).toBe(
      "子任务里的命令失败了，我会继续看失败原因。",
    );
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

  it("keeps live assistant placeholders sorted after newly persisted user messages", () => {
    const persisted: MessageRecord[] = [
      {
        id: "stored_user",
        sessionId: "sess_1",
        role: "user",
        content: "continue crawling",
        createdAt: 100,
      },
    ];
    const liveMessages: ChatMessageView[] = [
      {
        id: "live_assistant",
        sessionId: "sess_1",
        taskId: "task_live",
        role: "assistant",
        content: "thinking...",
        createdAt: 51,
        updatedAt: 51,
        streaming: true,
        placeholder: true,
      },
    ];

    const next = replaceSessionMessages(liveMessages, "sess_1", persisted);
    const visible = getVisibleChatMessages(next, "sess_1");

    expect(visible.map((message) => message.id)).toEqual(["stored_user", "live_assistant"]);
    expect(visible[1].updatedAt).toBeGreaterThan(visible[0].createdAt);
  });

  it("keeps local pending messages during a persisted-message refresh race", () => {
    const persisted: MessageRecord[] = [
      {
        id: "stored_user",
        sessionId: "sess_1",
        role: "user",
        content: "previous request",
        createdAt: 10,
      },
    ];
    const localMessages: ChatMessageView[] = [
      {
        id: "user_100",
        sessionId: "sess_1",
        taskId: "pending",
        role: "user",
        content: "new request not persisted yet",
        createdAt: 100,
        updatedAt: 100,
      },
      {
        id: "assistant_pending_101",
        sessionId: "sess_1",
        taskId: "pending",
        role: "assistant",
        content: "thinking...",
        createdAt: 101,
        updatedAt: 101,
        streaming: true,
        placeholder: true,
      },
    ];

    const next = replaceSessionMessages(localMessages, "sess_1", persisted);

    expect(getVisibleChatMessages(next, "sess_1").map((message) => message.content)).toEqual([
      "previous request",
      "new request not persisted yet",
      "thinking...",
    ]);
  });

  it("hides empty non-placeholder streaming assistant shells", () => {
    const liveMessages: ChatMessageView[] = [
      {
        id: "stored_user",
        sessionId: "sess_1",
        taskId: "task_1",
        role: "user",
        content: "build the project",
        createdAt: 1,
        updatedAt: 1,
      },
      {
        id: "empty_streaming",
        sessionId: "sess_1",
        taskId: "task_1",
        role: "assistant",
        content: "",
        createdAt: 2,
        updatedAt: 2,
        streaming: true,
        placeholder: false,
        status: "streaming",
      },
      {
        id: "thinking",
        sessionId: "sess_1",
        taskId: "task_1",
        role: "assistant",
        content: "thinking...",
        createdAt: 3,
        updatedAt: 3,
        streaming: true,
        placeholder: true,
        status: "streaming",
      },
    ];

    expect(getVisibleChatMessages(liveMessages, "sess_1").map((message) => message.id)).toEqual([
      "stored_user",
      "thinking",
    ]);
  });

  it("reconciles a backend assistant message with the pending streaming placeholder", () => {
    const pending: ChatMessageView[] = [
      {
        id: "stored_user",
        sessionId: "sess_1",
        taskId: "task_1",
        role: "user",
        content: "build the project",
        createdAt: 1,
        updatedAt: 1,
      },
      {
        id: "assistant_pending_101",
        sessionId: "sess_1",
        taskId: "pending",
        role: "assistant",
        content: "thinking...",
        createdAt: 2,
        updatedAt: 2,
        streaming: true,
        placeholder: true,
        status: "streaming",
      },
    ];

    const next = reconcileBackendMessage(pending, {
      id: "msg_backend_assistant",
      sessionId: "sess_1",
      taskId: "task_1",
      role: "assistant",
      content: "",
      createdAt: 3,
      updatedAt: 3,
      status: "streaming",
    });

    expect(getVisibleChatMessages(next, "sess_1").map((message) => message.id)).toEqual([
      "stored_user",
      "msg_backend_assistant",
    ]);
    expect(next).toHaveLength(2);
    expect(next[1]).toMatchObject({
      content: "thinking...",
      streaming: true,
      placeholder: true,
    });
  });

  it("attaches a message delta to the pending assistant when the backend id is new", () => {
    const pending: ChatMessageView[] = [
      {
        id: "assistant_pending_101",
        sessionId: "sess_1",
        taskId: "pending",
        role: "assistant",
        content: "thinking...",
        createdAt: 2,
        updatedAt: 2,
        streaming: true,
        placeholder: true,
        status: "streaming",
      },
    ];

    const next = updateAssistantMessageByMessageId(
      pending,
      "msg_backend_assistant",
      (message) => ({
        ...message,
        content: message.placeholder ? "first token" : `${message.content}first token`,
        placeholder: false,
        streaming: true,
        updatedAt: 4,
      }),
      { sessionId: "sess_1", taskId: "task_1" },
    );

    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({
      id: "msg_backend_assistant",
      taskId: "task_1",
      content: "first token",
      placeholder: false,
      streaming: true,
    });
  });

  it("drops a local pending message once the same persisted message arrives", () => {
    const persisted: MessageRecord[] = [
      {
        id: "stored_user",
        sessionId: "sess_1",
        role: "user",
        content: "new request",
        createdAt: 110,
      },
    ];
    const localMessages: ChatMessageView[] = [
      {
        id: "user_100",
        sessionId: "sess_1",
        taskId: "pending",
        role: "user",
        content: "new request",
        createdAt: 100,
        updatedAt: 100,
      },
    ];

    const next = replaceSessionMessages(localMessages, "sess_1", persisted);

    expect(getVisibleChatMessages(next, "sess_1").map((message) => message.id)).toEqual(["stored_user"]);
  });
});
