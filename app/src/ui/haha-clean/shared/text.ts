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

function parseJsonRecord(value?: string | null) {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function readRecordText(record: Record<string, unknown> | null, keys: string[]) {
  if (!record) return "";
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
    if (Array.isArray(value)) {
      const first = value.find((item) => typeof item === "string" && item.trim());
      if (typeof first === "string") return first.trim();
    }
  }
  return "";
}

function readRecordTextList(record: Record<string, unknown> | null, keys: string[]) {
  if (!record) return [];
  const values: string[] = [];
  const visit = (value: unknown, depth = 0) => {
    if (depth > 3 || values.length > 12) return;
    if (typeof value === "string" && value.trim()) {
      values.push(value.trim());
      return;
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      values.push(String(value));
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item) => visit(item, depth + 1));
      return;
    }
    if (value && typeof value === "object") {
      const entry = value as Record<string, unknown>;
      const direct = readRecordText(entry, ["path", "file", "name", "target"]);
      if (direct) values.push(direct);
      Object.values(entry).forEach((item) => visit(item, depth + 1));
    }
  };
  keys.forEach((key) => visit(record[key]));
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function firstPathFromText(value?: string | null) {
  const text = value?.trim();
  if (!text) return "";
  const explicit = /(?:path|file|cwd|target|root|路径|文件|工作目录)\s*[:=]\s*["']?([^"',\n\r]+)["']?/i.exec(text)?.[1]?.trim();
  if (explicit && /[./\\]/.test(explicit)) return explicit;
  const diffPath = /^(?:---|\+\+\+)\s+[ab]\/(.+)$/m.exec(text)?.[1]?.trim();
  if (diffPath) return diffPath;
  const token = text
    .split(/[\s"',:;|]+/)
    .find((part) => /[\\/]/.test(part) || /\.[a-z0-9]{1,8}$/i.test(part));
  return token?.replace(/^[ab]\//, "").trim() ?? "";
}

function diffPathsFromText(value?: string | null) {
  const text = value?.trim();
  if (!text) return [];
  const paths = new Set<string>();
  const add = (path?: string | null) => {
    const normalized = path
      ?.trim()
      .replace(/^["'`]+|["'`,;:]+$/g, "")
      .replace(/^[ab]\//, "");
    if (!normalized || normalized === "/dev/null") return;
    if (!/[./\\]/.test(normalized)) return;
    paths.add(normalized);
  };
  const gitPattern = /^diff --git\s+a\/(.+?)\s+b\/(.+)$/gm;
  let match: RegExpExecArray | null;
  while ((match = gitPattern.exec(text)) !== null) {
    add(match[2] || match[1]);
  }
  const headerPattern = /^(?:---|\+\+\+)\s+([^\r\n]+)$/gm;
  while ((match = headerPattern.exec(text)) !== null) {
    add(match[1]);
  }
  return Array.from(paths);
}

export function toolActionTitle({
  toolName,
  title,
  input,
  rawDetail,
  fallback = "工具调用",
}: {
  toolName?: string | null;
  title?: string | null;
  input?: string | null;
  rawDetail?: string | null;
  fallback?: string;
}) {
  const rawTitle = title?.trim() ?? "";
  const titleAsToolName = /^(apply_patch|write_file|read_file|list_dir|list_directory|search_files|code_search|run_command|command|bash|shell_command)$/i.test(rawTitle)
    ? rawTitle.toLowerCase()
    : "";
  const normalized = toolName?.trim().toLowerCase() || titleAsToolName;
  const label = toolLabel(normalized || title);
  const inputRecord = parseJsonRecord(input);
  const detailRecord = parseJsonRecord(rawDetail);
  const target =
    readRecordText(inputRecord, ["path", "file", "cwd", "root", "target", "query", "url", "command", "cmd"]) ||
    readRecordText(detailRecord, ["path", "file", "cwd", "root", "target", "query", "url", "command", "cmd"]) ||
    firstPathFromText(input) ||
    firstPathFromText(rawDetail) ||
    firstPathFromText(rawTitle);
  const fileTargets = [
    ...readRecordTextList(inputRecord, ["path", "paths", "file", "files", "target", "targets", "changes", "patches"]),
    ...readRecordTextList(detailRecord, ["path", "paths", "file", "files", "target", "targets", "changes", "patches"]),
    ...diffPathsFromText(input),
    ...diffPathsFromText(rawDetail),
    ...diffPathsFromText(rawTitle),
  ].filter((value) => /[\\/]|\.([a-z0-9]+)$/i.test(value) && !/^(approval|command|apply_patch|patch approval request)$/i.test(value));
  const uniqueFileTargets = Array.from(new Set(fileTargets));

  if (normalized === "apply_patch") {
    if (uniqueFileTargets.length === 1) return `修改 ${uniqueFileTargets[0]}`;
    if (uniqueFileTargets.length > 1) return `修改 ${uniqueFileTargets.length} 个文件`;
    if (target && /[\\/]|\.([a-z0-9]+)$/i.test(target)) return `修改 ${target}`;
    return "应用文件改动";
  }
  if (normalized === "write_file") {
    return target ? `写入 ${target}` : "写入文件";
  }
  if (normalized === "read_file") {
    return target ? `读取 ${target}` : "读取文件";
  }
  if (normalized === "list_dir" || normalized === "list_directory") {
    return target ? `查看 ${target}` : "查看目录";
  }
  if (normalized === "search_files" || normalized === "code_search") {
    return target ? `搜索 ${target}` : label;
  }
  if (normalized === "run_command" || normalized === "command" || normalized === "bash" || normalized === "shell_command") {
    return target ? `运行 ${compactText(target, 72)}` : "运行命令";
  }
  if (target && label !== target) return `${label} ${compactText(target, 72)}`;
  return title && !/^(apply_patch|write_file|read_file|list_dir|list_directory|search_files|code_search|run_command|command|request|approval|patch approval request)$/i.test(title.trim())
    ? rawTitle
    : label || fallback;
}

export function runtimeLabel(item: RuntimeTimelineItem) {
  if (item.kind === "approval") return toolActionTitle({ toolName: item.toolName, title: item.title, input: item.code, rawDetail: item.rawDetail, fallback: "审批请求" });
  if (item.kind === "patch") return toolActionTitle({ toolName: "apply_patch", title: item.title, input: item.code, rawDetail: item.rawDetail, fallback: "文件改动" });
  if (item.kind === "command") return toolActionTitle({ toolName: "run_command", title: item.title, input: item.code, rawDetail: item.rawDetail, fallback: "命令" });
  if (item.kind === "tool") return toolActionTitle({ toolName: item.toolName, title: item.title, input: item.code, rawDetail: item.rawDetail });
  return item.title || item.kind;
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
  const record = parseJsonRecord(value);
  if (!record) return "";
  const namedArrays: Array<{ keys: string[]; noun: string }> = [
    { keys: ["items", "entries", "children"], noun: "找到" },
    { keys: ["files", "changedFiles", "changes"], noun: "涉及文件" },
    { keys: ["matches", "results"], noun: "返回结果" },
  ];
  for (const group of namedArrays) {
    const key = group.keys.find((entry) => Array.isArray(record[entry]));
    const items = key ? record[key] as unknown[] : null;
    if (!items) continue;
    const names = items
      .map((item) => {
        if (typeof item === "string") return item.trim();
        return item && typeof item === "object" ? findRuntimeText(item as Record<string, unknown>, ["path", "file", "name", "title", "label"]) : "";
      })
      .filter(Boolean)
      .slice(0, 4);
    return `${group.noun} ${items.length} 项${names.length ? `：${names.join(", ")}${items.length > names.length ? `，另有 ${items.length - names.length} 项` : ""}` : ""}`;
  }

  const status = findRuntimeText(record, ["status", "state"]);
  const exitCode = findRuntimeText(record, ["exitCode", "exit_code", "code"]);
  const message = findRuntimeText(record, ["summary", "message", "result"]);
  const stdout = findRuntimeText(record, ["stdout", "output"]);
  const stderr = findRuntimeText(record, ["stderr", "error"]);
  const content = findRuntimeText(record, ["content", "text"]);
  const byteCount = findRuntimeText(record, ["bytes", "byteLength", "size"]);
  const parts: string[] = [];
  if (status) parts.push(statusLabel(status));
  if (exitCode) parts.push(`退出码 ${exitCode}`);
  if (message) parts.push(compactText(message, 92));
  if (!message && stdout) parts.push(compactText(stdout, 92));
  if (stderr) parts.push(compactText(stderr, 92));
  if (!parts.length && content) {
    parts.push(`读取完成，${content.length} 字符`);
  }
  if (!parts.length && byteCount) parts.push(`${byteCount} 字节`);
  return parts.length ? parts.join(" · ") : "";
}

export function runtimeSummary(item: RuntimeTimelineItem) {
  const jsonSummary = summarizeJsonOutput(item.rawDetail || item.code);
  const readableMeta = (item.meta ?? []).filter((meta) => !meta.trim().startsWith("{") && !meta.trim().startsWith("["));
  return compactText([jsonSummary || item.summary, ...readableMeta].filter(Boolean).join(" · "), 180);
}
