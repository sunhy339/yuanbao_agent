import { useState, type ReactNode } from "react";
import type { UiTone } from "../../types";
import { Button, StatusBadge } from "../ui";
import "./runtime.css";

type RuntimeStatus = "idle" | "running" | "success" | "completed" | "warning" | "pending" | "failed" | "error";

function toneFromStatus(status?: string): UiTone {
  if (!status) return "neutral";
  if (["success", "completed", "approved", "passed", "ready", "healthy"].includes(status)) return "success";
  if (["warning", "pending", "waiting", "queued", "degraded"].includes(status)) return "warning";
  if (["failed", "error", "rejected", "expired", "offline"].includes(status)) return "danger";
  if (["running", "loading", "checking"].includes(status)) return "primary";
  return "neutral";
}

function formatCount(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`;
  if (value >= 1_000) return `${Math.round(value / 100) / 10}k`;
  return String(Math.max(0, Math.round(value)));
}

function formatDate(value?: string | number) {
  if (!value) return undefined;
  const date = typeof value === "number" ? new Date(value) : new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toLocaleString("en-US", { hour12: false });
}

function clampRatio(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

export interface RuntimeSignalCardProps {
  title: string;
  value: string | number;
  tone?: UiTone;
  trend?: "up" | "down" | "flat";
  description?: string;
  loading?: boolean;
  action?: ReactNode;
}

export function RuntimeSignalCard({
  title,
  value,
  tone = "neutral",
  trend = "flat",
  description,
  loading = false,
  action,
}: RuntimeSignalCardProps) {
  return (
    <article className="yb-runtime-signal" data-tone={tone} data-trend={trend} data-loading={loading}>
      <div>
        <span>{title}</span>
        <strong>{loading ? "..." : value}</strong>
      </div>
      {description ? <p>{description}</p> : null}
      {action ? <footer>{action}</footer> : null}
    </article>
  );
}

export interface ContextBudgetBarProps {
  usedTokens: number;
  maxTokens: number;
  reservedTokens?: number;
  label?: string;
  warningThreshold?: number;
  dangerThreshold?: number;
}

export function ContextBudgetBar({
  usedTokens,
  maxTokens,
  reservedTokens = 0,
  label = "Context budget",
  warningThreshold = 0.72,
  dangerThreshold = 0.88,
}: ContextBudgetBarProps) {
  const usedRatio = maxTokens > 0 ? clampRatio(usedTokens / maxTokens) : 0;
  const reservedRatio = maxTokens > 0 ? clampRatio(reservedTokens / maxTokens) : 0;
  const tone: UiTone = usedRatio >= dangerThreshold ? "danger" : usedRatio >= warningThreshold ? "warning" : "success";

  return (
    <article className="yb-context-budget" data-tone={tone}>
      <header>
        <div>
          <p className="yb-runtime-kicker">{label}</p>
          <strong>{Math.round(usedRatio * 100)}%</strong>
        </div>
        <StatusBadge label={tone} tone={tone} compact />
      </header>
      <div
        className="yb-context-budget-track"
        aria-label={`${label}: ${formatCount(usedTokens)} of ${formatCount(maxTokens)} tokens used`}
        role="meter"
        aria-valuemin={0}
        aria-valuemax={maxTokens}
        aria-valuenow={Math.min(usedTokens, maxTokens)}
      >
        <span style={{ width: `${usedRatio * 100}%` }} />
        {reservedTokens > 0 ? <i style={{ width: `${reservedRatio * 100}%` }} /> : null}
      </div>
      <footer>
        <span>{formatCount(usedTokens)} used</span>
        {reservedTokens > 0 ? <span>{formatCount(reservedTokens)} reserved</span> : null}
        <span>{formatCount(maxTokens)} max</span>
      </footer>
    </article>
  );
}

export interface RoutingDecisionSnapshot {
  providerMode: string;
  model?: string;
  useBackground: boolean;
  reason: string;
  confidence?: number;
  fallbackReason?: string;
  createdAt: string | number;
}

export interface RoutingDecisionCardProps {
  decision: RoutingDecisionSnapshot;
  onInspect?: () => void;
}

export function RoutingDecisionCard({ decision, onInspect }: RoutingDecisionCardProps) {
  const confidence = typeof decision.confidence === "number" ? Math.round(decision.confidence * 100) : undefined;

  return (
    <article className="yb-routing-card">
      <header>
        <div>
          <p className="yb-runtime-kicker">Routing</p>
          <h3>{decision.model ?? decision.providerMode}</h3>
        </div>
        <StatusBadge label={decision.useBackground ? "background" : "inline"} tone={decision.useBackground ? "primary" : "neutral"} compact />
      </header>
      <p>{decision.reason}</p>
      <dl>
        <div>
          <dt>Provider</dt>
          <dd>{decision.providerMode}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{confidence !== undefined ? `${confidence}%` : "--"}</dd>
        </div>
        <div>
          <dt>Created</dt>
          <dd>{formatDate(decision.createdAt) ?? "--"}</dd>
        </div>
      </dl>
      {decision.fallbackReason ? <small>{decision.fallbackReason}</small> : null}
      {onInspect ? (
        <footer>
          <Button size="sm" variant="ghost" onClick={onInspect}>Inspect</Button>
        </footer>
      ) : null}
    </article>
  );
}

export interface ToolTraceRecord {
  id: string;
  toolName?: string;
  serverName?: string;
  status: RuntimeStatus | string;
  inputPreview?: string;
  outputPreview?: string;
  error?: string;
  latencyMs?: number;
  startedAt?: string | number;
  finishedAt?: string | number;
}

export interface ToolTraceCardProps {
  toolCall: ToolTraceRecord;
  expanded?: boolean;
  onToggleExpanded?: () => void;
  onCopyInput?: () => void;
  onCopyOutput?: () => void;
}

export function ToolTraceCard({
  toolCall,
  expanded,
  onToggleExpanded,
  onCopyInput,
  onCopyOutput,
}: ToolTraceCardProps) {
  const [localExpanded, setLocalExpanded] = useState(false);
  const isExpanded = expanded ?? localExpanded;
  const toggle = () => {
    if (onToggleExpanded) {
      onToggleExpanded();
    } else {
      setLocalExpanded((current) => !current);
    }
  };
  const title = toolCall.serverName ? `${toolCall.serverName}.${toolCall.toolName ?? "tool"}` : toolCall.toolName ?? "tool";

  return (
    <article className="yb-tool-trace" data-status={toolCall.status}>
      <button type="button" className="yb-tool-trace-head" aria-expanded={isExpanded} onClick={toggle}>
        <span>{title}</span>
        <StatusBadge label={toolCall.status} tone={toneFromStatus(toolCall.status)} compact />
        <i>{toolCall.latencyMs !== undefined ? `${toolCall.latencyMs}ms` : formatDate(toolCall.startedAt) ?? ""}</i>
      </button>
      {toolCall.outputPreview || toolCall.error ? <p>{toolCall.error ?? toolCall.outputPreview}</p> : null}
      {isExpanded ? (
        <div className="yb-tool-trace-detail">
          {toolCall.inputPreview ? (
            <section>
              <header>
                <strong>Input</strong>
                {onCopyInput ? <Button size="xs" variant="ghost" onClick={onCopyInput}>Copy</Button> : null}
              </header>
              <pre>{toolCall.inputPreview}</pre>
            </section>
          ) : null}
          {toolCall.outputPreview || toolCall.error ? (
            <section>
              <header>
                <strong>{toolCall.error ? "Error" : "Output"}</strong>
                {onCopyOutput ? <Button size="xs" variant="ghost" onClick={onCopyOutput}>Copy</Button> : null}
              </header>
              <pre>{toolCall.error ?? toolCall.outputPreview}</pre>
            </section>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

export interface ApprovalRecordView {
  id: string;
  title: string;
  kind?: string;
  status: RuntimeStatus | string;
  summary?: string;
  risk?: "low" | "medium" | "high";
  command?: string;
  cwd?: string;
  requestedAt?: string | number;
}

export interface ApprovalCardProps {
  approval: ApprovalRecordView;
  busy?: boolean;
  onApprove: (approvalId: string) => void | Promise<void>;
  onReject: (approvalId: string) => void | Promise<void>;
  onViewDetails?: (approvalId: string) => void;
}

export function ApprovalCard({ approval, busy = false, onApprove, onReject, onViewDetails }: ApprovalCardProps) {
  const pending = approval.status === "pending";
  const riskTone: UiTone = approval.risk === "high" ? "danger" : approval.risk === "medium" ? "warning" : "info";

  return (
    <article className="yb-approval-card" data-risk={approval.risk ?? "low"}>
      <header>
        <div>
          <p className="yb-runtime-kicker">{approval.kind ?? "Approval"}</p>
          <h3>{approval.title}</h3>
        </div>
        <StatusBadge label={approval.risk ?? "low risk"} tone={riskTone} compact />
      </header>
      {approval.summary ? <p>{approval.summary}</p> : null}
      <dl>
        {approval.command ? (
          <div>
            <dt>Command</dt>
            <dd>{approval.command}</dd>
          </div>
        ) : null}
        {approval.cwd ? (
          <div>
            <dt>CWD</dt>
            <dd>{approval.cwd}</dd>
          </div>
        ) : null}
        <div>
          <dt>Status</dt>
          <dd>{approval.status}</dd>
        </div>
      </dl>
      <footer>
        <Button size="sm" variant="primary" loading={busy} disabled={!pending} disabledReason="Only pending approvals can be approved" onClick={() => onApprove(approval.id)}>
          Approve
        </Button>
        <Button size="sm" variant="danger" loading={busy} disabled={!pending} disabledReason="Only pending approvals can be rejected" onClick={() => onReject(approval.id)}>
          Reject
        </Button>
        {onViewDetails ? <Button size="sm" variant="ghost" onClick={() => onViewDetails(approval.id)}>Details</Button> : null}
      </footer>
    </article>
  );
}

export interface PatchPlanRecord {
  id: string;
  summary: string;
  status: string;
  filesChanged?: number;
  additions?: number;
  deletions?: number;
}

export interface PatchPlanCardProps {
  patch: PatchPlanRecord;
  changedFiles?: Array<{ path: string; status?: string; additions?: number; deletions?: number }>;
  onOpenDiff: (patchId: string) => void;
  onApply?: (patchId: string) => void;
  onReject?: (patchId: string) => void;
}

export function PatchPlanCard({ patch, changedFiles = [], onOpenDiff, onApply, onReject }: PatchPlanCardProps) {
  return (
    <article className="yb-patch-plan">
      <header>
        <div>
          <p className="yb-runtime-kicker">Patch plan</p>
          <h3>{patch.summary}</h3>
        </div>
        <StatusBadge label={patch.status} tone={toneFromStatus(patch.status)} compact />
      </header>
      <dl>
        <div>
          <dt>Files</dt>
          <dd>{patch.filesChanged ?? changedFiles.length}</dd>
        </div>
        <div>
          <dt>Add</dt>
          <dd>+{patch.additions ?? 0}</dd>
        </div>
        <div>
          <dt>Del</dt>
          <dd>-{patch.deletions ?? 0}</dd>
        </div>
      </dl>
      {changedFiles.length ? (
        <ul>
          {changedFiles.slice(0, 4).map((file) => (
            <li key={file.path}>
              <span>{file.path}</span>
              <small>{file.status ?? "changed"} {file.additions !== undefined ? `+${file.additions}` : ""} {file.deletions !== undefined ? `-${file.deletions}` : ""}</small>
            </li>
          ))}
        </ul>
      ) : null}
      <footer>
        <Button size="sm" variant="secondary" onClick={() => onOpenDiff(patch.id)}>Open diff</Button>
        {onApply ? <Button size="sm" variant="primary" onClick={() => onApply(patch.id)}>Apply</Button> : null}
        {onReject ? <Button size="sm" variant="danger" onClick={() => onReject(patch.id)}>Reject</Button> : null}
      </footer>
    </article>
  );
}

export interface CommandLogRecordView {
  id: string;
  command: string;
  status: RuntimeStatus | string;
  cwd?: string;
  exitCode?: number | null;
  stdout?: string;
  stderr?: string;
  durationMs?: number;
}

export interface CommandOutputPanelProps {
  command: CommandLogRecordView;
  maxHeight?: number;
  autoScroll?: boolean;
  onCopy?: () => void;
}

export function CommandOutputPanel({ command, maxHeight = 220, onCopy }: CommandOutputPanelProps) {
  const output = command.stderr || command.stdout || "No output captured.";

  return (
    <article className="yb-command-output">
      <header>
        <div>
          <p className="yb-runtime-kicker">Command</p>
          <h3>{command.command}</h3>
        </div>
        <StatusBadge label={command.status} tone={toneFromStatus(command.status)} compact />
      </header>
      <div className="yb-command-output-meta">
        {command.cwd ? <span>{command.cwd}</span> : null}
        {command.exitCode !== undefined && command.exitCode !== null ? <span>exit {command.exitCode}</span> : null}
        {command.durationMs !== undefined ? <span>{command.durationMs}ms</span> : null}
      </div>
      <pre style={{ maxHeight }}>{output}</pre>
      {onCopy ? (
        <footer>
          <Button size="sm" variant="ghost" onClick={onCopy}>Copy output</Button>
        </footer>
      ) : null}
    </article>
  );
}
