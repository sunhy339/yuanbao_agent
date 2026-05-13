import { useEffect, useState } from "react";
import type { ScheduledTaskRecord, ScheduledTaskRunRecord } from "@shared";
import type { ScheduledTaskDraft } from "../ui/workbench/workspaces/scheduled/ScheduledWorkspace";
import { RuntimeClient } from "../lib/runtimeClient";
import type { HookDeps } from "./types";

const runtimeClient = new RuntimeClient();

export function useScheduledTasks(deps: HookDeps) {
  const { addToast, toastError, setError } = deps;

  const [scheduledRecords, setScheduledRecords] = useState<ScheduledTaskRecord[]>([]);
  const [scheduledLogs, setScheduledLogs] = useState<ScheduledTaskRunRecord[]>([]);
  const [selectedScheduledTaskId, setSelectedScheduledTaskId] = useState<string | null>(null);
  const [scheduledBusyTaskId, setScheduledBusyTaskId] = useState<string | null>(null);
  const [scheduledCreateBusy, setScheduledCreateBusy] = useState(false);

  async function refreshScheduledRecords(preferredTaskId?: string) {
    const result = await runtimeClient.listScheduledTasks();
    setScheduledRecords(result.tasks);
    const nextSelectedTaskId =
      preferredTaskId && result.tasks.some((item) => item.id === preferredTaskId)
        ? preferredTaskId
        : selectedScheduledTaskId && result.tasks.some((item) => item.id === selectedScheduledTaskId)
          ? selectedScheduledTaskId
          : result.tasks[0]?.id ?? null;
    setSelectedScheduledTaskId(nextSelectedTaskId);
    return result.tasks;
  }

  async function handleRunScheduledTask(taskId: string) {
    setScheduledBusyTaskId(taskId);
    setError(null);

    try {
      const result = await runtimeClient.runScheduledTaskNow({ taskId });
      await refreshScheduledRecords(taskId);
      const logs = await runtimeClient.listScheduledTaskLogs({ taskId, limit: 50 });
      setScheduledLogs(logs.logs);
      setSelectedScheduledTaskId(taskId);
      if (result.run.summary) {
        setError(result.run.summary);
      }
    } catch (reason) {
      toastError(reason);
    } finally {
      setScheduledBusyTaskId(null);
    }
  }

  async function handleToggleScheduledTask(taskId: string) {
    const current = scheduledRecords.find((item) => item.id === taskId);
    if (!current) {
      return;
    }

    setScheduledBusyTaskId(taskId);
    setError(null);

    try {
      await runtimeClient.toggleScheduledTask({
        taskId,
        enabled: !current.enabled,
      });
      await refreshScheduledRecords(taskId);
      setSelectedScheduledTaskId(taskId);
    } catch (reason) {
      toastError(reason);
    } finally {
      setScheduledBusyTaskId(null);
    }
  }

  function handleSelectScheduledTask(taskId: string) {
    setSelectedScheduledTaskId(taskId);
  }

  async function handleCreateScheduledTask(draft?: ScheduledTaskDraft) {
    if (!draft) {
      return;
    }

    setScheduledCreateBusy(true);
    setError(null);

    try {
      const prompt = draft.description ? `${draft.description}\n\n${draft.prompt}` : draft.prompt;
      const result = await runtimeClient.createScheduledTask({
        name: draft.name,
        prompt,
        schedule: draft.schedule,
        enabled: draft.enabled,
      });
      await refreshScheduledRecords(result.task.id);
      setSelectedScheduledTaskId(result.task.id);
      addToast("success", "定时任务已创建");
    } catch (reason) {
      toastError(reason);
    } finally {
      setScheduledCreateBusy(false);
    }
  }

  // Load logs when selected task changes
  useEffect(() => {
    let disposed = false;

    if (!selectedScheduledTaskId) {
      setScheduledLogs([]);
      return () => {
        disposed = true;
      };
    }

    runtimeClient
      .listScheduledTaskLogs({ taskId: selectedScheduledTaskId, limit: 50 })
      .then((result) => {
        if (!disposed) {
          setScheduledLogs(result.logs);
        }
      })
      .catch((reason) => {
        if (!disposed) {
          toastError(reason);
        }
      });

    return () => {
      disposed = true;
    };
  }, [selectedScheduledTaskId]);

  return {
    scheduledRecords,
    setScheduledRecords,
    scheduledLogs,
    setScheduledLogs,
    selectedScheduledTaskId,
    setSelectedScheduledTaskId,
    scheduledBusyTaskId,
    setScheduledBusyTaskId,
    scheduledCreateBusy,
    setScheduledCreateBusy,
    refreshScheduledRecords,
    handleRunScheduledTask,
    handleToggleScheduledTask,
    handleSelectScheduledTask,
    handleCreateScheduledTask,
  };
}
