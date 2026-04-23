import { describe, expect, it } from "vitest";
import { RuntimeClient } from "./runtimeClient";

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
