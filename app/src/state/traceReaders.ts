export function readRequestText(request: Record<string, unknown>, key: string, fallback: string): string {
  const value = request[key];
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return fallback;
}

export function readRequestNumber(request: Record<string, unknown>, key: string, fallback: number): number {
  const value = request[key];
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return fallback;
}

export function readRequestPatchId(request: Record<string, unknown>): string | undefined {
  const value = request.patch_id ?? request.patchId;
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  return undefined;
}

export function readEventText(payload: unknown, key: string): string | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const value = (payload as Record<string, unknown>)[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

export function readEventNumber(payload: unknown, key: string): number | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const value = (payload as Record<string, unknown>)[key];
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

export function summarizeValue(value: unknown, fallback = "not recorded", maxLength = 180): string {
  if (value === undefined || value === null) {
    return fallback;
  }

  const raw =
    typeof value === "string"
      ? value
      : typeof value === "number" || typeof value === "boolean"
        ? String(value)
        : (() => {
            try {
              return JSON.stringify(value);
            } catch {
              return fallback;
            }
          })();
  const compact = raw.replace(/\s+/g, " ").trim();
  if (!compact) {
    return fallback;
  }
  return compact.length > maxLength ? `${compact.slice(0, maxLength - 1)}...` : compact;
}

export function riskToLevel(value: string): "low" | "medium" | "high" {
  const normalized = value.toLowerCase();
  if (normalized.includes("delete") || normalized.includes("danger") || normalized.includes("network")) {
    return "high";
  }
  if (normalized.includes("write") || normalized.includes("command") || normalized.includes("patch")) {
    return "medium";
  }
  return "low";
}

export function countAddedLines(diffText: string): number {
  return diffText
    .split(/\r?\n/)
    .filter((line) => line.startsWith("+") && !line.startsWith("+++")).length;
}

export function countDeletedLines(diffText: string): number {
  return diffText
    .split(/\r?\n/)
    .filter((line) => line.startsWith("-") && !line.startsWith("---")).length;
}

export function parsePatchFiles(diffText = "", changedPaths: string[] = []) {
  if (!diffText.trim()) {
    return changedPaths
      .map((path) => path.trim().replace(/\\/g, "/"))
      .filter(Boolean)
      .filter((path, index, paths) => paths.indexOf(path) === index)
      .map((path) => ({
        path,
        status: "modified",
        additions: 0,
        deletions: 0,
        diff: "",
      }));
  }

  const sections = diffText.split(/^diff --git /m).filter(Boolean);
  return sections.map((section) => {
    const header = section.split(/\r?\n/, 1)[0] ?? "";
    const match = header.match(/^a\/(.+?) b\/(.+)$/);
    const path = match?.[2] ?? header.trim() ?? "unknown file";
    return {
      path,
      status: "modified",
      additions: countAddedLines(section),
      deletions: countDeletedLines(section),
      diff: `diff --git ${section}`.trim(),
    };
  });
}

export function getPayloadValue(payload: unknown, keys: string[]): unknown {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const record = payload as Record<string, unknown>;
  for (const key of keys) {
    if (record[key] !== undefined) {
      return record[key];
    }
  }
  return undefined;
}

export function truncateText(value: string, maxLength: number): string {
  const text = value.trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}...` : text;
}

export function formatRawValue(value: unknown, maxLength = 1800): string | undefined {
  if (value === undefined || value === null) {
    return undefined;
  }

  try {
    const raw = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    return raw && raw.trim() ? truncateText(raw, maxLength) : undefined;
  } catch {
    return summarizeValue(value, "", maxLength) || undefined;
  }
}

export function parseToolValue(value: unknown): unknown {
  if (typeof value !== "string") {
    return value;
  }

  const trimmed = value.trim();
  if (!trimmed || (!trimmed.startsWith("{") && !trimmed.startsWith("["))) {
    return value;
  }

  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function toolString(record: Record<string, unknown> | null, keys: string[]): string | undefined {
  if (!record) {
    return undefined;
  }

  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      return String(value);
    }
  }
  return undefined;
}

export function toolNumber(record: Record<string, unknown> | null, keys: string[]): number | undefined {
  if (!record) {
    return undefined;
  }

  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return undefined;
}

export function compactToolList(values: string[], limit = 5): string {
  const visible = values.filter(Boolean).slice(0, limit);
  const suffix = values.length > limit ? `，另有 ${values.length - limit} 项` : "";
  return visible.length ? `${visible.join(", ")}${suffix}` : "";
}

export function firstUsefulLine(value?: string): string | undefined {
  return value
    ?.split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);
}

export function summarizeToolArguments(toolName: string, value: unknown, fallback = "未记录参数"): string {
  if (typeof value === "string" && value.trim()) {
    return truncateText(value, 120);
  }
  const parsed = parseToolValue(value);
  const record = asRecord(parsed);
  const path = toolString(record, ["path", "file", "cwd", "root"]);

  if (toolName === "list_dir") {
    return `列出 ${path ?? "."}`;
  }
  if (toolName === "read_file") {
    return `读取 ${path ?? "文件"}`;
  }
  if (toolName === "search_files") {
    const query = toolString(record, ["query", "pattern", "glob"]);
    return query ? `搜索 ${query}${path ? ` @ ${path}` : ""}` : `搜索${path ? ` ${path}` : ""}`;
  }
  if (toolName === "apply_patch") {
    const filesValue = record?.files;
    const changedPathsValue = record?.changedPaths ?? record?.paths;
    const files = Array.isArray(filesValue)
      ? filesValue.map((item) => toolString(asRecord(item), ["path"])).filter((item): item is string => Boolean(item))
      : Array.isArray(changedPathsValue)
        ? changedPathsValue.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
        : [];
    return files.length ? `修改 ${compactToolList(files)}` : "应用补丁";
  }
  if (toolName === "run_command") {
    const command = toolString(record, ["command", "cmd"]);
    return command ? `运行 ${command}` : "运行命令";
  }

  return summarizeValue(value, fallback, 120);
}

export function summarizeToolResult(toolName: string, resultValue: unknown, errorValue: unknown, fallback = "等待结果") {
  if (errorValue !== undefined && errorValue !== null && summarizeValue(errorValue, "", 160)) {
    return `失败：${summarizeValue(errorValue, "", 160)}`;
  }

  if (typeof resultValue === "string" && resultValue.trim()) {
    return truncateText(resultValue, 160);
  }
  const parsed = parseToolValue(resultValue);
  const record = asRecord(parsed);

  if (toolName === "list_dir") {
    const itemsValue = record?.items ?? parsed;
    const items = Array.isArray(itemsValue) ? itemsValue : [];
    if (!items.length) {
      return resultValue === undefined ? fallback : "没有找到条目";
    }
    const names = items
      .map((item) => {
        const itemRecord = asRecord(item);
        const name = toolString(itemRecord, ["name", "path"]);
        const type = toolString(itemRecord, ["type"]);
        return name ? `${name}${type === "directory" ? "/" : ""}` : undefined;
      })
      .filter((item): item is string => Boolean(item));
    const dirCount = items.filter((item) => toolString(asRecord(item), ["type"]) === "directory").length;
    const fileCount = items.filter((item) => toolString(asRecord(item), ["type"]) === "file").length;
    return `找到 ${items.length} 项（${dirCount} 个目录，${fileCount} 个文件）：${compactToolList(names)}`;
  }

  if (toolName === "read_file") {
    const bytes = toolNumber(record, ["bytesRead", "bytes", "size"]);
    const content = toolString(record, ["content", "text"]);
    const preview = firstUsefulLine(content);
    return `读取完成${bytes !== undefined ? `，${bytes} 字节` : ""}${preview ? `：${truncateText(preview, 80)}` : ""}`;
  }

  if (toolName === "apply_patch") {
    const pathsValue = record?.changedPaths ?? record?.paths;
    const paths = Array.isArray(pathsValue)
      ? pathsValue.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
      : [];
    const error = toolString(record, ["error"]);
    if (error) {
      return `补丁失败：${truncateText(error, 140)}`;
    }
    return paths.length ? `补丁完成：${compactToolList(paths)}` : summarizeValue(resultValue, "补丁完成", 140);
  }

  if (toolName === "run_command") {
    const exitCode = toolNumber(record, ["exitCode", "code"]);
    const output = firstUsefulLine(toolString(record, ["stdout", "stderr", "output"]));
    return `命令${exitCode === undefined ? "完成" : `退出码 ${exitCode}`}${output ? `：${truncateText(output, 100)}` : ""}`;
  }

  return summarizeValue(resultValue, fallback, 160);
}
