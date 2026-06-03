import { describe, expect, it } from "vitest";
import type { AgentEventEnvelope, TaskRecord } from "@shared";
import { applyEventToTask, taskRecordFromEvent } from "./eventRecordViews";

function taskEvent(type: AgentEventEnvelope["type"], payload: AgentEventEnvelope["payload"]): AgentEventEnvelope {
  return {
    eventId: `evt_${type}`,
    sessionId: "sess_1",
    taskId: "task_1",
    type,
    ts: 1778734168000,
    payload,
  };
}

describe("eventRecordViews worktree routing", () => {
  it("creates a task record with activeWorktree from task.worktree.bound", () => {
    const task = taskRecordFromEvent(taskEvent("task.worktree.bound", {
      worktreeId: "wt_1",
      worktreePath: "D:/repo.worktrees/task_1",
      branchName: "agent/task_1",
      baseRef: "HEAD",
      status: "active",
    }));

    expect(task?.routing?.activeWorktree?.id).toBe("wt_1");
    expect(task?.routing?.activeWorktree?.worktreePath).toBe("D:/repo.worktrees/task_1");
  });

  it("merges payload routing activeWorktree into an existing task", () => {
    const current: TaskRecord = {
      id: "task_1",
      sessionId: "sess_1",
      type: "chat",
      status: "running",
      goal: "Edit safely",
      createdAt: 1,
      updatedAt: 1,
    };

    const next = applyEventToTask(current, taskEvent("task.updated", {
      status: "running",
      routing: {
        activeWorktree: {
          id: "wt_2",
          taskId: "task_1",
          baseRef: "main",
          branchName: "agent/task_1",
          worktreePath: "D:/repo.worktrees/task_1",
          status: "active",
        },
      },
    }));

    expect(next?.routing?.activeWorktree?.id).toBe("wt_2");
    expect(next?.routing?.activeWorktree?.baseRef).toBe("main");
  });

  it("keeps activeWorktree visible after a merge event", () => {
    const current: TaskRecord = {
      id: "task_1",
      sessionId: "sess_1",
      type: "chat",
      status: "running",
      goal: "Merge safely",
      createdAt: 1,
      updatedAt: 1,
    };

    const next = applyEventToTask(current, taskEvent("task.worktree.merged", {
      status: "running",
      activeWorktree: {
        id: "wt_3",
        taskId: "task_1",
        baseRef: "main",
        branchName: "agent/task_1",
        worktreePath: "D:/repo.worktrees/task_1",
        status: "merged",
      },
      targetBranch: "main",
    }));

    expect(next?.routing?.activeWorktree?.id).toBe("wt_3");
    expect(next?.routing?.activeWorktree?.status).toBe("merged");
  });

  it("keeps runtime-work waits in running state instead of approval state", () => {
    const current: TaskRecord = {
      id: "task_1",
      sessionId: "sess_1",
      type: "chat",
      status: "running",
      goal: "Fan out work",
      createdAt: 1,
      updatedAt: 1,
    };

    const next = applyEventToTask(current, taskEvent("task.runtime_work_waiting", {
      status: "running",
      detail: "Completion is waiting for runtime work to settle.",
      completionGate: {
        status: "waiting_runtime_work",
      },
    }));

    expect(next?.status).toBe("running");
    expect(next?.resultSummary).toBe("Completion is waiting for runtime work to settle.");
  });
});
