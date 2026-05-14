import { memo, useState } from "react";
import type { SessionWorkspaceActiveTask } from "./types";
import { useTickWhen } from "./useTick";
import { formatElapsedTime, getLiveTaskLabel, isTaskControllable } from "./utils";

export const TaskLivePill = memo(function TaskLivePill({ activeTask }: { activeTask?: SessionWorkspaceActiveTask | null }) {
  const shouldShow = isTaskControllable(activeTask?.status);
  const [fallbackStartedAt] = useState(() => Date.now());
  const startedAt = activeTask?.createdAt ?? activeTask?.updatedAt ?? fallbackStartedAt;
  const now = useTickWhen(shouldShow);

  if (!shouldShow || !activeTask) {
    return null;
  }

  const elapsed = formatElapsedTime(now - startedAt);
  const label = getLiveTaskLabel(activeTask);

  return (
    <div
      className="session-task-live-pill"
      aria-label={`当前任务${label}${elapsed}`}
      title={activeTask.currentStep || activeTask.goal || label}
    >
      <span className="session-task-live-spark" aria-hidden="true" />
      <strong>{label}</strong>
      <time>{elapsed}</time>
    </div>
  );
});
