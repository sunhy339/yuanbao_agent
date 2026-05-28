import type { RuntimeTimelineItem } from "../../workbench/workspaces/session/types";

export type CleanTone = "neutral" | "success" | "warning" | "danger" | "running";

export function compactText(value: string | null | undefined, max = 140) {
  const text = typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  if (!text) return "";
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(1, max - 1)).trimEnd()}...`;
}

export function basename(path?: string | null) {
  const normalized = String(path ?? "").replace(/\\/g, "/").replace(/\/+$/, "");
  return normalized.split("/").filter(Boolean).at(-1) || normalized || "workspace";
}

export function statusTone(status?: string | null): CleanTone {
  const normalized = status?.toLowerCase();
  if (!normalized) return "neutral";
  if (["completed", "succeeded", "passed", "approved", "applied", "recorded"].includes(normalized)) {
    return "success";
  }
  if (["running", "started", "planning", "verifying", "pending", "queued", "waiting", "waiting_approval"].includes(normalized)) {
    return "running";
  }
  if (["failed", "error", "rejected", "cancelled"].includes(normalized)) {
    return "danger";
  }
  if (["skipped", "warning"].includes(normalized)) {
    return "warning";
  }
  return "neutral";
}

export function statusLabel(status?: string | null) {
  const normalized = status?.trim().toLowerCase();
  const labels: Record<string, string> = {
    active: "进行中",
    applied: "已应用",
    approved: "已批准",
    cancelled: "已取消",
    completed: "已完成",
    failed: "失败",
    passed: "已通过",
    pending: "等待中",
    planning: "规划中",
    queued: "排队中",
    recorded: "已记录",
    rejected: "已拒绝",
    running: "运行中",
    skipped: "已跳过",
    started: "已开始",
    succeeded: "已成功",
    verifying: "验证中",
    waiting: "等待中",
    waiting_approval: "等待审批",
  };
  return normalized ? labels[normalized] ?? status ?? "未知" : "未知";
}

export function isInFlight(status?: string | null) {
  const normalized = status?.toLowerCase();
  return Boolean(
    normalized &&
      ["running", "started", "planning", "verifying", "pending", "queued", "waiting", "waiting_approval"].includes(normalized),
  );
}

export function formatDuration(durationMs?: number | null) {
  if (durationMs === undefined || durationMs === null || !Number.isFinite(durationMs)) return "";
  if (durationMs < 1000) return `${Math.round(durationMs)}ms`;
  const seconds = durationMs / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  return `${minutes}m ${rest}s`;
}

export function formatClock(value?: number | null) {
  if (!value || !Number.isFinite(value)) return "";
  return new Date(value).toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function toolLabel(name?: string | null) {
  const normalized = name?.trim();
  if (!normalized) return "工具";
  const labels: Record<string, string> = {
    apply_patch: "应用改动",
    bash: "命令",
    command: "命令",
    edit: "编辑",
    git_diff: "查看差异",
    git_status: "查看 Git 状态",
    list_dir: "查看目录",
    list_directory: "查看目录",
    read_file: "读取文件",
    run_command: "运行命令",
    search_files: "搜索文件",
    write_file: "写入文件",
  };
  return labels[normalized] ?? normalized.replace(/_/g, " ");
}

export function runtimeLabel(item: RuntimeTimelineItem) {
  if (item.kind === "approval") return item.title || "审批请求";
  if (item.kind === "patch") return item.title || "文件改动";
  if (item.kind === "command") return item.title || "命令";
  if (item.kind === "tool") return toolLabel(item.toolName) || item.title;
  return item.title || item.kind;
}

function parseRuntimeJson(value?: string) {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function findRuntimeText(record: Record<string, unknown> | null, keys: string[]) {
  if (!record) return "";
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function summarizeJsonOutput(value?: string) {
  const record = parseRuntimeJson(value);
  if (!record) return "";
  const items = Array.isArray(record.items) ? record.items : Array.isArray(record.entries) ? record.entries : null;
  if (items) {
    const names = items
      .map((item) => item && typeof item === "object" ? findRuntimeText(item as Record<string, unknown>, ["path", "name"]) : "")
      .filter(Boolean)
      .slice(0, 4);
    return `找到 ${items.length} 项${names.length ? `：${names.join(", ")}${items.length > names.length ? `，另有 ${items.length - names.length} 项` : ""}` : ""}`;
  }
  const changes = record.changes;
  if (Array.isArray(changes)) {
    return `${changes.length} 个改动`;
  }
  const summary = findRuntimeText(record, ["summary", "message", "status", "result"]);
  return summary ? compactText(summary, 150) : "";
}

export function runtimeSummary(item: RuntimeTimelineItem) {
  const jsonSummary = summarizeJsonOutput(item.rawDetail || item.code);
  const readableMeta = (item.meta ?? []).filter((meta) => !meta.trim().startsWith("{") && !meta.trim().startsWith("["));
  return compactText([jsonSummary || item.summary, ...readableMeta].filter(Boolean).join(" · "), 180);
}
