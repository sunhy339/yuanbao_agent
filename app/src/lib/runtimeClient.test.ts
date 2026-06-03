import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const invokeMock = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({
  invoke: invokeMock,
}));

vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(),
}));

import { listen } from "@tauri-apps/api/event";
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

// NOTE: Browser mock fallback tests removed — RuntimeClient no longer supports
// browser-only mode. All RPC calls require the Tauri bridge.

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

    await expect(client.getConfig()).rejects.toThrow("桌面运行时桥接不可用");
    await expect(client.sendMessage({ sessionId: "session", content: "hello", attachments: [] })).rejects.toThrow(
      "桌面运行时桥接不可用",
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

  it("sends explicit empty permission capability maps instead of reusing stale rules", async () => {
    const client = new RuntimeClient();
    const configPayload = {
      config: {
        policy: {
          approvalMode: "none" as const,
        },
        permissions: {
          preset: "autonomous" as const,
          capabilities: {},
        },
      },
    };

    invokeMock.mockResolvedValueOnce({ config: configPayload.config });
    await client.updateConfig(configPayload);

    expect(invokeMock).toHaveBeenLastCalledWith("config_update", { payload: configPayload });
  });

  it("checks computer use capability probe through the Tauri command", async () => {
    const client = new RuntimeClient();
    const probe = {
      status: "degraded",
      checkedAt: 1780200000000,
      platform: "windows",
      desktopBridge: true,
      runtimeRunning: true,
      capabilities: [
        {
          id: "screen-observation",
          label: "屏幕观察",
          state: "ready",
          detail: "Pillow ImageGrab 可导入。",
        },
      ],
    };

    invokeMock.mockResolvedValueOnce(probe);

    await expect(client.probeComputerUse()).resolves.toEqual(probe);
    expect(invokeMock).toHaveBeenLastCalledWith("computer_use_probe", undefined);
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

  it("wraps workspace memory init payload for Tauri command arguments", async () => {
    const client = new RuntimeClient();
    const workspace = {
      id: "ws_real",
      name: "Real",
      rootPath: "D:/project",
      summary: null,
      createdAt: 1,
      updatedAt: 2,
    };

    invokeMock.mockResolvedValueOnce({
      workspace,
      createdFiles: ["YUANBAO.md", "MEMORY.md", "MEMORY.local.md"],
      existingFiles: [],
    });

    await expect(client.initWorkspaceMemory({ workspaceId: "ws_real" })).resolves.toEqual({
      workspace,
      createdFiles: ["YUANBAO.md", "MEMORY.md", "MEMORY.local.md"],
      existingFiles: [],
    });
    expect(invokeMock).toHaveBeenLastCalledWith("workspace_memory_init", {
      payload: { workspaceId: "ws_real" },
    });
  });

  it("wraps workspace file search payload for Tauri command arguments", async () => {
    const client = new RuntimeClient();
    const result = {
      rootPath: "D:/project",
      query: "app",
      entries: [{ name: "App.tsx", path: "src/App.tsx", kind: "file" }],
      truncated: false,
      scanned: 42,
    };

    invokeMock.mockResolvedValueOnce(result);

    await expect(
      client.workspaceFileSearch({ workspaceRoot: "D:/project", query: "app", maxEntries: 12 }),
    ).resolves.toEqual(result);
    expect(invokeMock).toHaveBeenLastCalledWith("workspace_file_search", {
      payload: { workspaceRoot: "D:/project", query: "app", maxEntries: 12 },
    });
  });

  it("wraps local terminal PTY commands and subscribes to terminal events", async () => {
    const client = new RuntimeClient();
    const terminal = {
      id: "term_1",
      cwd: "D:/project",
      shell: "powershell.exe",
      status: "running",
      startedAt: 1,
    };
    const listenMock = vi.mocked(listen);
    const unlisten = vi.fn();
    listenMock.mockResolvedValueOnce(unlisten);

    invokeMock.mockResolvedValueOnce({ terminal });
    await expect(client.terminalStart({ cwd: "D:/project", cols: 100, rows: 30 })).resolves.toEqual({ terminal });
    expect(invokeMock).toHaveBeenLastCalledWith("terminal_start", {
      payload: { cwd: "D:/project", cols: 100, rows: 30 },
    });

    invokeMock.mockResolvedValueOnce({ terminal });
    await expect(client.terminalWrite({ terminalId: "term_1", data: "dir\r" })).resolves.toEqual({ terminal });
    expect(invokeMock).toHaveBeenLastCalledWith("terminal_write", {
      payload: { terminalId: "term_1", data: "dir\r" },
    });

    invokeMock.mockResolvedValueOnce({ terminal });
    await expect(client.terminalResize({ terminalId: "term_1", cols: 120, rows: 32 })).resolves.toEqual({ terminal });
    expect(invokeMock).toHaveBeenLastCalledWith("terminal_resize", {
      payload: { terminalId: "term_1", cols: 120, rows: 32 },
    });

    invokeMock.mockResolvedValueOnce({ terminal: { ...terminal, status: "exited" } });
    await expect(client.terminalStop({ terminalId: "term_1" })).resolves.toEqual({
      terminal: { ...terminal, status: "exited" },
    });
    expect(invokeMock).toHaveBeenLastCalledWith("terminal_stop", {
      payload: { terminalId: "term_1" },
    });

    const handler = vi.fn();
    const unsubscribe = await client.subscribeTerminalEvents(handler);
    expect(listenMock).toHaveBeenLastCalledWith("terminal://event", expect.any(Function));
    unsubscribe();
    expect(unlisten).toHaveBeenCalled();
  });

  it("subscribes to Yuanbao server messages", async () => {
    const client = new RuntimeClient();
    const listenMock = vi.mocked(listen);
    const unlisten = vi.fn();
    listenMock.mockResolvedValueOnce(unlisten);

    const handler = vi.fn();
    const unsubscribe = await client.subscribeYuanbaoMessages(handler);

    expect(listenMock).toHaveBeenLastCalledWith("yuanbao://message", expect.any(Function));
    const listener = listenMock.mock.calls.at(-1)?.[1] as (event: { payload: unknown }) => void;
    listener({ payload: { type: "content_delta", text: "hello" } });
    expect(handler).toHaveBeenCalledWith({ type: "content_delta", text: "hello" });

    unsubscribe();
    expect(unlisten).toHaveBeenCalled();
  });

  it("keeps legacy haha-cc compatible server message subscriptions as fallback", async () => {
    const client = new RuntimeClient();
    const listenMock = vi.mocked(listen);
    const unlisten = vi.fn();
    listenMock.mockResolvedValueOnce(unlisten);

    const handler = vi.fn();
    const unsubscribe = await client.subscribeHahaCcMessages(handler);

    expect(listenMock).toHaveBeenLastCalledWith("haha-cc://message", expect.any(Function));
    const listener = listenMock.mock.calls.at(-1)?.[1] as (event: { payload: unknown }) => void;
    listener({ payload: { type: "content_delta", text: "hello" } });
    expect(handler).toHaveBeenCalledWith({ type: "content_delta", text: "hello" });

    unsubscribe();
    expect(unlisten).toHaveBeenCalled();
  });

  it("wraps runtime ping for Yuanbao connected and pong messages", async () => {
    const client = new RuntimeClient();
    const result = {
      ok: true,
      transport: "json-rpc-stdio",
      yuanbaoMessages: [
        { type: "connected", sessionId: "sess_1" },
        { type: "pong" },
      ],
      hahaCcMessages: [
        { type: "connected", sessionId: "sess_1" },
        { type: "pong" },
      ],
      connected: { type: "connected", sessionId: "sess_1" },
      pong: { type: "pong" },
    };

    invokeMock.mockResolvedValueOnce(result);

    await expect(client.runtimePing({ sessionId: "sess_1", taskId: "task_1" })).resolves.toEqual(result);
    expect(invokeMock).toHaveBeenLastCalledWith("runtime_ping", {
      payload: { sessionId: "sess_1", taskId: "task_1" },
    });
  });

  it("fetches Yuanbao messages after a sequence", async () => {
    const client = new RuntimeClient();
    const result = {
      messages: [
        { type: "content_delta", text: "hello" },
        { type: "thinking", text: "plan" },
      ],
      lastSeq: 7,
      truncated: false,
    };

    invokeMock.mockResolvedValueOnce(result);

    await expect(client.yuanbaoEventsAfter({ sessionId: "sess_1", afterSeq: 3, limit: 100 })).resolves.toEqual(result);
    expect(invokeMock).toHaveBeenLastCalledWith("yuanbao_events_after", {
      payload: { sessionId: "sess_1", afterSeq: 3, limit: 100 },
    });
  });

  it("keeps legacy haha-cc message polling as fallback", async () => {
    const client = new RuntimeClient();
    const result = {
      messages: [
        { type: "content_delta", text: "hello" },
        { type: "thinking", text: "plan" },
      ],
      lastSeq: 7,
      truncated: false,
    };

    invokeMock.mockResolvedValueOnce(result);

    await expect(client.hahaCcEventsAfter({ sessionId: "sess_1", afterSeq: 3, limit: 100 })).resolves.toEqual(result);
    expect(invokeMock).toHaveBeenLastCalledWith("yuanbao_events_after", {
      payload: { sessionId: "sess_1", afterSeq: 3, limit: 100 },
    });
  });

  it("fetches current Yuanbao team snapshot messages", async () => {
    const client = new RuntimeClient();
    const result = {
      teamName: "sess_1",
      messages: [
        { type: "team_created", teamName: "sess_1" },
        { type: "team_update", teamName: "sess_1", members: [] },
      ],
    };

    invokeMock.mockResolvedValueOnce(result);

    await expect(client.yuanbaoTeamSnapshot({ sessionId: "sess_1" })).resolves.toEqual(result);
    expect(invokeMock).toHaveBeenLastCalledWith("yuanbao_team_snapshot", {
      payload: { sessionId: "sess_1" },
    });
  });

  it("keeps legacy haha-cc team snapshot polling as fallback", async () => {
    const client = new RuntimeClient();
    const result = {
      teamName: "sess_1",
      messages: [
        { type: "team_created", teamName: "sess_1" },
        { type: "team_update", teamName: "sess_1", members: [] },
      ],
    };

    invokeMock.mockResolvedValueOnce(result);

    await expect(client.hahaCcTeamSnapshot({ sessionId: "sess_1" })).resolves.toEqual(result);
    expect(invokeMock).toHaveBeenLastCalledWith("yuanbao_team_snapshot", {
      payload: { sessionId: "sess_1" },
    });
  });

  it("connects Yuanbao messages with initial connected and keepalive pong", async () => {
    const client = new RuntimeClient();
    const listenMock = vi.mocked(listen);
    const unlisten = vi.fn();
    const handler = vi.fn();
    listenMock.mockResolvedValueOnce(unlisten);
    invokeMock
      .mockResolvedValueOnce({
        ok: true,
        transport: "json-rpc-stdio",
        yuanbaoMessages: [
          { type: "connected", sessionId: "sess_1" },
          { type: "pong" },
        ],
        hahaCcMessages: [
          { type: "connected", sessionId: "sess_1" },
          { type: "pong" },
        ],
        connected: { type: "connected", sessionId: "sess_1" },
        pong: { type: "pong" },
      })
      .mockResolvedValueOnce({
        ok: true,
        transport: "json-rpc-stdio",
        yuanbaoMessages: [
          { type: "connected", sessionId: "sess_1" },
          { type: "pong" },
        ],
        hahaCcMessages: [
          { type: "connected", sessionId: "sess_1" },
          { type: "pong" },
        ],
        connected: { type: "connected", sessionId: "sess_1" },
        pong: { type: "pong" },
      });

    const unsubscribe = await client.connectYuanbaoMessages(handler, {
      sessionId: "sess_1",
      taskId: "task_1",
      keepAliveMs: 1000,
    });

    expect(listenMock).toHaveBeenLastCalledWith("yuanbao://message", expect.any(Function));
    expect(handler).toHaveBeenCalledWith({ type: "connected", sessionId: "sess_1" });
    expect(handler).toHaveBeenCalledWith({ type: "pong" });
    expect(invokeMock).toHaveBeenLastCalledWith("runtime_ping", {
      payload: { sessionId: "sess_1", taskId: "task_1" },
    });

    handler.mockClear();
    await vi.advanceTimersByTimeAsync(1000);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith({ type: "pong" });

    unsubscribe();
    expect(unlisten).toHaveBeenCalled();
  });

  it("wraps local git management commands for the desktop workspace", async () => {
    const client = new RuntimeClient();
    const status = {
      cwd: "D:/project",
      repoRoot: "D:/project",
      branch: "main",
      dirtyFiles: 1,
      files: [{ path: "src/app.ts", status: "M", raw: " M src/app.ts" }],
      branches: [{ name: "main", current: true }],
      clean: false,
      rawStatus: "## main\n M src/app.ts\n",
    };

    invokeMock.mockResolvedValueOnce(status);
    await expect(client.gitLocalStatus({ cwd: "D:/project" })).resolves.toEqual(status);
    expect(invokeMock).toHaveBeenLastCalledWith("git_local_status", {
      payload: { cwd: "D:/project" },
    });

    const diff = {
      cwd: "D:/project",
      repoRoot: "D:/project",
      diff: "diff --git a/src/app.ts b/src/app.ts\n",
      stat: " src/app.ts | 1 +",
      files: ["src/app.ts"],
      truncated: false,
    };
    invokeMock.mockResolvedValueOnce(diff);
    await expect(client.gitLocalDiff({ cwd: "D:/project", path: "src/app.ts" })).resolves.toEqual(diff);
    expect(invokeMock).toHaveBeenLastCalledWith("git_local_diff", {
      payload: { cwd: "D:/project", path: "src/app.ts" },
    });

    invokeMock.mockResolvedValueOnce({ cwd: "D:/project", repoRoot: "D:/project", stdout: "Initialized", stderr: "", status });
    await expect(client.gitLocalInit({ cwd: "D:/project" })).resolves.toMatchObject({ stdout: "Initialized" });
    expect(invokeMock).toHaveBeenLastCalledWith("git_local_init", {
      payload: { cwd: "D:/project" },
    });

    invokeMock.mockResolvedValueOnce({ cwd: "D:/project", repoRoot: "D:/project", branch: "feature", stdout: "", stderr: "", status });
    await expect(client.gitLocalCheckout({ cwd: "D:/project", branch: "feature" })).resolves.toMatchObject({ branch: "feature" });
    expect(invokeMock).toHaveBeenLastCalledWith("git_local_checkout", {
      payload: { cwd: "D:/project", branch: "feature" },
    });

    invokeMock.mockResolvedValueOnce({ cwd: "D:/project", repoRoot: "D:/project", stdout: "[main abc] Update", stderr: "", status });
    await expect(client.gitLocalCommit({ cwd: "D:/project", message: "Update" })).resolves.toMatchObject({ stdout: "[main abc] Update" });
    expect(invokeMock).toHaveBeenLastCalledWith("git_local_commit", {
      payload: { cwd: "D:/project", message: "Update" },
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

  it("forwards supplement message metadata through the Tauri command payload", async () => {
    const client = new RuntimeClient();
    const task = {
      id: "task_real",
      sessionId: "sess_real",
      type: "chat",
      status: "running",
      goal: "continue",
      createdAt: 1,
      updatedAt: 2,
    };

    invokeMock.mockResolvedValueOnce({ task });

    await expect(
      client.sendMessage({
        sessionId: "sess_real",
        content: "one more detail",
        attachments: [],
        taskId: "task_real",
        mode: "supplement",
      }),
    ).resolves.toEqual({ task });
    expect(invokeMock).toHaveBeenLastCalledWith("message_send", {
      payload: {
        sessionId: "sess_real",
        content: "one more detail",
        attachments: [],
        taskId: "task_real",
        mode: "supplement",
      },
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
