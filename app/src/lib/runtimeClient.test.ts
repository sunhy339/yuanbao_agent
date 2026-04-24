import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RuntimeClient } from "./runtimeClient";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("RuntimeClient schedule fallback", () => {
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
