import { useState } from "react";
import type {
  CommandLogRecord,
  PatchRecord,
  TaskRecord,
  TraceEventRecord,
} from "@shared";
import { RuntimeClient } from "../lib/runtimeClient";
import { TRACE_LIMIT, isTaskControllable } from "../state/providerConfig";
import type { TaskControlAction } from "../state/providerConfig";
import { upsertRecord } from "../state/eventRecordViews";
import type { HookDeps } from "./types";

const runtimeClient = new RuntimeClient();

export interface UseTaskTraceDeps extends HookDeps {
  task: TaskRecord | null;
  setTask: (task: TaskRecord | null) => void;
  taskHistory: TaskRecord[];
  setTaskHistory: React.Dispatch<React.SetStateAction<TaskRecord[]>>;
  setActiveTaskForSession: (taskId: string | null, sessionId?: string | null) => void;
  activeTaskId: string | null;
  setActiveTaskId: (id: string | null) => void;
}

export function useTaskTrace(deps: UseTaskTraceDeps) {
  const {
    toastError, setError,
    task, setTask, taskHistory, setTaskHistory,
    setActiveTaskForSession,
    activeTaskId, setActiveTaskId,
  } = deps;

  const [events, setEvents] = useState<any[]>([]);
  const [traceEvents, setTraceEvents] = useState<TraceEventRecord[]>([]);
  const [commandLogCacheById, setCommandLogCacheById] = useState<Record<string, CommandLogRecord>>({});
  const [patchCacheById, setPatchCacheById] = useState<Record<string, PatchRecord>>({});
  const [traceBusy, setTraceBusy] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [approvalBusyId, setApprovalBusyId] = useState<string | null>(null);
  const [patchBusyId, setPatchBusyId] = useState<string | null>(null);
  const [commandJobBusyId, setCommandJobBusyId] = useState<string | null>(null);
  const [taskControlBusyAction, setTaskControlBusyAction] = useState<TaskControlAction | null>(null);
  const [taskControlError, setTaskControlError] = useState<string | null>(null);
  const [refreshBusy, setRefreshBusy] = useState(false);

  async function loadTraceForTask(taskId: string, isCancelled: () => boolean = () => false) {
    setTraceBusy(true);
    setTraceError(null);

    try {
      const [result, commandResult] = await Promise.all([
        runtimeClient.listTrace({
          taskId,
          limit: TRACE_LIMIT,
        }),
        runtimeClient
          .commandLogList({
            taskId,
            limit: TRACE_LIMIT,
          })
          .catch(() => ({ commandLogs: [] as CommandLogRecord[] })),
      ]);
      if (!isCancelled()) {
        setTraceEvents(result.traceEvents);
        setCommandLogCacheById((current) => ({
          ...current,
          ...Object.fromEntries(commandResult.commandLogs.map((log) => [log.id, log])),
        }));
      }
    } catch (reason) {
      if (!isCancelled()) {
        setTraceEvents([]);
        setTraceError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (!isCancelled()) {
        setTraceBusy(false);
      }
    }
  }

  async function handleRefreshTask() {
    if (!task) {
      return;
    }

    const taskId = task.id;
    setRefreshBusy(true);
    setError(null);

    try {
      const result = await runtimeClient.getTask(taskId);
      setTask(result.task);
      setActiveTaskForSession(result.task.id);
      setTaskHistory((current) => upsertRecord(current, result.task));
      await loadTraceForTask(taskId);
    } catch (reason) {
      toastError(reason);
    } finally {
      setRefreshBusy(false);
    }
  }

  async function refreshTaskControlState(taskId: string) {
    const [taskResult, taskListResult] = await Promise.all([
      runtimeClient.getTask(taskId),
      runtimeClient.listTasks(),
    ]);
    setTask(taskResult.task);
    setTaskHistory(taskListResult.tasks);
    setActiveTaskForSession(taskId);
    await loadTraceForTask(taskId);
  }

  async function handleTaskControl(action: TaskControlAction, taskIdOverride?: string) {
    const targetTask = taskIdOverride && task?.id !== taskIdOverride
      ? taskHistory.find((item) => item.id === taskIdOverride) ?? null
      : task;
    if (!targetTask) {
      return;
    }

    if (taskControlBusyAction !== null) {
      return;
    }

    if (action === "cancel" && !isTaskControllable(targetTask.status)) {
      return;
    }

    const taskId = targetTask.id;
    setTaskControlBusyAction(action);
    setTaskControlError(null);
    setError(null);

    try {
      if (action === "cancel") {
        await runtimeClient.cancelTask({ taskId });
      } else if (action === "pause") {
        await runtimeClient.pauseTask({ taskId });
      } else {
        await runtimeClient.resumeTask({ taskId });
      }

      await refreshTaskControlState(taskId);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setTaskControlError(message);
      setError(message);
    } finally {
      setTaskControlBusyAction(null);
    }
  }

  async function handleRefreshTrace() {
    if (!activeTaskId) {
      setTraceEvents([]);
      setCommandLogCacheById({});
      setTraceError(null);
      return;
    }

    await loadTraceForTask(activeTaskId);
  }

  async function handleRefreshCommandJob(commandId: string) {
    setCommandJobBusyId(commandId);
    setError(null);

    try {
      const result = await runtimeClient.commandLogGet({ commandId });
      setCommandLogCacheById((current) => ({
        ...current,
        [result.commandLog.id]: result.commandLog,
      }));
      await loadTraceForTask(result.commandLog.taskId);
    } catch (reason) {
      toastError(reason);
    } finally {
      setCommandJobBusyId((current) => (current === commandId ? null : current));
    }
  }

  async function handleStopCommandJob(commandId: string) {
    setCommandJobBusyId(commandId);
    setError(null);

    try {
      const result = await runtimeClient.commandCancel({ commandId });
      setCommandLogCacheById((current) => ({
        ...current,
        [result.commandLog.id]: result.commandLog,
      }));
      await loadTraceForTask(result.commandLog.taskId);
    } catch (reason) {
      toastError(reason);
    } finally {
      setCommandJobBusyId((current) => (current === commandId ? null : current));
    }
  }

  async function handleLoadPatchDiff(patchId: string) {
    setPatchBusyId(patchId);
    setError(null);

    try {
      const result = await runtimeClient.diffGet({ patchId });
      setPatchCacheById((current) => ({
        ...current,
        [patchId]: {
          ...result.patch,
          diffText: result.diffText || result.patch.diffText,
        },
      }));
    } catch (reason) {
      toastError(reason);
    } finally {
      setPatchBusyId((current) => (current === patchId ? null : current));
    }
  }

  async function handleApprovalSubmit(approvalId: string, decision: "approved" | "rejected") {
    setApprovalBusyId(approvalId);
    setError(null);

    try {
      const result = await runtimeClient.approvalSubmit({
        approvalId,
        decision,
      });
      if (result.worktreeMerge) {
        const merge = result.worktreeMerge;
        const message = merge.merged
          ? "Worktree merged."
          : merge.error
            ? `Worktree merge failed: ${merge.error}`
            : "Worktree merge did not complete.";
        deps.addToast(merge.merged ? "success" : "error", message);
        const refreshed = await runtimeClient.getTask(result.approval.taskId);
        setTask(refreshed.task);
        setTaskHistory((current) => upsertRecord(current, refreshed.task));
        await loadTraceForTask(result.approval.taskId);
        return;
      }
      deps.addToast("success", decision === "approved" ? "Approved." : "Rejected.");
    } catch (reason) {
      toastError(reason);
    } finally {
      setApprovalBusyId((current) => (current === approvalId ? null : current));
    }
  }

  return {
    events, setEvents,
    traceEvents, setTraceEvents,
    commandLogCacheById, setCommandLogCacheById,
    patchCacheById, setPatchCacheById,
    traceBusy, setTraceBusy,
    traceError, setTraceError,
    approvalBusyId, setApprovalBusyId,
    patchBusyId, setPatchBusyId,
    commandJobBusyId, setCommandJobBusyId,
    taskControlBusyAction, setTaskControlBusyAction,
    taskControlError, setTaskControlError,
    refreshBusy, setRefreshBusy,
    loadTraceForTask,
    handleRefreshTask,
    handleTaskControl,
    handleRefreshTrace,
    handleRefreshCommandJob,
    handleStopCommandJob,
    handleLoadPatchDiff,
    handleApprovalSubmit,
  };
}
