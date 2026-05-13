import type { ScheduledTaskRecord, ScheduledTaskRunRecord, TaskRecord } from "@shared";
import type { ExecutionLog, ScheduledTask } from "../ui/workbench/workspaces/scheduled/ScheduledWorkspace";
import { formatTimestamp } from "./providerConfig";

export function taskStatusToScheduledStatus(status: TaskRecord["status"]): ScheduledTask["status"] {
  if (status === "completed") {
    return "completed";
  }
  if (status === "failed" || status === "cancelled") {
    return "failed";
  }
  return "active";
}

export function scheduledRecordToWorkspaceTask(record: ScheduledTaskRecord): ScheduledTask {
  return {
    id: record.id,
    title: record.name,
    description: record.prompt,
    status: record.enabled ? record.status : "disabled",
    scheduleText: record.schedule || "未设置计划",
    lastRunText: record.lastRunAt ? `上次运行：${formatTimestamp(record.lastRunAt)}` : "尚未运行",
  };
}

export function scheduledRunToExecutionLog(run: ScheduledTaskRunRecord): ExecutionLog {
  return {
    id: run.id,
    taskId: run.taskId,
    time: formatTimestamp(run.startedAt),
    result: run.status === "completed" ? "completed" : "failed",
    message: run.summary ?? run.error ?? `运行状态：${run.status}`,
  };
}
