import type { SessionWorkspaceActiveTask } from "./types";

export function aggregateRuntimeStatus(statuses: Array<string | undefined>, emptyStatus: string) {
  const normalized = statuses.filter((status): status is string => Boolean(status));
  if (normalized.length === 0) {
    return emptyStatus;
  }
  if (normalized.some((status) => ["failed", "error", "cancelled"].includes(status))) {
    return "failed";
  }
  if (normalized.some((status) => ["running", "pending", "queued"].includes(status))) {
    return "running";
  }
  if (normalized.every((status) => status === "skipped")) {
    return "skipped";
  }
  if (normalized.every((status) => ["passed", "completed", "applied", "succeeded"].includes(status))) {
    return normalized.every((status) => status === "passed") ? "passed" : "completed";
  }

  return "recorded";
}

export type TaskPhase = "idle" | "analyzing" | "modifying" | "verifying" | "waiting" | "completed" | "failed";

export const TASK_PHASES: Array<{ id: TaskPhase; label: string }> = [
  { id: "analyzing", label: "正在分析代码" },
  { id: "modifying", label: "正在修改" },
  { id: "verifying", label: "正在验证" },
  { id: "completed", label: "已完成" },
];

export function getTaskPhase(activeTask?: SessionWorkspaceActiveTask | null): TaskPhase {
  const status = activeTask?.status?.toLowerCase();
  if (!activeTask) return "idle";
  if (status && ["failed", "error", "cancelled", "rejected"].includes(status)) return "failed";
  if (status === "completed" || status === "succeeded") return "completed";
  if (status === "waiting_approval" || status === "paused") return "waiting";
  if (status === "verifying" || (activeTask.verification?.length && status !== "completed")) return "verifying";
  if (activeTask.changedFiles?.length || activeTask.commands?.some((command) => command.status === "running")) return "modifying";
  return "analyzing";
}

export function getTaskPhaseLabel(phase: TaskPhase) {
  if (phase === "idle") return "等待需求";
  if (phase === "waiting") return "等待审批";
  if (phase === "failed") return "执行失败";
  return TASK_PHASES.find((item) => item.id === phase)?.label ?? "正在处理";
}

export function getTaskPhaseTone(phase: TaskPhase): "neutral" | "primary" | "success" | "warning" | "danger" | "info" {
  if (phase === "completed") return "success";
  if (phase === "failed") return "danger";
  if (phase === "waiting") return "warning";
  if (phase === "idle") return "neutral";
  return "info";
}

export function getTaskPhaseIndex(phase: TaskPhase) {
  if (phase === "completed") return TASK_PHASES.length - 1;
  const index = TASK_PHASES.findIndex((item) => item.id === phase);
  return index >= 0 ? index : 0;
}

export function buildTaskProgressSummary(activeTask?: SessionWorkspaceActiveTask | null) {
  if (!activeTask) return "说出一个需求后，我会先分析代码，再修改和验证。";
  const phase = getTaskPhase(activeTask);
  if (phase === "completed") {
    return activeTask.resultSummary || activeTask.summary || "任务已完成，下面可以查看变更和验证结果。";
  }
  if (phase === "failed") {
    return activeTask.resultSummary || activeTask.summary || "任务执行失败，下面会保留可用的诊断信息。";
  }
  if (phase === "waiting") {
    return "任务需要审批后才能继续。";
  }
  return activeTask.goal || "任务正在执行。";
}

export function normalizeSubtaskStatus(status?: string) {
  const normalized = status?.toLowerCase();
  if (!normalized) return "pending";
  if (["active", "running", "started", "planning", "verifying"].includes(normalized)) return "active";
  if (["completed", "succeeded", "passed", "applied"].includes(normalized)) return "completed";
  if (["failed", "error", "cancelled", "rejected"].includes(normalized)) return "failed";
  return normalized;
}

export const ACTION_PLAN_STEP_RE =
  /\b(apply|patch|edit|write|implement|modify|command|shell|run|verify|git|commit|diff|build|fix)\b|\u5e94\u7528|\u8865\u4e01|\u7f16\u8f91|\u5199\u5165|\u5b9e\u73b0|\u4fee\u6539|\u8fd0\u884c|\u6267\u884c|\u9a8c\u8bc1|\u6784\u5efa|\u4fee\u590d|\u63d0\u4ea4/i;
export const QUESTION_GOAL_RE = new RegExp(
  "[?\\uFF1F]\\s*$|\\u5417|\\u662f\\u5426|\\u662f\\u4e0d\\u662f|\\u80fd\\u4e0d\\u80fd|\\u53ef\\u4ee5|\\u5b8c\\u6210\\u4e86\\u5417|\\u7ed3\\u675f\\u4e86\\u5417|\\u4ec0\\u4e48\\u60c5\\u51b5|\\u4e3a\\u4ec0\\u4e48|\\u600e\\u4e48",
);
export const GENERIC_ANSWER_STEP_RE =
  /\b(inspect|search|summarize|understand|locate)\b|\u7406\u89e3|\u5b9a\u4f4d|\u67e5\u627e|\u6574\u7406|\u7b54\u590d|\u603b\u7ed3/i;
export const AGENT_WORK_REQUEST_RE = /\b(agent|worker|subagent|child task|childtask)\b|子任务|协作|多个\s*agent|多\s*agent|起\s*agent/i;

export function hasTaskWorkEvidence(activeTask?: SessionWorkspaceActiveTask | null) {
  return Boolean(
    activeTask?.changedFiles?.length ||
      activeTask?.commands?.length ||
      activeTask?.verification?.length,
  );
}

export function hasActionPlanStep(activeTask?: SessionWorkspaceActiveTask | null) {
  return Boolean(
    activeTask?.planSteps?.some((step) =>
      ACTION_PLAN_STEP_RE.test(`${step.id ?? ""} ${step.title ?? ""}`),
    ),
  );
}

export function isQuestionLikeGoal(goal?: string | null) {
  return QUESTION_GOAL_RE.test(goal?.trim() ?? "");
}

export function isGenericAnswerPlan(activeTask?: SessionWorkspaceActiveTask | null) {
  const planSteps = activeTask?.planSteps ?? [];
  return Boolean(
    planSteps.length > 0 &&
      planSteps.length <= 3 &&
      planSteps.every((step) => GENERIC_ANSWER_STEP_RE.test(`${step.id ?? ""} ${step.title ?? ""}`)),
  );
}

export function shouldDisplayTaskScaffold(activeTask?: SessionWorkspaceActiveTask | null) {
  if (!activeTask) return false;
  if (hasTaskWorkEvidence(activeTask)) return true;

  const planCount = activeTask.planSteps?.length ?? 0;
  if (isQuestionLikeGoal(activeTask.goal) && isGenericAnswerPlan(activeTask)) return false;
  if (hasActionPlanStep(activeTask)) return true;
  if (planCount > 3) return true;

  // Generic discovery plans are useful internally, but they make ordinary follow-up
  // questions look like failed work. Keep the task UI for real execution plans only.
  if (planCount > 0 && isQuestionLikeGoal(activeTask.goal)) return false;

  return !isQuestionLikeGoal(activeTask.goal);
}

export function expectsAgentWork(activeTask?: SessionWorkspaceActiveTask | null) {
  if (!activeTask) return false;
  return AGENT_WORK_REQUEST_RE.test(`${activeTask.goal ?? ""} ${activeTask.currentStep ?? ""}`);
}
