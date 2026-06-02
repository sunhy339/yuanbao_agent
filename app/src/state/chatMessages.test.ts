import { describe, expect, it } from "vitest";
import {
  appendAssistantContentDelta,
  appendOrUpdateAssistantMessageCompletion,
  appendOrUpdateAssistantMessageDelta,
  appendOrUpdateAssistantToolInputDelta,
  appendOrUpdateAssistantToolOutputDelta,
  appendOrUpdateAssistantToolStartMessage,
  appendOrUpdateAssistantThinkingMessage,
  appendOrUpdatePermissionRequestMessage,
  appendAssistantToolResultMessage,
  appendAssistantPlaceholder,
  appendAssistantProgressMessage,
  appendSpecialEventMessage,
  appendUserMessage,
  completeAssistantToolUseMessage,
  completeChatCompatMessage,
  failAssistantMessage,
  getVisibleChatMessages,
  isOperationalAssistantDelta,
  reconcileBackendMessage,
  removeChatMessage,
  removeAssistantThinkingMessage,
  replaceSessionMessages,
  resolvePermissionRequestMessage,
  resolveSpecialApprovalMessage,
  sanitizeAssistantStatusContent,
  stripAssistantRuntimeProgress,
  summarizeOperationalAssistantDelta,
  updateAssistantMessageByMessageId,
  updatePendingMessageTask,
  stopStreamingMessagesForTask,
} from "./chatMessages";
import type { ChatMessageView } from "./chatMessages";
import type { MessageRecord } from "@shared";
import { appendAssistantToken } from "./chatTokenHelpers";

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

  it("shows and clears haha-style assistant thinking status blocks", () => {
    const next = appendOrUpdateAssistantThinkingMessage(messages, {
      sessionId: "sess_1",
      taskId: "task_3",
      state: "thinking",
      now: 4,
    });

    const visible = getVisibleChatMessages(next, "sess_1");
    expect(visible.at(-1)).toMatchObject({
      id: "assistant_thinking:task_3",
      role: "assistant",
      streaming: true,
      metadata: { kind: "assistant_thinking", state: "thinking" },
    });

    const cleared = removeAssistantThinkingMessage(next, {
      sessionId: "sess_1",
      taskId: "task_3",
    });
    expect(getVisibleChatMessages(cleared, "sess_1").map((message) => message.id)).not.toContain(
      "assistant_thinking:task_3",
    );
  });

  it("appends streaming thinking summary deltas", () => {
    const first = appendOrUpdateAssistantThinkingMessage(messages, {
      sessionId: "sess_1",
      taskId: "task_3",
      state: "thinking",
      text: "Reading ",
      now: 4,
    });
    const next = appendOrUpdateAssistantThinkingMessage(first, {
      sessionId: "sess_1",
      taskId: "task_3",
      state: "thinking",
      text: "files.",
      now: 5,
    });

    expect(next.find((message) => message.id === "assistant_thinking:task_3")?.content).toBe("Reading files.");
  });

  it("keeps permission request blocks when persisted messages refresh", () => {
    const withPermission = appendOrUpdatePermissionRequestMessage(messages, {
      requestId: "approval_1",
      toolName: "run_command",
      input: { command: "npm test" },
      description: "Need approval",
      sessionId: "sess_1",
      taskId: "task_3",
      now: 4,
    });

    const refreshed = replaceSessionMessages(withPermission, "sess_1", [
      {
        id: "stored_user",
        sessionId: "sess_1",
        role: "user",
        content: "Run tests",
        createdAt: 5,
      } as MessageRecord,
    ]);

    const permission = getVisibleChatMessages(refreshed, "sess_1").find(
      (message) => message.id === "permission_request:approval_1",
    );
    expect(permission).toMatchObject({
      role: "assistant",
      toolName: "run_command",
      metadata: { kind: "permission_request", requestId: "approval_1" },
    });
  });

  it("stores structured permission request preview and changed files", () => {
    const withPermission = appendOrUpdatePermissionRequestMessage(messages, {
      requestId: "approval_1",
      toolName: "apply_patch",
      input: { summary: "Update rules" },
      description: "Patch needs approval",
      preview: [{ label: "摘要", value: "Update rules" }],
      filesChanged: 2,
      changedPaths: ["src/rules.ts", "src/rules.test.ts"],
      diffText: "diff --git a/src/rules.ts b/src/rules.ts\n",
      sessionId: "sess_1",
      taskId: "task_3",
      now: 4,
    });

    const permission = getVisibleChatMessages(withPermission, "sess_1").find(
      (message) => message.id === "permission_request:approval_1",
    );
    expect(permission).toMatchObject({
      toolName: "apply_patch",
      metadata: {
        kind: "permission_request",
        requestId: "approval_1",
        approvalKind: "apply_patch",
        previewRows: [{ label: "摘要", value: "Update rules" }],
        filesChanged: 2,
        changedPaths: ["src/rules.ts", "src/rules.test.ts"],
        diffText: "diff --git a/src/rules.ts b/src/rules.ts\n",
      },
    });
  });

  it("marks permission request blocks resolved without keeping action state", () => {
    const withPermission = appendOrUpdatePermissionRequestMessage(messages, {
      requestId: "approval_1",
      toolName: "run_command",
      input: { command: "npm test" },
      description: "Need approval",
      sessionId: "sess_1",
      taskId: "task_3",
      now: 4,
    });

    const resolved = resolvePermissionRequestMessage(withPermission, {
      requestId: "approval_1",
      decision: "approved",
      now: 5,
    });

    const permission = getVisibleChatMessages(resolved, "sess_1").find(
      (message) => message.id === "permission_request:approval_1",
    );
    expect(permission).toMatchObject({
      status: "completed",
      metadata: { kind: "permission_request", requestId: "approval_1", decision: "approved", resolved: true },
    });
  });

  it("uses resolved approval payload to fill missing permission details", () => {
    const withPermission = appendOrUpdatePermissionRequestMessage(messages, {
      requestId: "approval_1",
      toolName: "approval",
      input: {},
      sessionId: "sess_1",
      taskId: "task_3",
      now: 4,
    });

    const resolved = resolvePermissionRequestMessage(withPermission, {
      requestId: "approval_1",
      decision: "approved",
      toolName: "apply_patch",
      input: { summary: "Update rules" },
      preview: [{ label: "摘要", value: "Update rules" }],
      filesChanged: 1,
      changedPaths: ["src/rules.ts"],
      diffText: "diff --git a/src/rules.ts b/src/rules.ts\n",
      now: 5,
    });

    const permission = getVisibleChatMessages(resolved, "sess_1").find(
      (message) => message.id === "permission_request:approval_1",
    );
    expect(permission).toMatchObject({
      status: "completed",
      metadata: {
        approvalKind: "apply_patch",
        previewRows: [{ label: "摘要", value: "Update rules" }],
        filesChanged: 1,
        changedPaths: ["src/rules.ts"],
        diffText: "diff --git a/src/rules.ts b/src/rules.ts\n",
      },
    });
  });

  it("creates a resolved permission card from detailed resolved payload when the request is missing", () => {
    const resolved = resolvePermissionRequestMessage(messages, {
      requestId: "approval_1",
      decision: "approved",
      toolName: "write_file",
      input: { path: "src/new.ts", risk: "writes file" },
      preview: [{ label: "文件", value: "src/new.ts" }],
      filesChanged: 1,
      changedPaths: ["src/new.ts"],
      diffText: "--- /dev/null\n+++ b/src/new.ts\n",
      sessionId: "sess_1",
      taskId: "task_1",
      createIfMissing: true,
      now: 5,
    });

    const permission = getVisibleChatMessages(resolved, "sess_1").find(
      (message) => message.id === "permission_request:approval_1",
    );
    expect(permission).toMatchObject({
      status: "completed",
      toolName: "write_file",
      metadata: {
        kind: "permission_request",
        requestId: "approval_1",
        decision: "approved",
        resolved: true,
        approvalKind: "write_file",
        previewRows: [{ label: "文件", value: "src/new.ts" }],
        filesChanged: 1,
        changedPaths: ["src/new.ts"],
        diffText: "--- /dev/null\n+++ b/src/new.ts\n",
      },
    });
  });

  it("does not create a low-value permission card for empty resolved payloads", () => {
    const resolved = resolvePermissionRequestMessage(messages, {
      requestId: "approval_1",
      decision: "approved",
      sessionId: "sess_1",
      createIfMissing: true,
      now: 5,
    });

    expect(getVisibleChatMessages(resolved, "sess_1").some(
      (message) => message.id === "permission_request:approval_1",
    )).toBe(false);
  });

  it("reuses computer-use approval id and resolves the special card", () => {
    const withRequest = appendSpecialEventMessage(messages, {
      kind: "computer_use_permission",
      sessionId: "sess_1",
      taskId: "task_3",
      content: "",
      summary: "Read current window",
      status: "waiting_approval",
      metadata: {
        kind: "computer_use_permission",
        approvalId: "appr_computer",
        app: "VS Code",
        action: "Read current window",
      },
      now: 4,
    });
    const withResolvedEvent = appendSpecialEventMessage(withRequest, {
      kind: "computer_use_permission",
      sessionId: "sess_1",
      taskId: "task_3",
      content: "",
      summary: "Read current window",
      status: "approved",
      metadata: {
        kind: "computer_use_permission",
        approvalId: "appr_computer",
        decision: "approved",
        resolved: true,
      },
      now: 5,
    });

    const cards = getVisibleChatMessages(withResolvedEvent, "sess_1").filter(
      (message) => message.id === "computer_use_permission:appr_computer",
    );
    expect(cards).toHaveLength(1);
    expect(cards[0]).toMatchObject({
      metadata: {
        app: "VS Code",
        action: "Read current window",
        decision: "approved",
        resolved: true,
      },
    });

    const resolved = resolveSpecialApprovalMessage(withResolvedEvent, {
      approvalId: "appr_computer",
      decision: "rejected",
      now: 6,
    });
    const card = getVisibleChatMessages(resolved, "sess_1").find(
      (message) => message.id === "computer_use_permission:appr_computer",
    );
    expect(card).toMatchObject({
      status: "failed",
      metadata: {
        kind: "computer_use_permission",
        approvalId: "appr_computer",
        app: "VS Code",
        action: "Read current window",
        decision: "rejected",
        resolved: true,
      },
    });
  });

  it("fills computer-use special cards with resolved approval request details", () => {
    const withRequest = appendSpecialEventMessage(messages, {
      kind: "computer_use_permission",
      sessionId: "sess_1",
      taskId: "task_3",
      content: "",
      summary: "Computer Use approval",
      status: "waiting_approval",
      metadata: {
        kind: "computer_use_permission",
        approvalId: "appr_computer",
      },
      now: 4,
    });

    const resolved = resolveSpecialApprovalMessage(withRequest, {
      approvalId: "appr_computer",
      decision: "approved",
      input: {
        app: "Browser",
        action: "click",
        selector: "button[type=submit]",
        url: "http://localhost:5173",
        pageId: "page_1",
      },
      preview: [
        { label: "动作", value: "click" },
        { label: "目标", value: "button[type=submit]" },
      ],
      now: 6,
    });

    const card = getVisibleChatMessages(resolved, "sess_1").find(
      (message) => message.id === "computer_use_permission:appr_computer",
    );
    expect(card).toMatchObject({
      status: "completed",
      metadata: {
        kind: "computer_use_permission",
        approvalId: "appr_computer",
        decision: "approved",
        resolved: true,
        app: "Browser",
        action: "click",
        selector: "button[type=submit]",
        url: "http://localhost:5173",
        pageId: "page_1",
        previewRows: [
          { label: "动作", value: "click" },
          { label: "目标", value: "button[type=submit]" },
        ],
      },
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

  it("appends supplement send failures instead of overwriting the previous assistant answer", () => {
    const next = failAssistantMessage(messages, {
      sessionId: "sess_1",
      taskId: "task_1",
      content: "发送失败：Cannot supplement task that is not active",
      now: 5,
      appendOnly: true,
    });

    expect(next.map((message) => message.content)).toEqual([
      "你好",
      "你好，有什么可以帮你？",
      "Other session",
      "发送失败：Cannot supplement task that is not active",
    ]);
    expect(next.at(-1)).toMatchObject({
      role: "assistant",
      kind: "failure",
      status: "failed",
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
    expect(isOperationalAssistantDelta('Task Cancelled {"acceptanceCriteria":["Keep focused"]}')).toBe(true);
    expect(isOperationalAssistantDelta('{"cwd":"D:\\\\py\\\\test_pro","sessionId":"sess_1","taskId":"task_1"}')).toBe(true);
    expect(isOperationalAssistantDelta("我已经创建好了文件。")).toBe(false);
  });

  it("keeps assistant stream content contiguous", () => {
    expect(appendAssistantContentDelta("我就可以", "正在使用 git_status。")).toBe("我就可以正在使用 git_status。");
  });

  it("replaces repeated full assistant snapshots instead of appending duplicates", () => {
    expect(
      appendAssistantContentDelta(
        "当前目录内容如下：\n\n- snake_game/\n",
        "当前目录内容如下：\n\n- snake_game/\n- README.md\n",
      ),
    ).toBe("当前目录内容如下：\n\n- snake_game/\n- README.md\n");
  });

  it("turns runtime progress into short useful chat updates", () => {
    expect(summarizeOperationalAssistantDelta("Running tool: list_dir")).toContain("我在查看目录。");
    expect(summarizeOperationalAssistantDelta("Building context and preparing the first tool calls...")).toContain("正在整理上下文");
    expect(summarizeOperationalAssistantDelta("Subtask tool completed: run_command")).toContain("命令已完成。");
    expect(summarizeOperationalAssistantDelta("Subtask waiting for approval: run_command")).toContain("等待审批：命令。");
    expect(summarizeOperationalAssistantDelta("Subtask tool failed: run_command")).toContain("命令失败");
    expect(summarizeOperationalAssistantDelta("Subtask command cancelled")).toContain("命令已取消。");
    expect(summarizeOperationalAssistantDelta('Task Cancelled {"acceptanceCriteria":["Keep focused"]}')).toBe(
      "\n\n任务已取消，已停止继续执行。",
    );
    expect(summarizeOperationalAssistantDelta('{"cwd":"D:\\\\py\\\\test_pro","sessionId":"sess_1","taskId":"task_1"}')).toBeNull();
  });

  it("routes operational assistant tokens into a thinking status message", () => {
    const next = appendAssistantToken([], {
      eventId: "evt_1",
      type: "assistant.token",
      sessionId: "sess_1",
      taskId: "task_1",
      ts: 10,
      payload: {
        delta: "Running tool: list_dir",
      },
    } as any);

    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({
      id: "assistant_thinking:task_1",
      role: "assistant",
      content: "我在查看目录。",
      metadata: {
        kind: "assistant_thinking",
      },
    });
  });

  it("keeps operational progress as a visible lightweight transcript item", () => {
    const next = appendAssistantProgressMessage([], {
      sessionId: "sess_1",
      taskId: "task_1",
      content: "我在查看目录。",
      now: 10,
      eventId: "evt_progress",
    });

    expect(getVisibleChatMessages(next, "sess_1")).toEqual([
      expect.objectContaining({
        id: "assistant_progress:evt_progress",
        content: "我在查看目录。",
        metadata: { kind: "assistant_progress" },
      }),
    ]);
  });

  it("does not append real assistant tokens into the thinking status bubble", () => {
    const withProgress = appendAssistantToken([], {
      eventId: "evt_1",
      type: "assistant.token",
      sessionId: "sess_1",
      taskId: "task_1",
      ts: 10,
      payload: { delta: "Building context and preparing the first tool calls..." },
    } as any);
    const next = appendAssistantToken(withProgress, {
      eventId: "evt_2",
      type: "assistant.token",
      sessionId: "sess_1",
      taskId: "task_1",
      ts: 11,
      payload: { delta: "这里是实际回复。" },
    } as any);

    expect(next).toHaveLength(2);
    expect(next[0].metadata?.kind).toBe("assistant_thinking");
    expect(next[0].content).toContain("正在整理上下文");
    expect(next[1]).toMatchObject({
      role: "assistant",
      content: "这里是实际回复。",
      streaming: true,
    });
  });

  it("does not append the same operational update twice", () => {
    expect(appendAssistantContentDelta("我在查看目录。", "\n\n我在查看目录。")).toBe("我在查看目录。");
  });

  it("strips runtime progress lines from assistant transcript display", () => {
    expect(
      stripAssistantRuntimeProgress("我在查看目录。\n\n最终回答：已经完成。\n\n命令已完成。"),
    ).toBe("最终回答：已经完成。");
  });

  it("strips English runtime progress when it is glued to the final answer", () => {
    expect(
      stripAssistantRuntimeProgress(
        "Building context and preparing the first tool calls...Here’s what I found:\n\n1. Project structure\n- README.md",
      ),
    ).toBe("Here’s what I found:\n\n1. Project structure\n- README.md");
  });

  it("hides assistant messages that only contain runtime progress", () => {
    const progressOnly: ChatMessageView[] = [
      {
        id: "m_progress",
        sessionId: "sess_1",
        taskId: "task_1",
        role: "assistant",
        content: "我在查看目录。\n\n命令已完成。",
        createdAt: 1,
        updatedAt: 1,
      },
    ];

    expect(getVisibleChatMessages(progressOnly, "sess_1")).toEqual([]);
  });

  it("sanitizes runtime failure payloads before they become chat text", () => {
    expect(sanitizeAssistantStatusContent('Task Cancelled {"acceptanceCriteria":["Keep focused"]}')).toBe(
      "任务已取消，已停止继续执行。",
    );
    expect(
      sanitizeAssistantStatusContent(
        "Task task_27bbe3b48966 cannot transition from 'cancelled' to 'waiting_approval'.\nAllowed: none (terminal state)",
      ),
    ).toBe("这条任务已经结束，不能继续补充；请重新发起一条任务。");
    expect(sanitizeAssistantStatusContent("Cannot supplement task that is not active: task_065cdf0c450f")).toBe(
      "这条任务已经结束，不能继续补充；请重新发起一条任务。",
    );
    expect(
      sanitizeAssistantStatusContent(
        '{"error":"Command is not allowed by command allowlist","failureKind":"permission_denied"}',
      ),
    ).toBe("命令没有真正执行：运行时策略拦截了这条命令，需要先审批或使用允许的等价命令。");
    expect(sanitizeAssistantStatusContent("Provider returned error: Concurrency limit exceeded for account")).toBe(
      "模型并发额度暂时满了，请稍后重试。",
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

  it("hides persisted empty assistant shells that were left streaming", () => {
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
        id: "empty_persisted_streaming",
        sessionId: "sess_1",
        taskId: "task_1",
        role: "assistant",
        content: "",
        createdAt: 2,
        updatedAt: 2,
        status: "streaming",
      },
    ];

    expect(getVisibleChatMessages(liveMessages, "sess_1").map((message) => message.id)).toEqual([
      "stored_user",
    ]);
  });

  it("removes empty streaming placeholders when a task reaches a terminal state", () => {
    const next = stopStreamingMessagesForTask(
      [
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
          id: "assistant_pending",
          sessionId: "sess_1",
          taskId: "task_1",
          role: "assistant",
          content: "",
          createdAt: 2,
          updatedAt: 2,
          streaming: true,
          placeholder: true,
          status: "streaming",
        },
        {
          id: "other_task",
          sessionId: "sess_1",
          taskId: "task_2",
          role: "assistant",
          content: "still running",
          createdAt: 3,
          updatedAt: 3,
          streaming: true,
          placeholder: false,
          status: "streaming",
        },
      ],
      { sessionId: "sess_1", taskId: "task_1" },
    );

    expect(next.map((message) => message.id)).toEqual(["stored_user", "other_task"]);
    expect(next.find((message) => message.id === "other_task")).toMatchObject({
      streaming: true,
      status: "streaming",
    });
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

  it("creates a visible streaming assistant message when a backend delta arrives before message.created", () => {
    const next = appendOrUpdateAssistantMessageDelta([], {
      messageId: "msg_backend_assistant",
      sessionId: "sess_1",
      taskId: "task_1",
      delta: "First streamed token",
      now: 4,
    });

    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({
      id: "msg_backend_assistant",
      sessionId: "sess_1",
      taskId: "task_1",
      role: "assistant",
      content: "First streamed token",
      placeholder: false,
      streaming: true,
      status: "streaming",
    });
    expect(getVisibleChatMessages(next, "sess_1").map((message) => message.content)).toEqual([
      "First streamed token",
    ]);
  });

  it("creates a completed assistant message when message.completed arrives before local state exists", () => {
    const next = appendOrUpdateAssistantMessageCompletion([], {
      messageId: "msg_backend_assistant",
      sessionId: "sess_1",
      taskId: "task_1",
      content: "Final answer",
      now: 5,
    });

    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({
      id: "msg_backend_assistant",
      sessionId: "sess_1",
      taskId: "task_1",
      role: "assistant",
      content: "Final answer",
      placeholder: false,
      streaming: false,
      status: "completed",
    });
    expect(getVisibleChatMessages(next, "sess_1").map((message) => message.content)).toEqual([
      "Final answer",
    ]);
  });

  it("prefers completed content when streaming content only carried runtime progress", () => {
    const current: ChatMessageView[] = [
      {
        id: "msg_backend_assistant",
        sessionId: "sess_1",
        taskId: "task_1",
        role: "assistant",
        content: "Building context and preparing the first tool calls...Here’s what I found:",
        createdAt: 1,
        updatedAt: 2,
        streaming: true,
        status: "streaming",
      },
    ];

    const next = appendOrUpdateAssistantMessageCompletion(current, {
      messageId: "msg_backend_assistant",
      sessionId: "sess_1",
      taskId: "task_1",
      content: "Here’s what I found:\n\n1. **Project structure**\n- README.md\n- src/ledger.py",
      now: 5,
    });

    expect(next[0]).toMatchObject({
      content: "Here’s what I found:\n\n1. **Project structure**\n- README.md\n- src/ledger.py",
      streaming: false,
      status: "completed",
    });
  });

  it("merges compact tool-use and tool-result chat blocks by tool id", () => {
    const withInput = appendOrUpdateAssistantToolInputDelta([], {
      toolUseId: "tc_1",
      toolName: "run_command",
      target: "npm test",
      inputSummary: "npm test",
      sessionId: "sess_1",
      taskId: "task_1",
      delta: "{\"command\":\"npm",
      now: 1,
    });
    const completed = completeAssistantToolUseMessage(withInput, {
      toolUseId: "tc_1",
      toolName: "run_command",
      sessionId: "sess_1",
      taskId: "task_1",
      input: { command: "npm test" },
      now: 2,
    });
    const withResult = appendAssistantToolResultMessage(completed, {
      toolUseId: "tc_1",
      toolName: "run_command",
      sessionId: "sess_1",
      taskId: "task_1",
      content: { status: "completed", exitCode: 0 },
      now: 3,
    });

    expect(getVisibleChatMessages(withResult, "sess_1").map((message) => message.id)).toEqual([
      "tool_activity:tc_1",
    ]);
    expect(withResult[0]).toMatchObject({
      id: "tool_activity:tc_1",
      toolName: "run_command",
      streaming: false,
      metadata: {
        kind: "tool_activity",
        toolUseId: "tc_1",
        inputText: '{\n  "command": "npm test"\n}',
        resultText: '{\n  "status": "completed",\n  "exitCode": 0\n}',
        isError: false,
      },
    });
  });

  it("streams tool output into the matching tool activity block", () => {
    const started = completeAssistantToolUseMessage(
      appendOrUpdateAssistantToolStartMessage([], {
        toolUseId: "tc_1",
        toolName: "run_command",
        sessionId: "sess_1",
        taskId: "task_1",
        now: 1,
      }),
      {
        toolUseId: "tc_1",
        toolName: "run_command",
        sessionId: "sess_1",
        taskId: "task_1",
        input: { command: "npm test" },
        now: 2,
      },
    );
    const withStdout = appendOrUpdateAssistantToolOutputDelta(started, {
      toolUseId: "tc_1",
      toolName: "run_command",
      target: "npm test",
      inputSummary: "npm test",
      sessionId: "sess_1",
      taskId: "task_1",
      stream: "stdout",
      delta: "first line\n",
      now: 3,
    });
    const withStderr = appendOrUpdateAssistantToolOutputDelta(withStdout, {
      toolUseId: "tc_1",
      toolName: "run_command",
      sessionId: "sess_1",
      taskId: "task_1",
      stream: "stderr",
      delta: "warning\n",
      now: 4,
    });
    const completed = appendAssistantToolResultMessage(withStderr, {
      toolUseId: "tc_1",
      toolName: "run_command",
      sessionId: "sess_1",
      taskId: "task_1",
      content: { status: "completed", exitCode: 0 },
      resultSummary: "exit 0: ok",
      now: 5,
    });

    expect(completed[0]).toMatchObject({
      id: "tool_activity:tc_1",
      streaming: false,
      metadata: {
        target: "npm test",
        inputSummary: "npm test",
        output: {
          stdout: "first line\n",
          stderr: "warning\n",
          result: "",
        },
        resultText: "stdout\nfirst line\n\nstderr\nwarning\n\nexit 0: ok",
      },
    });
  });

  it("streams non-command tool result previews into the matching activity block", () => {
    const started = completeAssistantToolUseMessage(
      appendOrUpdateAssistantToolStartMessage([], {
        toolUseId: "tc_preview",
        toolName: "search_files",
        sessionId: "sess_1",
        taskId: "task_1",
        now: 1,
      }),
      {
        toolUseId: "tc_preview",
        toolName: "search_files",
        sessionId: "sess_1",
        taskId: "task_1",
        input: { query: "needle" },
        now: 2,
      },
    );
    const withActivity = appendOrUpdateAssistantToolOutputDelta(started, {
      toolUseId: "tc_preview",
      toolName: "search_files",
      sessionId: "sess_1",
      taskId: "task_1",
      stream: "activity",
      delta: "正在搜索文件：search needle\n",
      now: 3,
    });
    const withPreview = appendOrUpdateAssistantToolOutputDelta(withActivity, {
      toolUseId: "tc_preview",
      toolName: "search_files",
      sessionId: "sess_1",
      taskId: "task_1",
      stream: "result_preview",
      delta: "命中: 2 项\n样例: src/app.ts\n",
      now: 4,
    });

    expect(withPreview[0]).toMatchObject({
      metadata: {
        output: {
          activity: "正在搜索文件：search needle\n",
          result: "命中: 2 项\n样例: src/app.ts\n",
        },
        resultText: "过程\n正在搜索文件：search needle\n\n结果预览\n命中: 2 项\n样例: src/app.ts",
      },
    });
  });

  it("appends multiple structured tool activity deltas before result previews", () => {
    const started = completeAssistantToolUseMessage(
      appendOrUpdateAssistantToolStartMessage([], {
        toolUseId: "tc_steps",
        toolName: "mcp__docs__lookup",
        sessionId: "sess_1",
        taskId: "task_1",
        now: 1,
      }),
      {
        toolUseId: "tc_steps",
        toolName: "mcp__docs__lookup",
        sessionId: "sess_1",
        taskId: "task_1",
        input: { query: "install guide" },
        now: 2,
      },
    );
    const withFirstStep = appendOrUpdateAssistantToolOutputDelta(started, {
      toolUseId: "tc_steps",
      toolName: "mcp__docs__lookup",
      sessionId: "sess_1",
      taskId: "task_1",
      stream: "activity",
      delta: "connect (completed): opened docs index\n",
      now: 3,
    });
    const withSecondStep = appendOrUpdateAssistantToolOutputDelta(withFirstStep, {
      toolUseId: "tc_steps",
      toolName: "mcp__docs__lookup",
      sessionId: "sess_1",
      taskId: "task_1",
      stream: "activity",
      delta: "search (completed): matched install guide\n",
      now: 4,
    });
    const withPreview = appendOrUpdateAssistantToolOutputDelta(withSecondStep, {
      toolUseId: "tc_steps",
      toolName: "mcp__docs__lookup",
      sessionId: "sess_1",
      taskId: "task_1",
      stream: "result_preview",
      delta: "摘要: found 2 docs\n",
      now: 5,
    });

    expect(withPreview[0]).toMatchObject({
      metadata: {
        output: {
          activity: "connect (completed): opened docs index\nsearch (completed): matched install guide\n",
          result: "摘要: found 2 docs\n",
        },
      },
    });
    expect(withPreview[0].metadata?.resultText).toContain("connect (completed): opened docs index");
    expect(withPreview[0].metadata?.resultText).toContain("search (completed): matched install guide");
    expect(withPreview[0].metadata?.resultText).toContain("摘要: found 2 docs");
  });

  it("preserves structured tool summaries on merged tool result blocks", () => {
    const withInput = completeAssistantToolUseMessage(
      appendOrUpdateAssistantToolStartMessage([], {
        toolUseId: "tc_2",
        toolName: "read_file",
        sessionId: "sess_1",
        taskId: "task_1",
        now: 1,
      }),
      {
        toolUseId: "tc_2",
        toolName: "read_file",
        sessionId: "sess_1",
        taskId: "task_1",
        input: { path: "src/app.ts" },
        now: 2,
      },
    );
    const withResult = appendAssistantToolResultMessage(withInput, {
      toolUseId: "tc_2",
      toolName: "read_file",
      sessionId: "sess_1",
      taskId: "task_1",
      content: { content: "const app = true;" },
      target: "src/app.ts",
      inputSummary: "read src/app.ts",
      resultSummary: "read src/app.ts (17 chars)",
      durationMs: 37,
      resultPreview: [{ label: "文件", value: "src/app.ts" }],
      now: 3,
    });

    expect(withResult[0].metadata?.durationMs).toBe(37);
    expect(withResult[0]).toMatchObject({
      id: "tool_activity:tc_2",
      content: '{\n  "path": "src/app.ts"\n}',
      metadata: {
        target: "src/app.ts",
        inputSummary: "read src/app.ts",
        resultSummary: "read src/app.ts (17 chars)",
        resultPreview: [{ label: "文件", value: "src/app.ts" }],
        resultText: "read src/app.ts (17 chars)",
      },
    });
  });

  it("shows a content_start tool block before input deltas arrive", () => {
    const started = appendOrUpdateAssistantToolStartMessage([], {
      toolUseId: "tc_1",
      toolName: "read_file",
      target: "src/index.ts",
      inputSummary: "read src/index.ts",
      parentToolUseId: "parent_1",
      toolGroupId: "tgrp_1",
      toolIndex: 1,
      toolTotal: 2,
      toolOperationId: "context:path:src/index.ts",
      toolOperationLabel: "读取上下文",
      toolCategory: "context_read",
      toolPhaseId: "context_read",
      toolPhaseLabel: "读取上下文",
      toolSemanticParentId: "phase:context_read",
      toolSemanticParentLabel: "读取上下文",
      sessionId: "sess_1",
      taskId: "task_1",
      now: 1,
    });

    expect(getVisibleChatMessages(started, "sess_1")).toEqual([
      expect.objectContaining({
        id: "tool_use:tc_1",
        streaming: true,
        toolName: "read_file",
        metadata: {
          kind: "tool_use",
          toolUseId: "tc_1",
          target: "src/index.ts",
          inputSummary: "read src/index.ts",
          parentToolUseId: "parent_1",
          toolGroupId: "tgrp_1",
          toolIndex: 1,
          toolTotal: 2,
          toolOperationId: "context:path:src/index.ts",
          toolOperationLabel: "读取上下文",
          toolCategory: "context_read",
          toolPhaseId: "context_read",
          toolPhaseLabel: "读取上下文",
          toolSemanticParentId: "phase:context_read",
          toolSemanticParentLabel: "读取上下文",
        },
      }),
    ]);

    const completed = completeAssistantToolUseMessage(started, {
      toolUseId: "tc_1",
      toolName: "read_file",
      target: "src/index.ts",
      inputSummary: "read src/index.ts",
      parentToolUseId: "parent_1",
      toolGroupId: "tgrp_1",
      toolIndex: 1,
      toolTotal: 2,
      toolOperationId: "context:path:src/index.ts",
      toolOperationLabel: "读取上下文",
      toolCategory: "context_read",
      toolPhaseId: "context_read",
      toolPhaseLabel: "读取上下文",
      toolSemanticParentId: "phase:context_read",
      toolSemanticParentLabel: "读取上下文",
      sessionId: "sess_1",
      taskId: "task_1",
      input: { path: "src/index.ts" },
      now: 2,
    });

    expect(completed[0]).toMatchObject({
      streaming: false,
      status: "completed",
      metadata: {
        target: "src/index.ts",
        inputSummary: "read src/index.ts",
        parentToolUseId: "parent_1",
        toolGroupId: "tgrp_1",
        toolIndex: 1,
        toolTotal: 2,
        toolOperationId: "context:path:src/index.ts",
        toolOperationLabel: "读取上下文",
        toolCategory: "context_read",
        toolPhaseId: "context_read",
        toolPhaseLabel: "读取上下文",
        toolSemanticParentId: "phase:context_read",
        toolSemanticParentLabel: "读取上下文",
        inputText: '{\n  "path": "src/index.ts"\n}',
      },
    });
  });

  it("keeps raw lifecycle tool starts streaming while preserving arguments", () => {
    const started = appendOrUpdateAssistantToolStartMessage([], {
      toolUseId: "tc_raw",
      toolName: "read_file",
      input: { path: "src/app.ts" },
      target: "src/app.ts",
      inputSummary: "read src/app.ts",
      sessionId: "sess_1",
      taskId: "task_1",
      now: 1,
    });

    expect(started[0]).toMatchObject({
      id: "tool_use:tc_raw",
      streaming: true,
      status: "streaming",
      metadata: {
        kind: "tool_use",
        toolUseId: "tc_raw",
        target: "src/app.ts",
        inputSummary: "read src/app.ts",
        input: { path: "src/app.ts" },
        inputText: '{\n  "path": "src/app.ts"\n}',
      },
    });
  });

  it("records haha-style special transcript events", () => {
    const next = appendSpecialEventMessage([], {
      kind: "api_retry",
      sessionId: "sess_1",
      taskId: "task_1",
      title: "API 重试",
      summary: "模型请求失败，正在重试。",
      status: "warning",
      eventId: "evt_retry",
      metadata: { attempt: 2 },
      now: 1,
    });

    expect(getVisibleChatMessages(next, "sess_1")).toEqual([
      expect.objectContaining({
        id: "api_retry:evt_retry",
        role: "assistant",
        content: "模型请求失败，正在重试。",
        status: "completed",
        metadata: {
          kind: "api_retry",
          title: "API 重试",
          summary: "模型请求失败，正在重试。",
          status: "warning",
          attempt: 2,
        },
      }),
    ]);
  });

  it("keeps haha-style special transcript events across persisted message refreshes", () => {
    const localEvents = appendSpecialEventMessage([], {
      kind: "slash_command",
      sessionId: "sess_1",
      taskId: "task_1",
      content: "**运行时：** 本地运行时已连接",
      title: "/status result",
      summary: "本地运行时已连接",
      eventId: "evt_status",
      metadata: { command: "/status", args: "" },
      now: 10,
    });

    const refreshed = replaceSessionMessages(localEvents, "sess_1", [
      {
        id: "assistant_final",
        sessionId: "sess_1",
        taskId: "task_1",
        role: "assistant",
        content: "最终结论。",
        createdAt: 20,
      },
    ]);

    expect(getVisibleChatMessages(refreshed, "sess_1").map((message) => message.id)).toEqual([
      "slash_command:evt_status",
      "assistant_final",
    ]);
  });

  it("keeps system special transcript events across persisted message refreshes", () => {
    const localEvents = appendSpecialEventMessage([], {
      kind: "system",
      sessionId: "sess_1",
      taskId: "task_1",
      content: "Switched to fallback model",
      title: "Provider notice",
      eventId: "evt_system",
      metadata: { model: "fallback-model" },
      now: 10,
    });

    const refreshed = replaceSessionMessages(localEvents, "sess_1", [
      {
        id: "assistant_final",
        sessionId: "sess_1",
        taskId: "task_1",
        role: "assistant",
        content: "Done.",
        createdAt: 20,
      },
    ]);

    expect(getVisibleChatMessages(refreshed, "sess_1").map((message) => message.id)).toEqual([
      "system:evt_system",
      "assistant_final",
    ]);
  });

  it("merges out-of-order tool result and completed input into one activity block", () => {
    const withResult = appendAssistantToolResultMessage([], {
      toolUseId: "tc_1",
      toolName: "run_command",
      sessionId: "sess_1",
      taskId: "task_1",
      content: { status: "completed" },
      now: 1,
    });
    const completed = completeAssistantToolUseMessage(withResult, {
      toolUseId: "tc_1",
      toolName: "run_command",
      sessionId: "sess_1",
      taskId: "task_1",
      input: { command: "npm test" },
      now: 2,
    });
    const merged = appendAssistantToolResultMessage(completed, {
      toolUseId: "tc_1",
      toolName: "run_command",
      target: "npm test",
      inputSummary: "npm test",
      sessionId: "sess_1",
      taskId: "task_1",
      content: { status: "completed" },
      now: 3,
    });

    expect(getVisibleChatMessages(merged, "sess_1").map((message) => message.id)).toEqual([
      "tool_activity:tc_1",
    ]);
    expect(merged[0]).toMatchObject({
      metadata: {
        kind: "tool_activity",
        target: "npm test",
        inputSummary: "npm test",
        inputText: '{\n  "command": "npm test"\n}',
        resultText: '{\n  "status": "completed"\n}',
      },
    });
  });

  it("keeps chat block messages across persisted message refreshes", () => {
    const toolBlocks = appendAssistantToolResultMessage([], {
      toolUseId: "tc_1",
      toolName: "read_file",
      sessionId: "sess_1",
      taskId: "task_1",
      content: "ok",
      durationMs: 12,
      now: 3,
    });

    const next = replaceSessionMessages(toolBlocks, "sess_1", [
      {
        id: "stored_user",
        sessionId: "sess_1",
        role: "user",
        content: "read file",
        createdAt: 1,
      },
    ]);

    expect(getVisibleChatMessages(next, "sess_1").map((message) => message.id)).toEqual([
      "stored_user",
      "tool_result:tc_1",
    ]);
    expect(getVisibleChatMessages(next, "sess_1")[1].metadata?.durationMs).toBe(12);
  });

  it("completes all streaming chat-compat blocks for a task", () => {
    const streaming = appendOrUpdateAssistantToolInputDelta([], {
      toolUseId: "tc_1",
      toolName: "run_command",
      sessionId: "sess_1",
      taskId: "task_1",
      delta: "{\"command\":\"npm test\"}",
      now: 1,
    });

    const completed = completeChatCompatMessage(streaming, {
      sessionId: "sess_1",
      taskId: "task_1",
      now: 2,
    });

    expect(completed[0]).toMatchObject({ streaming: false, status: "completed" });
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

  it("replaces a streaming backend shell when the completed persisted message arrives", () => {
    const persisted: MessageRecord[] = [
      {
        id: "msg_backend_assistant",
        sessionId: "sess_1",
        taskId: "task_1",
        role: "assistant",
        content: "Final persisted answer with enough detail to render in the conversation.",
        createdAt: 10,
        updatedAt: 30,
        status: "completed",
      },
    ];
    const localMessages: ChatMessageView[] = [
      {
        id: "msg_backend_assistant",
        sessionId: "sess_1",
        taskId: "task_1",
        role: "assistant",
        content: "Building context and preparing the first tool calls...",
        createdAt: 10,
        updatedAt: 11,
        streaming: true,
        status: "streaming",
      },
    ];

    const next = replaceSessionMessages(localMessages, "sess_1", persisted);

    const visible = getVisibleChatMessages(next, "sess_1");

    expect(next).toHaveLength(1);
    expect(visible).toHaveLength(1);
    expect(visible[0]).toMatchObject({
      id: "msg_backend_assistant",
      content: "Final persisted answer with enough detail to render in the conversation.",
      status: "completed",
    });
    expect(visible[0]).not.toHaveProperty("streaming");
  });
});
