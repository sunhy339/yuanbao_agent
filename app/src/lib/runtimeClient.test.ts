import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const invokeMock = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({
  invoke: invokeMock,
}));

vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(),
}));

import { RuntimeClient } from "./runtimeClient";

beforeEach(() => {
  vi.useFakeTimers();
  invokeMock.mockReset();
  delete (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
  delete (window as Window & { __TAURI__?: unknown }).__TAURI__;
  delete (window as Window & { __YUANBAO_ENABLE_BROWSER_MOCK__?: unknown }).__YUANBAO_ENABLE_BROWSER_MOCK__;
});

afterEach(() => {
  vi.useRealTimers();
  delete (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
  delete (window as Window & { __TAURI__?: unknown }).__TAURI__;
  delete (window as Window & { __YUANBAO_ENABLE_BROWSER_MOCK__?: unknown }).__YUANBAO_ENABLE_BROWSER_MOCK__;
});

describe("RuntimeClient schedule fallback", () => {
  beforeEach(() => {
    (window as Window & { __YUANBAO_ENABLE_BROWSER_MOCK__?: unknown }).__YUANBAO_ENABLE_BROWSER_MOCK__ = true;
  });

  it("creates, lists, toggles, runs, and lists logs in browser mode", async () => {
    const client = new RuntimeClient();

    const created = await client.createScheduledTask({
      name: "Morning check",
      prompt: "Summarize changed files",
      schedule: "every 30 minutes",
      enabled: true,
    });

    expect(created.task.id).toMatch(/^sched_/);
    expect(created.task.status).toBe("active");
    expect(created.task.nextRunAt).toBeTypeOf("number");

    await client.toggleScheduledTask({ taskId: created.task.id, enabled: false });
    const listed = await client.listScheduledTasks();
    expect(listed.tasks.find((task) => task.id === created.task.id)?.enabled).toBe(false);

    const run = await client.runScheduledTaskNow({ taskId: created.task.id });
    expect(run.run.taskId).toBe(created.task.id);
    expect(run.run.status).toBe("completed");

    const logs = await client.listScheduledTaskLogs({ taskId: created.task.id });
    expect(logs.logs.some((log) => log.id === run.run.id)).toBe(true);
  });
});

describe("RuntimeClient command log fallback", () => {
  beforeEach(() => {
    (window as Window & { __YUANBAO_ENABLE_BROWSER_MOCK__?: unknown }).__YUANBAO_ENABLE_BROWSER_MOCK__ = true;
  });

  it("lists, gets, and cancels command jobs from browser trace state", async () => {
    const client = new RuntimeClient();
    const session = await client.createSession({
      workspaceId: "workspace_mock",
      title: "Command controls",
    });
    const message = await client.sendMessage({
      sessionId: session.session.id,
      content: "Run the focused test",
      attachments: [],
    });

    await vi.advanceTimersByTimeAsync(400);

    const traces = await client.listTrace({ taskId: message.task.id, limit: 50 });
    const approvalTrace = traces.traceEvents.find((trace) => trace.type === "approval.requested");
    const approvalId =
      approvalTrace?.payload && typeof approvalTrace.payload === "object"
        ? (approvalTrace.payload as Record<string, unknown>).approvalId
        : null;
    expect(approvalId).toEqual(expect.any(String));

    await client.approvalSubmit({
      approvalId: String(approvalId),
      decision: "approved",
    });

    const listed = await client.commandLogList({ sessionId: session.session.id, status: "completed" });
    expect(listed.commandLogs).toHaveLength(1);
    expect(listed.commandLogs[0]).toMatchObject({
      taskId: message.task.id,
      command: "pytest",
      cwd: ".",
      shell: "powershell",
      status: "completed",
      exitCode: 0,
      stdout: expect.stringContaining("Approved command finished successfully"),
    });

    const fetched = await client.commandLogGet({ commandId: listed.commandLogs[0].id });
    expect(fetched.commandLog.id).toBe(listed.commandLogs[0].id);

    const cancelled = await client.commandCancel({ commandId: listed.commandLogs[0].id });
    expect(cancelled.commandLog).toMatchObject({
      id: listed.commandLogs[0].id,
      status: "killed",
    });

    const afterCancel = await client.commandLogGet({ commandId: listed.commandLogs[0].id });
    expect(afterCancel.commandLog.status).toBe("killed");
  });
});

describe("RuntimeClient message fallback", () => {
  beforeEach(() => {
    (window as Window & { __YUANBAO_ENABLE_BROWSER_MOCK__?: unknown }).__YUANBAO_ENABLE_BROWSER_MOCK__ = true;
  });

  it("persists browser mock user and assistant messages by session", async () => {
    const client = new RuntimeClient();
    const firstSession = await client.createSession({
      workspaceId: "workspace_mock",
      title: "First",
    });
    await vi.advanceTimersByTimeAsync(1);
    const secondSession = await client.createSession({
      workspaceId: "workspace_mock",
      title: "Second",
    });

    await client.sendMessage({
      sessionId: firstSession.session.id,
      content: "remember me",
      attachments: [],
    });
    await vi.advanceTimersByTimeAsync(1);
    await client.sendMessage({
      sessionId: secondSession.session.id,
      content: "do not leak",
      attachments: [],
    });

    expect((await client.listMessages({ sessionId: firstSession.session.id })).messages).toMatchObject([
      {
        sessionId: firstSession.session.id,
        role: "user",
        content: "remember me",
      },
    ]);

    await vi.advanceTimersByTimeAsync(230);

    const listed = await client.listMessages({ sessionId: firstSession.session.id });
    expect(listed.messages.map((message) => message.content)).toEqual([
      "remember me",
      "Browser mock assistant response completed.",
    ]);
  });
});

describe("RuntimeClient desktop transport", () => {
  beforeEach(() => {
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      value: {},
      configurable: true,
    });
  });

  it("rejects browser runtime calls unless mock mode is explicitly enabled", async () => {
    delete (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;

    const client = new RuntimeClient();

    await expect(client.getConfig()).rejects.toThrow("Desktop runtime bridge is unavailable");
    await expect(client.sendMessage({ sessionId: "session", content: "hello", attachments: [] })).rejects.toThrow(
      "Desktop runtime bridge is unavailable",
    );
  });

  it("does not silently fallback to mock data when Tauri config/provider calls fail", async () => {
    const client = new RuntimeClient();

    invokeMock.mockRejectedValueOnce(new Error("runtime config failed"));
    await expect(
      client.updateConfig({
        config: {
          provider: {
            mode: "mock",
          },
        },
      }),
    ).rejects.toThrow("runtime config failed");

    invokeMock.mockRejectedValueOnce(new Error("provider unavailable"));
    await expect(client.testProvider({ profileId: "real" })).rejects.toThrow(
      "provider unavailable",
    );
  });

  it("wraps dynamic config/provider payloads for Tauri command arguments", async () => {
    const client = new RuntimeClient();
    const configPayload = {
      config: {
        provider: {
          mode: "mock" as const,
        },
      },
    };

    invokeMock.mockResolvedValueOnce({ config: configPayload.config });
    await client.updateConfig(configPayload);
    expect(invokeMock).toHaveBeenLastCalledWith("config_update", { payload: configPayload });

    const providerPayload = { profileId: "real" };
    invokeMock.mockResolvedValueOnce({ ok: true, status: "ok" });
    await client.testProvider(providerPayload);
    expect(invokeMock).toHaveBeenLastCalledWith("provider_test", { payload: providerPayload });
  });

  it("wraps workspace memory clear payload for Tauri command arguments", async () => {
    const client = new RuntimeClient();
    const workspace = {
      id: "ws_real",
      name: "Real",
      rootPath: "D:/project",
      summary: null,
      createdAt: 1,
      updatedAt: 2,
    };

    invokeMock.mockResolvedValueOnce({ workspace });

    await expect(client.clearWorkspaceMemory({ workspaceId: "ws_real" })).resolves.toEqual({ workspace });
    expect(invokeMock).toHaveBeenLastCalledWith("workspace_memory_clear", {
      payload: { workspaceId: "ws_real" },
    });
  });

  it("wraps workspace focus update payload for Tauri command arguments", async () => {
    const client = new RuntimeClient();
    const workspace = {
      id: "ws_real",
      name: "Real",
      rootPath: "D:/project",
      focus: "Keep scope narrow.",
      summary: null,
      createdAt: 1,
      updatedAt: 2,
    };

    invokeMock.mockResolvedValueOnce({ workspace });

    await expect(
      client.updateWorkspaceFocus({ workspaceId: "ws_real", focus: "Keep scope narrow." }),
    ).resolves.toEqual({ workspace });
    expect(invokeMock).toHaveBeenLastCalledWith("workspace_focus_update", {
      payload: { workspaceId: "ws_real", focus: "Keep scope narrow." },
    });
  });

  it("wraps message list payload for Tauri command arguments", async () => {
    const client = new RuntimeClient();
    const messages = [
      {
        id: "msg_real",
        sessionId: "sess_real",
        role: "user",
        content: "hello",
        createdAt: 1,
      },
    ];

    invokeMock.mockResolvedValueOnce({ messages });

    await expect(client.listMessages({ sessionId: "sess_real", limit: 50 })).resolves.toEqual({
      messages,
    });
    expect(invokeMock).toHaveBeenLastCalledWith("message_list", {
      payload: { sessionId: "sess_real", limit: 50 },
    });
  });

  it.each([
    ["session list", () => new RuntimeClient().listSessions()],
    ["message list", () => new RuntimeClient().listMessages({ sessionId: "sess_real" })],
    ["diff get", () => new RuntimeClient().diffGet({ patchId: "patch_real" })],
    ["command log list", () => new RuntimeClient().commandLogList({ taskId: "task_real" })],
    ["command log get", () => new RuntimeClient().commandLogGet({ commandId: "cmd_real" })],
    ["command cancel", () => new RuntimeClient().commandCancel({ commandId: "cmd_real" })],
    ["task cancel", () => new RuntimeClient().cancelTask({ taskId: "task_real" })],
    ["task pause", () => new RuntimeClient().pauseTask({ taskId: "task_real" })],
    ["task resume", () => new RuntimeClient().resumeTask({ taskId: "task_real" })],
    ["trace list", () => new RuntimeClient().listTrace({ taskId: "task_real" })],
  ])("propagates %s failures instead of using browser mock state", async (_name, run) => {
    invokeMock.mockRejectedValueOnce(new Error("runtime bridge failed"));

    await expect(run()).rejects.toThrow("runtime bridge failed");
  });
});
