import { Button, StatusBadge } from "../../../v2/components/ui";
import { formatStatusLabel } from "../../../copy";
import type {
  SessionWorkspaceActiveTask,
  SessionWorkspaceApproval,
  SessionWorkspaceContextPreview,
  SessionWorkspacePatch,
  SessionWorkspaceTrace,
} from "./types";
import { getStatusTone } from "./utils";
import {
  buildTaskProgressSummary,
  getTaskPhase,
  getTaskPhaseLabel,
  getTaskPhaseTone,
} from "./taskPhase";

interface RuntimeCockpitPanelProps {
  activeTask?: SessionWorkspaceActiveTask | null;
  approvals?: SessionWorkspaceApproval[];
  patches?: SessionWorkspacePatch[];
  traces?: SessionWorkspaceTrace[];
  contextPreview?: SessionWorkspaceContextPreview;
  taskBusyAction?: "refresh" | "stop" | "pause" | "resume" | null;
  onRefreshTask?(): void | Promise<void>;
  onPauseTask?(taskId: string): void | Promise<void>;
  onResumeTask?(taskId: string): void | Promise<void>;
}

interface CockpitMetric {
  label: string;
  value: string;
  tone?: "neutral" | "success" | "warning" | "danger" | "primary" | "info";
}

interface CockpitSignal {
  label: string;
  value: string;
  tone?: CockpitMetric["tone"];
}

interface RuntimeHighlights {
  latestCheck?: string;
  blocker?: string;
  pendingApproval?: string;
}

function formatApprovalLabel(kind?: string) {
  if (!kind) {
    return "审批";
  }
  switch (kind.toLowerCase()) {
    case "shell":
    case "run_command":
      return "命令审批";
    case "write_file":
    case "apply_patch":
      return "写入审批";
    case "merge":
      return "合并审批";
    default:
      return kind.replace(/_/g, " ");
  }
}

function normalizeCommandLabel(command?: string) {
  if (!command) {
    return undefined;
  }
  return command
    .trim()
    .replace(/\s+/g, " ")
    .replace(/\\/g, "/")
    .replace(/^[a-z]:\/[^ ]*python(?:\.exe)?\s+-m\s+/i, "python -m ")
    .replace(/^[a-z]:\/[^ ]*node(?:\.exe)?\s+/i, "node ")
    .replace(/^[a-z]:\/[^ ]*git(?:\.exe)?\s+/i, "git ");
}

function isVerificationLikeCommand(command?: string) {
  const normalized = normalizeCommandLabel(command)?.toLowerCase();
  if (!normalized) {
    return false;
  }
  return (
    normalized.startsWith("python -m pytest") ||
    normalized.startsWith("python -m py_compile") ||
    normalized.startsWith("node --check")
  );
}

function getVerificationLikeCommands(activeTask?: SessionWorkspaceActiveTask | null) {
  return (activeTask?.commands ?? []).filter((command) => isVerificationLikeCommand(command.command));
}

function summarizeVerification(activeTask?: SessionWorkspaceActiveTask | null) {
  const verification = activeTask?.verification ?? [];
  if (!verification.length) {
    const latestVerificationCommand = [...getVerificationLikeCommands(activeTask)]
      .reverse()
      .find((command) => ["passed", "completed", "succeeded"].includes(String(command.status ?? "").toLowerCase()));
    return latestVerificationCommand?.command ? normalizeCommandLabel(latestVerificationCommand.command) : undefined;
  }
  const latestPassed = [...verification]
    .reverse()
    .find((item) => ["passed", "completed", "succeeded"].includes(String(item.status ?? "").toLowerCase()));
  if (latestPassed?.command) {
    return normalizeCommandLabel(latestPassed.command);
  }
  return `${verification.length} 项检查`;
}

function buildChangedFilesSummary(activeTask?: SessionWorkspaceActiveTask | null) {
  const files = activeTask?.changedFiles ?? [];
  if (!files.length) {
    return undefined;
  }
  const visibleFiles = files.slice(0, 3).map((file) => file.path);
  return visibleFiles.length < files.length
    ? `${visibleFiles.join("、")} 等 ${files.length} 个文件`
    : visibleFiles.join("、");
}

