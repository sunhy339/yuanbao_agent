import { StatusBadge } from "../../../v2/components/ui";
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
}

interface CockpitMetric {
  label: string;
  value: string;
  tone?: "neutral" | "success" | "warning" | "danger" | "primary" | "info";
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
  const passedVerification = verification.filter((item) => item.status === "passed" || item.status === "completed").length;
  const failedVerification = verification.filter((item) => item.status === "failed" || item.status === "error").length;
  const pendingApprovals = approvals?.filter((approval) => approval.status === "pending").length ?? 0;
  const failedSignals = traces?.filter((trace) =>
    ["failed", "error", "cancelled"].includes(String(trace.status ?? "").toLowerCase()),
  ).length ?? 0;

  return [
    { label: "Files", value: String(changedFiles), tone: changedFiles > 0 ? "primary" : "neutral" },
    { label: "Commands", value: String(commands), tone: commands > 0 ? "primary" : "neutral" },
    {
      label: "Verified",
      value: verification.length ? `${passedVerification}/${verification.length}` : "0",
      tone: failedVerification > 0 ? "danger" : passedVerification > 0 ? "success" : "neutral",
    },
    { label: "Approvals", value: String(pendingApprovals), tone: pendingApprovals > 0 ? "warning" : "neutral" },
    { label: "Signals", value: String(failedSignals), tone: failedSignals > 0 ? "danger" : "neutral" },
  ];
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
  const primarySummary = reviewSummary || blockingApproval?.summary || buildTaskProgressSummary(activeTask);

  return (
    <section className="runtime-cockpit-panel" aria-label="Runtime cockpit">
      <header className="runtime-cockpit-header">
        <div>
          <p className="session-kicker">Runtime cockpit</p>
          <h2>{activeTask?.goal || "No active task"}</h2>
        </div>
        <div className="runtime-cockpit-status">
          <StatusBadge label={getTaskPhaseLabel(phase)} tone={getTaskPhaseTone(phase)} compact />
          {gateStatus ? <StatusBadge label={gateStatus} tone="warning" compact /> : null}
          {blockingApproval ? (
            <StatusBadge
              label={formatStatusLabel(blockingApproval.kind ?? "approval")}
              tone={getStatusTone(blockingApproval.status)}
              compact
            />
          ) : null}
        </div>
      </header>

      <p className="runtime-cockpit-summary">{primarySummary}</p>

      <dl className="runtime-cockpit-metrics">
        {metrics.map((metric) => (
          <div key={metric.label} data-tone={metric.tone ?? "neutral"}>
            <dt>{metric.label}</dt>
            <dd>{metric.value}</dd>
          </div>
        ))}
      </dl>

      <div className="runtime-cockpit-lower">
        <article>
          <span>Next check</span>
          <strong>
            {gateStatus
              ? gateStatus.replace(/_/g, " ")
              : blockingApproval
                ? "approval required"
                : activeTask?.verification?.length
                  ? "review verification"
                  : "watch execution"}
          </strong>
        </article>
        <article>
          <span>Acceptance</span>
          <strong>{completionEvidence?.issues?.[0] ?? completionEvidence?.evidenceLevel ?? "no blocking evidence"}</strong>
        </article>
        {budget ? (
          <article className="runtime-cockpit-budget" data-tone={budget.tone}>
            <span>Context budget</span>
            <strong>{budget.percent}%</strong>
            <i>
              {budget.used}/{budget.max}
            </i>
          </article>
        ) : null}
      </div>
    </section>
  );
}
