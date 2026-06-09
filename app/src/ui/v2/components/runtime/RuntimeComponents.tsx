import { useState, type ReactNode } from "react";
import { formatStatusLabel } from "../../../copy";
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
  return date.toLocaleString("zh-CN", { hour12: false });
}

function clampRatio(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

function formatToolNameLabel(toolName?: string) {
  if (!toolName) return "工具调用";
  const normalized = toolName.trim().toLowerCase();
  const labels: Record<string, string> = {
    apply_patch: "应用文件改动",
    write_file: "写入文件",
    run_command: "运行命令",
    shell_command: "运行命令",
    list_dir: "查看目录",
    list_directory: "查看目录",
    read_file: "读取文件",
    search_files: "搜索文件",
    code_search: "搜索代码",
    web_fetch: "读取网页",
    browser: "浏览器操作",
    git_status: "查看 Git 状态",
    git_diff: "查看代码差异",
  };
  return labels[normalized] ?? toolName.replace(/_/g, " ");
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
  label = "上下文预算",
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
        <StatusBadge label={formatStatusLabel(tone)} tone={tone} compact />
      </header>
      <div
        className="yb-context-budget-track"
      aria-label={`${label}: 已使用 ${formatCount(usedTokens)} / ${formatCount(maxTokens)} 令牌`}
        role="meter"
        aria-valuemin={0}
        aria-valuemax={maxTokens}
        aria-valuenow={Math.min(usedTokens, maxTokens)}
      >
        <span style={{ width: `${usedRatio * 100}%` }} />
        {reservedTokens > 0 ? <i style={{ width: `${reservedRatio * 100}%` }} /> : null}
      </div>
      <footer>
        <span>已用 {formatCount(usedTokens)}</span>
        {reservedTokens > 0 ? <span>预留 {formatCount(reservedTokens)}</span> : null}
        <span>上限 {formatCount(maxTokens)}</span>
      </footer>
    </article>
  );
}

export interface ProviderSelectionSnapshot {
  providerMode: string;
  model?: string;
  useBackground: boolean;
  reason: string;
  confidence?: number;
  fallbackReason?: string;
  createdAt: string | number;
}

export interface ProviderSelectionCardProps {
  decision: ProviderSelectionSnapshot;
  onInspect?: () => void;
}