function formatBudgetRatio(contextPreview?: SessionWorkspaceContextPreview) {
  const stats = contextPreview?.budgetStats;
  const used = stats?.estimatedInputTokens ?? stats?.estimatedTokens ?? stats?.messageTokens;
  const max = stats?.maxContextTokens;
  if (typeof used !== "number" || typeof max !== "number" || max <= 0) {
    return undefined;
  }
  const ratio = Math.max(0, Math.min(1, used / max));
  return {
    percent: Math.round(ratio * 100),
    used,
    max,
    tone: ratio >= 0.88 ? "danger" : ratio >= 0.72 ? "warning" : "success",
  } as const;
}

function latestCompletionEvidence(approvals?: SessionWorkspaceApproval[]) {
  return approvals
    ?.filter((approval) => approval.completionEvidence)
    .sort((left, right) => (right.requestedAt ?? 0) - (left.requestedAt ?? 0))[0]?.completionEvidence;
}

function latestBlockingApproval(approvals?: SessionWorkspaceApproval[]) {
  return approvals
    ?.filter((approval) => approval.status === "pending")
    .sort((left, right) => (right.requestedAt ?? 0) - (left.requestedAt ?? 0))[0];
}

function compactSignals(signals: Array<CockpitSignal | null | undefined>, limit = 4): CockpitSignal[] {
  return signals.filter((signal): signal is CockpitSignal => Boolean(signal?.value)).slice(0, limit);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function readWorkflowString(record: Record<string, unknown> | null, key: string) {
  const value = record?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function readWorkflowBoolean(record: Record<string, unknown> | null, key: string) {
  return record?.[key] === true;
}

function latestTraceSignals(
  traces: SessionWorkspaceTrace[] | undefined,
  matcher: (trace: SessionWorkspaceTrace) => boolean,
  limit = 3,
): CockpitSignal[] {
  return [...(traces ?? [])]
    .filter(matcher)
    .sort((left, right) => (right.time ?? 0) - (left.time ?? 0))
    .slice(0, limit)
    .map((trace) => ({
      label: trace.type,
      value: trace.summary || trace.detail || trace.status || trace.source || "recorded",
      tone: ["failed", "error", "cancelled"].includes(String(trace.status ?? "").toLowerCase())
        ? "danger"
        : trace.status === "completed"
          ? "success"
          : "neutral",
    }));
}

function isProviderTrace(trace: SessionWorkspaceTrace) {
  const type = trace.type.toLowerCase();
  const haystack = `${trace.source ?? ""} ${trace.title ?? ""} ${trace.summary ?? ""}`.toLowerCase();
  return (
    type.includes("provider.failure") ||
    type.includes("provider.preflight") ||
    type.includes("provider.switch") ||
    type.includes("failure_recovery") ||
    (type.includes("provider") && (haystack.includes("recovery") || haystack.includes("preflight") || haystack.includes("failed")))
  );
}

function buildHighlights(
  activeTask?: SessionWorkspaceActiveTask | null,
  approvals?: SessionWorkspaceApproval[],
) {
  const latestPassedVerification = latestWorkspaceVerificationSignal(activeTask);
  const latestFailure = latestWorkspaceFailureSignal(activeTask);
  const blockingApproval = latestBlockingApproval(approvals);
  const hasSuccessfulVerification = Boolean(latestPassedVerification?.value);
  const hasPendingApproval = Boolean(blockingApproval?.summary || blockingApproval?.title);
  return {
    latestCheck: latestPassedVerification?.value,
    blocker: hasPendingApproval ? blockingApproval?.summary || blockingApproval?.title : hasSuccessfulVerification ? undefined : latestFailure?.value,
    pendingApproval: blockingApproval?.summary || blockingApproval?.title,
  } satisfies RuntimeHighlights;
}

function isMcpSkillTrace(trace: SessionWorkspaceTrace) {
  const haystack = `${trace.type} ${trace.source ?? ""} ${trace.title ?? ""} ${trace.summary ?? ""}`.toLowerCase();
  return haystack.includes("mcp") || haystack.includes("skill") || haystack.includes("tool_recovery");
}

function isUsefulProviderSignal(signal: CockpitSignal) {
  const label = signal.label.toLowerCase();
  const value = signal.value.toLowerCase();
  if (/provider\.failure\.recovery_decision|provider\.preflight|agent\.decision/.test(label)) {
    return false;
  }
  if (value.startsWith("{") || value.startsWith("[")) {
    return false;
  }
  return signal.tone === "danger" || signal.tone === "warning";
}

function isUsefulMcpSignal(signal: CockpitSignal) {
  const label = signal.label.toLowerCase();
  const value = signal.value.toLowerCase();
  if (/tool_recovery|agent\.decision/.test(label)) {
    return false;
  }
  if (value.startsWith("{") || value.startsWith("[")) {
    return false;
  }
  return signal.tone === "danger" || signal.tone === "warning";
}

function approvalAuditSignals(completionEvidence: ReturnType<typeof latestCompletionEvidence>): CockpitSignal[] {
  const audit = completionEvidence?.audit;
  const counts = audit?.approvalCounts;
  const signals = compactSignals([
    counts
      ? {
          label: "审批记录",
          value: `${counts.approved ?? 0} 已批准 / ${counts.pending ?? 0} 待处理 / ${counts.rejected ?? 0} 已拒绝`,
          tone: (counts.rejected ?? 0) > 0 ? "danger" : (counts.pending ?? 0) > 0 ? "warning" : "success",
        }
      : null,
    audit?.completionAdvisor
      ? {
          label: "完成建议",
          value: compactSignals([
            { label: "source", value: audit.completionAdvisor.source ?? "" },
            {
              label: "confidence",
              value:
                typeof audit.completionAdvisor.confidence === "number"
                  ? `${Math.round(audit.completionAdvisor.confidence * 100)}%`
                  : "",
            },
            { label: "proposal", value: audit.completionAdvisor.proposalRecordId ?? "" },
          ], 3)
            .map((signal) => signal.value)
            .join(" | "),
          tone: audit.completionAdvisor.accepted === false ? "warning" : "info",
        }
      : null,
  ]);
  const approvalRows =
    audit?.approvals?.slice(-3).map((approval): CockpitSignal => ({
      label: approval.kind || "approval",
      value: [approval.decision, approval.gateStatus, approval.summary].filter(Boolean).join(" | ") || "recorded",
      tone: approval.decision === "rejected" ? "danger" : approval.decision === "approved" ? "success" : "warning",
    })) ?? [];
  return [...signals, ...approvalRows].slice(0, 5);
}

function contextSignals(
  contextPreview: SessionWorkspaceContextPreview | undefined,
  budget: ReturnType<typeof formatBudgetRatio>,
): CockpitSignal[] {
  const stats = contextPreview?.budgetStats;
  const taskFocus = contextPreview?.taskFocus;
  return compactSignals([
    budget
      ? {
          label: "预算",
          value: `${budget.percent}% (${budget.used}/${budget.max})`,
          tone: budget.tone,
        }
      : null,
    stats?.trimmedSections?.length
      ? {
          label: "已裁剪",
          value: stats.trimmedSections.join(", "),
          tone: "warning",
        }
      : null,
    stats?.droppedSections?.length
      ? {
          label: "已丢弃",
          value: stats.droppedSections.join(", "),
          tone: "danger",
        }
      : null,
    taskFocus?.currentStep
      ? {
          label: "当前步骤",
          value: taskFocus.currentStep,
          tone: "info",
        }
      : null,
  ]);
}

function workflowSignals(activeTask?: SessionWorkspaceActiveTask | null): CockpitSignal[] {
  const workflow = asRecord(activeTask?.mainWorkflow);
  const convergence = asRecord(workflow?.convergence);
  const takeover = asRecord(workflow?.userTakeover);
  const automation = asRecord(workflow?.automation);
  return compactSignals([
    convergence
      ? {
          label: "收敛状态",
          value: [
            readWorkflowString(convergence, "state"),
            readWorkflowBoolean(convergence, "resumable") ? "resumable" : "",
            readWorkflowString(convergence, "reason"),
          ]
            .filter(Boolean)
            .join(" | ") || "recorded",
          tone: readWorkflowBoolean(convergence, "resumable") ? "info" : "neutral",
        }
      : null,
    readWorkflowString(convergence, "targetGoal")
      ? {
          label: "目标",
          value: readWorkflowString(convergence, "targetGoal") ?? "",
          tone: "primary",
        }
      : null,
    readWorkflowString(convergence, "handoffFocus")
      ? {
          label: "接力焦点",
          value: readWorkflowString(convergence, "handoffFocus") ?? "",
          tone: "info",
        }
      : null,
    takeover
      ? {
          label: "人工接管",
          value: [readWorkflowString(takeover, "state"), readWorkflowString(takeover, "intent")]
            .filter(Boolean)
            .join(" | ") || "recorded",
          tone: "info",
        }
      : null,
    readWorkflowString(automation, "level") && (convergence || takeover)
      ? {
          label: "自动化",
          value: readWorkflowString(automation, "level") ?? "",
          tone: "neutral",
        }
      : null,
  ], 8);
}

function workspaceSignals(activeTask?: SessionWorkspaceActiveTask | null): CockpitSignal[] {
  const commands = activeTask?.commands ?? [];
  const verification = activeTask?.verification ?? [];
  const verificationCommands = getVerificationLikeCommands(activeTask);
  const successfulCommands = [...commands]
    .filter((command) => command.status === "completed" || command.status === "passed")
    .slice(-2)
    .reverse();
  const blockingCommands = [...commands]
    .filter((command) => command.status === "failed" || command.status === "error")
    .slice(-1)
    .reverse();
  const latestVerification = [...verification].slice(-3).reverse();
  const passedVerification = verification.filter((item) => item.status === "passed" || item.status === "completed");
  const failedVerification = verification.filter((item) => item.status === "failed" || item.status === "error");
  const passedVerificationCommands = verificationCommands.filter((item) =>
    ["passed", "completed", "succeeded"].includes(String(item.status ?? "").toLowerCase()),
  );
  const latestFailure = [...commands]
    .reverse()
    .find((command) => command.status === "failed" || command.status === "error");

  return compactSignals([
    activeTask?.changedFiles?.length
      ? {
          label: "变更文件",
          value: buildChangedFilesSummary(activeTask) ?? activeTask.changedFiles.slice(0, 4).map((file) => file.path).join(", "),
          tone: "primary",
        }
      : null,
    (passedVerification.length || passedVerificationCommands.length)
      ? {
          label: "验证结果",
          value: summarizeVerification(activeTask) ?? `${passedVerification.length || passedVerificationCommands.length} 项通过`,
          tone: failedVerification.length ? "warning" : "success",
        }
      : null,
    ...successfulCommands.map((command): CockpitSignal => ({
      label: "最近命令",
      value: normalizeCommandLabel(command.command) ?? command.command,
      tone: "success",
    })),
    ...(!successfulCommands.length ? blockingCommands : []).map((command): CockpitSignal => ({
      label: "需要处理",
      value: [normalizeCommandLabel(command.command) ?? command.command, formatStatusLabel(command.status)].filter(Boolean).join(" | "),
      tone: "danger",
    })),
    ...(!successfulCommands.length && !blockingCommands.length && latestFailure
      ? [{
          label: "最近失败",
          value: normalizeCommandLabel(latestFailure.command) ?? latestFailure.command,
          tone: "danger" as const,
        }]
      : []),
    ...latestVerification.map((item): CockpitSignal => ({
      label: "检查",
      value: [normalizeCommandLabel(item.command) ?? item.command ?? item.id ?? "check", formatStatusLabel(item.status)].filter(Boolean).join(" | "),
      tone: item.status === "failed" || item.status === "error" ? "danger" : item.status === "passed" ? "success" : "neutral",
    })),
    ...(!latestVerification.length
      ? passedVerificationCommands
          .slice(-2)
          .reverse()
          .map((item): CockpitSignal => ({
            label: "检查",
            value: [normalizeCommandLabel(item.command) ?? item.command, formatStatusLabel(item.status ?? "completed")]
              .filter(Boolean)
              .join(" | "),
            tone: "success",
          }))
      : []),
  ], 6);
}

function latestWorkspaceVerificationSignal(activeTask?: SessionWorkspaceActiveTask | null): CockpitSignal | null {
  const verification = activeTask?.verification ?? [];
  const latestPassed = [...verification]
    .reverse()
    .find((item) => ["passed", "completed", "succeeded"].includes(String(item.status ?? "").toLowerCase()));
  if (!latestPassed) {
    const latestPassedCommand = [...getVerificationLikeCommands(activeTask)]
      .reverse()
      .find((item) => ["passed", "completed", "succeeded"].includes(String(item.status ?? "").toLowerCase()));
    if (!latestPassedCommand) {
      return null;
    }
    return {
      label: "最近验证",
      value: [normalizeCommandLabel(latestPassedCommand.command) ?? latestPassedCommand.command, formatStatusLabel(latestPassedCommand.status ?? "completed")]
        .filter(Boolean)
        .join(" · "),
      tone: "success",
    };
  }
  return {
    label: "最近验证",
    value:
      latestPassed.summary ||
      [normalizeCommandLabel(latestPassed.command) ?? latestPassed.command ?? latestPassed.id ?? "check", formatStatusLabel(latestPassed.status)]
        .filter(Boolean)
        .join(" · "),
    tone: "success",
  };
}

function latestWorkspaceFailureSignal(activeTask?: SessionWorkspaceActiveTask | null): CockpitSignal | null {
  const commands = activeTask?.commands ?? [];
  const latestFailure = [...commands]
    .reverse()
    .find((command) => ["failed", "error"].includes(String(command.status ?? "").toLowerCase()));
  if (!latestFailure) {
    return null;
  }
  return {
    label: "待处理",
    value: [normalizeCommandLabel(latestFailure.command) ?? latestFailure.command, formatStatusLabel(latestFailure.status)]
      .filter(Boolean)
      .join(" · "),
    tone: "danger",
  };
}

function buildNextCheckLabel({
  gateStatus,
  blockingApproval,
  activeTask,
}: {
  gateStatus?: string;
  blockingApproval?: ReturnType<typeof latestBlockingApproval>;
  activeTask?: SessionWorkspaceActiveTask | null;
}) {
  if (gateStatus) {
    return gateStatus.replace(/_/g, " ");
  }
  if (blockingApproval) {
    return blockingApproval.summary || blockingApproval.title;
  }
  if (activeTask?.verification?.length) {
    return "等待验证收口";
  }
  if (activeTask?.commands?.length || activeTask?.changedFiles?.length) {
    return "检查最新结果";
  }
  return "观察执行进展";
}

function buildAcceptanceSummary(
  completionEvidence: ReturnType<typeof latestCompletionEvidence>,
  activeTask?: SessionWorkspaceActiveTask | null,
) {
  if (completionEvidence?.issues?.[0]) {
    return completionEvidence.issues[0];
  }
  if (completionEvidence?.evidenceLevel) {
    return completionEvidence.evidenceLevel;
  }
  const passedChecks =
    activeTask?.verification?.filter((item) => item.status === "passed" || item.status === "completed").length ?? 0;
  if (passedChecks > 0) {
    return `${passedChecks} 项检查已经通过`;
  }
  return "当前没有阻塞证据";
}

function buildCompletedSummary(activeTask?: SessionWorkspaceActiveTask | null) {
  const changedFiles = activeTask?.changedFiles?.length ?? 0;
  const passedChecks =
    activeTask?.verification?.filter((item) => item.status === "passed" || item.status === "completed").length ?? 0;
  if (changedFiles > 0 && passedChecks > 0) {
    return `已完成修改，并通过 ${passedChecks} 项检查`;
  }
  if (changedFiles > 0) {
    return `已完成修改，共处理 ${changedFiles} 个文件`;
  }
  if (passedChecks > 0) {
    return `本轮未改代码，已通过 ${passedChecks} 项检查`;
  }
  return "本轮任务已经完成";
}

function buildCurrentStatusLabel(activeTask?: SessionWorkspaceActiveTask | null) {
  if (!activeTask) {
    return "等待新任务";
  }
  const status = activeTask?.status?.toLowerCase();
  if (status === "completed" || status === "succeeded") {
    return "本轮任务已完成";
  }
  if (status === "failed" || status === "error" || status === "cancelled") {
    return "有问题需要继续处理";
  }
  if (status === "waiting_approval" || status === "paused") {
    return "等待继续动作";
  }
  return "继续观察执行进展";
}

function buildPrimaryStepLabel(activeTask?: SessionWorkspaceActiveTask | null) {
  if (!activeTask) {
    return "等待新任务";
  }
  const status = activeTask?.status?.toLowerCase();
  if (status === "completed" || status === "succeeded") {
    return "本轮任务已完成";
  }
  if (status === "failed" || status === "error" || status === "cancelled") {
    return "需要继续处理失败项";
  }
  if (status === "waiting_approval" || status === "paused") {
    return "等待继续动作";
  }
  if (activeTask?.currentStep) {
    return activeTask.currentStep;
  }
  const activePlanStep = activeTask?.planSteps?.find((step) =>
    ["active", "running", "started", "pending", "verifying"].includes(String(step.status ?? "").toLowerCase()),
  );
  if (activePlanStep?.title) {
    return activePlanStep.title;
  }
  return buildCurrentStatusLabel(activeTask);
}

function CockpitDetailSection({
  title,
  summary,
  signals,
}: {
  title: string;
  summary: string;
  signals: CockpitSignal[];
}) {
  if (!signals.length) {
    return null;
  }
  return (
    <details className="runtime-cockpit-detail">
      <summary>
        <span>{title}</span>
        <strong>{summary}</strong>
      </summary>
      <dl>
        {signals.map((signal, index) => (
          <div key={`${signal.label}-${index}`} data-tone={signal.tone ?? "neutral"}>
            <dt>{signal.label}</dt>
            <dd>{signal.value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function buildCockpitMetrics({
  activeTask,
  approvals,
  patches,
  traces,
}: RuntimeCockpitPanelProps): CockpitMetric[] {
  const changedFiles =
    activeTask?.changedFiles?.length ??
    patches?.reduce((total, patch) => total + (patch.files?.length ?? patch.filesChanged ?? 0), 0) ??
    0;
  const commands = activeTask?.commands?.length ?? 0;
  const verification = activeTask?.verification ?? [];
  const verificationCommands = getVerificationLikeCommands(activeTask);
  const passedVerification =
    verification.filter((item) => item.status === "passed" || item.status === "completed").length ||
    verificationCommands.filter((item) => ["passed", "completed", "succeeded"].includes(String(item.status ?? "").toLowerCase())).length;
  const failedVerification = verification.filter((item) => item.status === "failed" || item.status === "error").length;
  const pendingApprovals = approvals?.filter((approval) => approval.status === "pending").length ?? 0;
  const failedSignals = traces?.filter((trace) =>
    ["failed", "error", "cancelled"].includes(String(trace.status ?? "").toLowerCase()),
  ).length ?? 0;

  const metrics: CockpitMetric[] = [
    { label: "文件", value: changedFiles ? `${changedFiles}` : "0", tone: changedFiles > 0 ? "primary" : "neutral" },
    { label: "命令", value: commands ? String(commands) : "0", tone: commands > 0 ? "primary" : "neutral" },
    {
      label: "验证",
      value: verification.length ? `${passedVerification} 通过` : "0",
      tone: failedVerification > 0 ? "danger" : passedVerification > 0 ? "success" : "neutral",
    },
    { label: "审批", value: pendingApprovals ? `${pendingApprovals} 待处理` : "0", tone: pendingApprovals > 0 ? "warning" : "neutral" },
    { label: "异常", value: failedSignals ? String(failedSignals) : "0", tone: failedSignals > 0 ? "danger" : "neutral" },
  ];

  return metrics.filter((metric) => {
    if (metric.label === "文件") return changedFiles > 0;
    if (metric.label === "命令") return commands > 0;
    if (metric.label === "验证") return verification.length > 0 || verificationCommands.length > 0;
    if (metric.label === "审批") return pendingApprovals > 0;
    if (metric.label === "异常") return failedSignals > 0;
    return false;
  });
}

function shouldShowMetrics({
  activeTask,
  metrics,
  blockingApproval,
  compactOperationalSignals,
}: {
  activeTask?: SessionWorkspaceActiveTask | null;
  metrics: CockpitMetric[];
  blockingApproval?: ReturnType<typeof latestBlockingApproval>;
  compactOperationalSignals: CockpitSignal[];
}) {
  const status = activeTask?.status?.toLowerCase();
  if (!metrics.length) {
    return false;
  }
  if (blockingApproval) {
    return true;
  }
  if (compactOperationalSignals.some((signal) => signal.tone === "danger" || signal.tone === "warning")) {
    return true;
  }
  if (["completed", "succeeded"].includes(status ?? "")) {
    return metrics.some((metric) => metric.label === "文件");
  }
  return true;
}

export function RuntimeCockpitPanel(props: RuntimeCockpitPanelProps) {
  const { activeTask, approvals, contextPreview } = props;
  const hasRuntimeState = Boolean(
    activeTask ||
      approvals?.length ||
      props.patches?.length ||
      props.traces?.length ||
      contextPreview?.budgetStats,
  );
  if (!hasRuntimeState) {
    return null;
  }

  const phase = getTaskPhase(activeTask);
  const completionEvidence = latestCompletionEvidence(approvals);
  const blockingApproval = latestBlockingApproval(approvals);
  const budget = formatBudgetRatio(contextPreview);
  const metrics = buildCockpitMetrics(props);
  const gateStatus = completionEvidence?.gateStatus;
  const reviewSummary = completionEvidence?.summary;
  const primarySummary =
    (["completed", "succeeded"].includes(String(activeTask?.status ?? "").toLowerCase()) ? buildCompletedSummary(activeTask) : undefined) ||
    reviewSummary ||
    blockingApproval?.summary ||
    (activeTask ? buildTaskProgressSummary(activeTask) : "发送一条需求后，我们就开始分析、修改和验证。");
  const acceptanceSignals = compactSignals([
    completionEvidence?.evidenceLevel ? { label: "证据级别", value: completionEvidence.evidenceLevel, tone: "info" } : null,
    completionEvidence?.status ? { label: "证据状态", value: completionEvidence.status, tone: "neutral" } : null,
    ...(completionEvidence?.metrics ?? []).map((metric) => ({ label: metric.label, value: metric.value, tone: "neutral" as const })),
    ...(completionEvidence?.issues ?? []).map((issue) => ({ label: "问题", value: issue, tone: "warning" as const })),
    completionEvidence?.reviewConclusion
      ? {
          label: "审核结论",
          value: [
            completionEvidence.reviewConclusion.decision,
            completionEvidence.reviewConclusion.decidedBy,
            completionEvidence.reviewConclusion.summary,
          ]
            .filter(Boolean)
            .join(" | "),
          tone: completionEvidence.reviewConclusion.decision === "rejected" ? "danger" : "success",
        }
      : null,
    ...approvalAuditSignals(completionEvidence),
  ], 10);
  const providerSignals = latestTraceSignals(props.traces, isProviderTrace).filter(
    isUsefulProviderSignal,
  );
  const contextDetailSignals = contextSignals(contextPreview, budget);
  const handoffSignals = workflowSignals(activeTask);
  const workspaceDetailSignals = workspaceSignals(activeTask);
  const highlights = buildHighlights(activeTask, approvals);
  const taskStatus = activeTask?.status?.toLowerCase();
  const canPauseTask = Boolean(
    activeTask?.id &&
      props.onPauseTask &&
      ["running", "planning", "verifying", "queued", "waiting_approval"].includes(taskStatus ?? ""),
  );
  const canResumeTask = Boolean(activeTask?.id && props.onResumeTask && taskStatus === "paused");
  const hasTaskActions = Boolean(props.onRefreshTask || canPauseTask || canResumeTask);
  const hasRiskyOperationalSignals =
    providerSignals.some((signal) => signal.tone === "danger" || signal.tone === "warning");
  const showDeepOperationalDetails =
    Boolean(blockingApproval) || hasRiskyOperationalSignals || acceptanceSignals.some((signal) => signal.tone === "warning" || signal.tone === "danger");
  const showWorkflowDetails = Boolean(handoffSignals.length && (taskStatus === "paused" || taskStatus === "failed"));
  const lowerSummaryLabel = highlights.blocker ? "待处理" : highlights.latestCheck ? "最近检查" : "当前状态";
  const lowerSummaryValue =
    highlights.blocker ??
    highlights.latestCheck ??
    (activeTask ? buildAcceptanceSummary(completionEvidence, activeTask) : blockingApproval?.summary || "等待新任务");
  const compactOperationalSignals = compactSignals([
    highlights.pendingApproval
      ? { label: "待审批", value: highlights.pendingApproval, tone: "warning" as const }
      : null,
    ...providerSignals.slice(0, 2),
    ...contextDetailSignals.filter((signal) => signal.tone === "warning" || signal.tone === "danger").slice(0, 2),
  ], 4);
  const latestWorkspaceSignals = compactSignals([
    ...workspaceDetailSignals.slice(0, 3),
    highlights.pendingApproval
      ? { label: "待审批", value: highlights.pendingApproval, tone: "warning" as const }
      : null,
  ], 4);
  const showMetrics = shouldShowMetrics({
    activeTask,
    metrics,
    blockingApproval,
    compactOperationalSignals,
  });
  const isCompletedTask = ["completed", "succeeded"].includes(taskStatus ?? "");
  const shouldShowContextCard = Boolean(budget && !isCompletedTask);
  const contextBudgetCard = shouldShowContextCard && budget
    ? {
        label: "上下文预算",
        value: `${budget.percent}%`,
        meta: `${budget.used}/${budget.max}`,
        tone: budget.tone,
      }
    : null;
  const visibleLowerCards = [
    { label: "任务状态", value: buildCurrentStatusLabel(activeTask) },
    { label: lowerSummaryLabel, value: lowerSummaryValue },
    contextBudgetCard,
  ].filter(Boolean) as Array<{ label: string; value: string; meta?: string; tone?: "success" | "warning" | "danger" }>;
  const compactDetailsLabel = isCompletedTask ? "摘要" : "最近动态";
  const compactDetailsSummary = isCompletedTask
    ? "当前无需额外关注"
    : compactOperationalSignals.length
      ? `${compactOperationalSignals.length} 条摘要`
      : "当前平稳";
  const compactSignalsForDisplay = isCompletedTask
    ? compactOperationalSignals.filter((signal) => signal.tone === "warning" || signal.tone === "danger")
    : compactOperationalSignals;
  const hasCompactDetailSections = Boolean(compactSignalsForDisplay.length || showDeepOperationalDetails);

  return (
    <section className="runtime-cockpit-panel" aria-label="运行态概览">
      <header className="runtime-cockpit-header">
        <div>
          <p className="session-kicker">运行态</p>
          <h2>{activeTask?.goal || (blockingApproval ? "等待你处理审批" : "等待新任务")}</h2>
        </div>
        <div className="runtime-cockpit-status">
          <StatusBadge label={getTaskPhaseLabel(phase)} tone={getTaskPhaseTone(phase)} compact />
          {gateStatus ? <StatusBadge label={gateStatus} tone="warning" compact /> : null}
          {blockingApproval ? (
            <StatusBadge
              label={formatApprovalLabel(blockingApproval.kind ?? "approval")}
              tone={getStatusTone(blockingApproval.status)}
              compact
            />
          ) : null}
        </div>
      </header>

      <p className="runtime-cockpit-summary">{primarySummary}</p>

      {hasTaskActions ? (
        <div className="runtime-cockpit-actions" aria-label="任务操作">
          {props.onRefreshTask ? (
            <Button
              size="sm"
              variant="secondary"
              loading={props.taskBusyAction === "refresh"}
              onClick={() => {
                void props.onRefreshTask?.();
              }}
            >
              刷新
            </Button>
          ) : null}
          {props.onPauseTask ? (
            <Button
              size="sm"
              variant="secondary"
              loading={props.taskBusyAction === "pause"}
              disabled={!canPauseTask || !activeTask?.id}
              onClick={() => {
                if (activeTask?.id) {
                  void props.onPauseTask?.(activeTask.id);
                }
              }}
            >
              暂停
            </Button>
          ) : null}
          {props.onResumeTask ? (
            <Button
              size="sm"
              variant="primary"
              loading={props.taskBusyAction === "resume"}
              disabled={!canResumeTask || !activeTask?.id}
              onClick={() => {
                if (activeTask?.id) {
                  void props.onResumeTask?.(activeTask.id);
                }
              }}
            >
              继续
            </Button>
          ) : null}
        </div>
      ) : null}

      {showMetrics ? (
        <dl className="runtime-cockpit-metrics">
          {metrics.map((metric) => (
            <div key={metric.label} data-tone={metric.tone ?? "neutral"}>
              <dt>{metric.label}</dt>
              <dd>{metric.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <div className="runtime-cockpit-lower">
        {visibleLowerCards.map((card) => (
          <article key={card.label} className={card.label === "上下文预算" ? "runtime-cockpit-budget" : undefined} data-tone={card.tone}>
            <span>{card.label}</span>
            <strong>{card.value}</strong>
            {card.meta ? <i>{card.meta}</i> : null}
          </article>
        ))}
      </div>

      {hasCompactDetailSections ? (
        <div className="runtime-cockpit-details" aria-label="运行态概览详情">
          {compactSignalsForDisplay.length ? (
            <CockpitDetailSection
              title={compactDetailsLabel}
              summary={compactDetailsSummary}
              signals={compactSignalsForDisplay}
            />
          ) : null}
          {showDeepOperationalDetails ? (
            <>
              <CockpitDetailSection
                title="完成依据"
                summary={gateStatus ? gateStatus.replace(/_/g, " ") : "证据轨迹"}
                signals={acceptanceSignals}
              />
              {providerSignals.length ? (
                <CockpitDetailSection
                  title="模型异常"
                  summary={`${providerSignals.length} 条信号`}
                  signals={providerSignals}
                />
              ) : null}
              {contextDetailSignals.length ? (
                <CockpitDetailSection
                  title="上下文"
                  summary={budget ? `${budget.percent}% 预算` : "当前平稳"}
                  signals={contextDetailSignals}
                />
              ) : null}
              {showWorkflowDetails ? (
                <CockpitDetailSection
                  title="接力状态"
                  summary={`${handoffSignals.length} 条信号`}
                  signals={handoffSignals}
                />
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
