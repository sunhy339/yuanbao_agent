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

interface CockpitSignal {
  label: string;
  value: string;
  tone?: CockpitMetric["tone"];
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

function isMcpSkillTrace(trace: SessionWorkspaceTrace) {
  const haystack = `${trace.type} ${trace.source ?? ""} ${trace.title ?? ""} ${trace.summary ?? ""}`.toLowerCase();
  return haystack.includes("mcp") || haystack.includes("skill") || haystack.includes("tool_recovery");
}

function approvalAuditSignals(completionEvidence: ReturnType<typeof latestCompletionEvidence>): CockpitSignal[] {
  const audit = completionEvidence?.audit;
  const counts = audit?.approvalCounts;
  const signals = compactSignals([
    counts
      ? {
          label: "Approval audit",
          value: `${counts.approved ?? 0} approved / ${counts.pending ?? 0} pending / ${counts.rejected ?? 0} rejected`,
          tone: (counts.rejected ?? 0) > 0 ? "danger" : (counts.pending ?? 0) > 0 ? "warning" : "success",
        }
      : null,
    audit?.completionAdvisor
      ? {
          label: "Completion advisor",
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
          label: "Budget",
          value: `${budget.percent}% (${budget.used}/${budget.max})`,
          tone: budget.tone,
        }
      : null,
    stats?.trimmedSections?.length
      ? {
          label: "Trimmed",
          value: stats.trimmedSections.join(", "),
          tone: "warning",
        }
      : null,
    stats?.droppedSections?.length
      ? {
          label: "Dropped",
          value: stats.droppedSections.join(", "),
          tone: "danger",
        }
      : null,
    taskFocus?.currentStep
      ? {
          label: "Current step",
          value: taskFocus.currentStep,
          tone: "info",
        }
      : null,
  ]);
}

function workspaceSignals(activeTask?: SessionWorkspaceActiveTask | null): CockpitSignal[] {
  const latestCommands = [...(activeTask?.commands ?? [])].slice(-3).reverse();
  const latestVerification = [...(activeTask?.verification ?? [])].slice(-3).reverse();
  return compactSignals([
    activeTask?.changedFiles?.length
      ? {
          label: "Changed files",
          value: activeTask.changedFiles.slice(0, 4).map((file) => file.path).join(", "),
          tone: "primary",
        }
      : null,
    ...latestCommands.map((command): CockpitSignal => ({
      label: "Command",
      value: [command.command, command.status].filter(Boolean).join(" | "),
      tone: command.status === "failed" ? "danger" : command.status === "completed" ? "success" : "neutral",
    })),
    ...latestVerification.map((item): CockpitSignal => ({
      label: "Verification",
      value: [item.command ?? item.id ?? "check", item.status].filter(Boolean).join(" | "),
      tone: item.status === "failed" || item.status === "error" ? "danger" : item.status === "passed" ? "success" : "neutral",
    })),
  ], 6);
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
  const acceptanceSignals = compactSignals([
    completionEvidence?.evidenceLevel ? { label: "Evidence level", value: completionEvidence.evidenceLevel, tone: "info" } : null,
    completionEvidence?.status ? { label: "Evidence status", value: completionEvidence.status, tone: "neutral" } : null,
    ...(completionEvidence?.metrics ?? []).map((metric) => ({ label: metric.label, value: metric.value, tone: "neutral" as const })),
    ...(completionEvidence?.issues ?? []).map((issue) => ({ label: "Issue", value: issue, tone: "warning" as const })),
    completionEvidence?.reviewConclusion
      ? {
          label: "Review conclusion",
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
  const providerSignals = latestTraceSignals(props.traces, isProviderTrace);
  const mcpSkillSignals = latestTraceSignals(props.traces, isMcpSkillTrace);
  const contextDetailSignals = contextSignals(contextPreview, budget);
  const workspaceDetailSignals = workspaceSignals(activeTask);

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

      <div className="runtime-cockpit-details" aria-label="Runtime cockpit drill-down">
        <CockpitDetailSection
          title="Acceptance audit"
          summary={gateStatus ? gateStatus.replace(/_/g, " ") : "evidence trail"}
          signals={acceptanceSignals}
        />
        <CockpitDetailSection
          title="Provider recovery"
          summary={providerSignals.length ? `${providerSignals.length} signal(s)` : "quiet"}
          signals={providerSignals}
        />
        <CockpitDetailSection
          title="MCP / Skills"
          summary={mcpSkillSignals.length ? `${mcpSkillSignals.length} signal(s)` : "quiet"}
          signals={mcpSkillSignals}
        />
        <CockpitDetailSection
          title="Memory / Context"
          summary={budget ? `${budget.percent}% budget` : "no pressure"}
          signals={contextDetailSignals}
        />
        <CockpitDetailSection
          title="Workspace status"
          summary={workspaceDetailSignals.length ? `${workspaceDetailSignals.length} latest signal(s)` : "unchanged"}
          signals={workspaceDetailSignals}
        />
      </div>
    </section>
  );
}