export function ProviderSelectionCard({ decision, onInspect }: ProviderSelectionCardProps) {
  const confidence = typeof decision.confidence === "number" ? Math.round(decision.confidence * 100) : undefined;

  return (
    <article className="yb-routing-card">
      <header>
        <div>
          <p className="yb-runtime-kicker">模型</p>
          <h3>{decision.model ?? decision.providerMode}</h3>
        </div>
        <StatusBadge label={decision.useBackground ? "后台" : "前台"} tone={decision.useBackground ? "primary" : "neutral"} compact />
      </header>
      <p>{decision.reason}</p>
      <dl>
        <div>
          <dt>供应商</dt>
          <dd>{decision.providerMode}</dd>
        </div>
        <div>
          <dt>置信度</dt>
          <dd>{confidence !== undefined ? `${confidence}%` : "--"}</dd>
        </div>
        <div>
          <dt>创建时间</dt>
          <dd>{formatDate(decision.createdAt) ?? "--"}</dd>
        </div>
      </dl>
      {decision.fallbackReason ? <small>{decision.fallbackReason}</small> : null}
      {onInspect ? (
        <footer>
          <Button size="sm" variant="ghost" onClick={onInspect}>查看</Button>
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
  const title = toolCall.serverName
    ? `${toolCall.serverName}.${formatToolNameLabel(toolCall.toolName)}`
    : formatToolNameLabel(toolCall.toolName);

  return (
    <article className="yb-tool-trace" data-status={toolCall.status}>
      <button type="button" className="yb-tool-trace-head" aria-expanded={isExpanded} onClick={toggle}>
        <span>{title}</span>
        <StatusBadge label={formatStatusLabel(toolCall.status)} tone={toneFromStatus(toolCall.status)} compact />
        <i>{toolCall.latencyMs !== undefined ? `${toolCall.latencyMs}ms` : formatDate(toolCall.startedAt) ?? ""}</i>
      </button>
      {toolCall.outputPreview || toolCall.error ? <p>{toolCall.error ?? toolCall.outputPreview}</p> : null}
      {isExpanded ? (
        <div className="yb-tool-trace-detail">
          {toolCall.inputPreview ? (
            <section>
              <header>
                <strong>输入</strong>
                {onCopyInput ? <Button size="xs" variant="ghost" onClick={onCopyInput}>复制</Button> : null}
              </header>
              <pre>{toolCall.inputPreview}</pre>
            </section>
          ) : null}
          {toolCall.outputPreview || toolCall.error ? (
            <section>
              <header>
                <strong>{toolCall.error ? "错误" : "输出"}</strong>
                {onCopyOutput ? <Button size="xs" variant="ghost" onClick={onCopyOutput}>复制</Button> : null}
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
  previewRows?: Array<{ label: string; value: string }>;
  previewSections?: Array<{
    kind: "items";
    title: string;
    items: Array<{ id: string; title: string; description?: string; meta?: string[] }>;
  }>;
  completionEvidence?: {
    gateStatus?: string;
    evidenceLevel?: string;
    status?: string;
    summary: string;
    metrics: Array<{ label: string; value: string }>;
    issues: string[];
    audit?: {
      approvals?: Array<{
        approvalId?: string;
        kind?: string;
        decision?: string;
        decidedBy?: string;
        gateStatus?: string;
        evidenceLevel?: string;
        summary?: string;
        reviewStatus?: string;
        verificationStatus?: string;
      }>;
    };
    reviewConclusion?: {
      approvalId?: string;
      decision?: string;
      decidedBy?: string;
      decidedAt?: number;
      gateStatus?: string;
      summary?: string;
    };
  };
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
  const statusTone: UiTone = pending
    ? "warning"
    : approval.status === "approved"
      ? "success"
      : approval.status === "rejected"
        ? "danger"
        : toneFromStatus(approval.status);

  return (
    <article className="yb-approval-card" data-risk={approval.risk ?? "low"}>
      <header>
        <div>
          <p className="yb-runtime-kicker">{approval.kind ?? "审批"}</p>
          <h3>{approval.title}</h3>
        </div>
        <div className="yb-runtime-status-stack">
          <StatusBadge label={formatStatusLabel(approval.status)} tone={statusTone} compact />
          <StatusBadge label={formatStatusLabel(approval.risk ? `${approval.risk} risk` : "low risk")} tone={riskTone} compact />
        </div>
      </header>
      {approval.summary ? <p className="yb-approval-summary">{approval.summary}</p> : null}
      {approval.completionEvidence ? <CompletionEvidencePanel evidence={approval.completionEvidence} /> : null}
      {approval.previewSections?.length ? <PreviewSections sections={approval.previewSections} /> : null}
      {approval.previewRows?.length ? (
        <dl className="yb-approval-preview" aria-label="审批预览">
          {approval.previewRows.slice(0, 6).map((row) => (
            <div key={`${row.label}:${row.value}`}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <dl>
        {approval.command ? (
          <div>
            <dt>命令</dt>
            <dd>{approval.command}</dd>
          </div>
        ) : null}
        {approval.cwd ? (
          <div>
            <dt>工作目录</dt>
            <dd>{approval.cwd}</dd>
          </div>
        ) : null}
        <div>
          <dt>状态</dt>
          <dd>{formatStatusLabel(approval.status)}</dd>
        </div>
      </dl>
      <footer>
        {pending ? (
          <>
            <Button size="sm" variant="primary" aria-label="批准" loading={busy} onClick={() => onApprove(approval.id)}>
              批准
            </Button>
            <Button size="sm" variant="danger" aria-label="拒绝" loading={busy} onClick={() => onReject(approval.id)}>
              拒绝
            </Button>
          </>
        ) : null}
        {onViewDetails ? <Button size="sm" variant="ghost" onClick={() => onViewDetails(approval.id)}>详情</Button> : null}
      </footer>
    </article>
  );
}

function PreviewSections({ sections }: { sections: NonNullable<ApprovalRecordView["previewSections"]> }) {
  return (
    <>
      {sections.map((section, sectionIndex) => (
        <section className="yb-preview-section" aria-label={section.title} key={`${section.kind}:${section.title}:${sectionIndex}`}>
          <header>
            <span>{section.title}</span>
          </header>
          <div>
            {section.items.slice(0, 8).map((item) => (
              <article key={item.id}>
                <span aria-hidden="true" />
                <div>
                  <strong>{item.title}</strong>
                  {item.meta?.length ? <small>{item.meta.join(" · ")}</small> : null}
                  {item.description ? <p>{item.description}</p> : null}
                </div>
                <code>{item.id}</code>
              </article>
            ))}
          </div>
        </section>
      ))}
    </>
  );
}

function CompletionEvidencePanel({
  evidence,
}: {
  evidence: NonNullable<ApprovalRecordView["completionEvidence"]>;
}) {
  const auditApprovals = (evidence.audit?.approvals ?? [])
    .filter((approval) => approval.summary || approval.decision || approval.gateStatus || approval.reviewStatus || approval.verificationStatus)
    .slice(-3);

  return (
    <section className="yb-approval-evidence" aria-label="Completion evidence">
      <div className="yb-approval-evidence-head">
        <span>{evidence.gateStatus ?? "review"}</span>
        {evidence.evidenceLevel ? <span>{evidence.evidenceLevel}</span> : null}
        {evidence.status ? <span>{evidence.status}</span> : null}
      </div>
      <p>{evidence.summary}</p>
      {evidence.metrics.length ? (
        <dl className="yb-approval-evidence-metrics">
          {evidence.metrics.slice(0, 6).map((metric) => (
            <div key={`${metric.label}:${metric.value}`}>
              <dt>{metric.label}</dt>
              <dd>{metric.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {evidence.issues.length ? (
        <ul className="yb-approval-evidence-issues">
          {evidence.issues.slice(0, 4).map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      ) : null}
      {auditApprovals.length ? (
        <ul className="yb-approval-evidence-issues" aria-label="Completion audit">
          {auditApprovals.map((approval, index) => (
            <li key={approval.approvalId ?? `${approval.kind ?? "approval"}:${index}`}>
              {approval.summary ? <span>{approval.summary}</span> : null}
              {[approval.decision, approval.gateStatus, approval.reviewStatus, approval.verificationStatus]
                .filter(Boolean)
                .map((part) => (
                  <small key={part}> {part}</small>
                ))}
            </li>
          ))}
        </ul>
      ) : null}
      {evidence.reviewConclusion ? (
        <p className="yb-approval-evidence-review">
          {[
            evidence.reviewConclusion.decision ? `review ${evidence.reviewConclusion.decision}` : null,
            evidence.reviewConclusion.decidedBy ? `by ${evidence.reviewConclusion.decidedBy}` : null,
            evidence.reviewConclusion.summary,
          ].filter(Boolean).join(" | ")}
        </p>
      ) : null}
    </section>
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
  const visibleFiles = changedFiles.slice(0, 5);
  return (
    <article className="yb-patch-plan">
      <header>
        <div>
          <p className="yb-runtime-kicker">文件改动</p>
          <h3>{patch.summary}</h3>
        </div>
        <StatusBadge label={formatStatusLabel(patch.status)} tone={toneFromStatus(patch.status)} compact />
      </header>
      <dl>
        <div>
          <dt>文件</dt>
          <dd>{patch.filesChanged ?? changedFiles.length}</dd>
        </div>
        {patch.additions !== undefined ? (
          <div>
            <dt>新增</dt>
            <dd>+{patch.additions}</dd>
          </div>
        ) : null}
        {patch.deletions !== undefined ? (
          <div>
            <dt>删除</dt>
            <dd>-{patch.deletions}</dd>
          </div>
        ) : null}
      </dl>
      {changedFiles.length ? (
        <ul>
          {visibleFiles.map((file) => (
            <li key={file.path}>
              <span>{file.path}</span>
              <small>{formatStatusLabel(file.status ?? "changed")} {file.additions !== undefined ? `+${file.additions}` : ""} {file.deletions !== undefined ? `-${file.deletions}` : ""}</small>
            </li>
          ))}
          {changedFiles.length > visibleFiles.length ? (
            <li>
              <span>另有 {changedFiles.length - visibleFiles.length} 个文件</span>
              <small>可在右侧文件区查看</small>
            </li>
          ) : null}
        </ul>
      ) : null}
      <footer>
        <Button size="sm" variant="secondary" aria-label="查看差异" onClick={() => onOpenDiff(patch.id)}>查看差异</Button>
        {onApply ? <Button size="sm" variant="primary" onClick={() => onApply(patch.id)}>应用</Button> : null}
        {onReject ? <Button size="sm" variant="danger" onClick={() => onReject(patch.id)}>拒绝</Button> : null}
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
  const output = command.stderr || command.stdout || "暂无输出。";

  return (
    <article className="yb-command-output">
      <header>
        <div>
          <p className="yb-runtime-kicker">命令</p>
          <h3>{command.command}</h3>
        </div>
        <StatusBadge label={formatStatusLabel(command.status)} tone={toneFromStatus(command.status)} compact />
      </header>
      <div className="yb-command-output-meta">
        {command.cwd ? <span>{command.cwd}</span> : null}
        {command.exitCode !== undefined && command.exitCode !== null ? <span>退出码 {command.exitCode}</span> : null}
        {command.durationMs !== undefined ? <span>{command.durationMs}ms</span> : null}
      </div>
      <pre style={{ maxHeight }}>{output}</pre>
      {onCopy ? (
        <footer>
          <Button size="sm" variant="ghost" aria-label="复制输出" onClick={onCopy}>复制输出</Button>
        </footer>
      ) : null}
    </article>
  );
}
