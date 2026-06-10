import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Highlight, type PrismTheme, type Token } from "prism-react-renderer";
import {
  Binary,
  ChevronDown,
  ChevronRight,
  Copy,
  Database,
  File,
  FileCode2,
  FileText,
  Folder,
  FolderOpen,
  Globe2,
  GripVertical,
  Maximize2,
  MessageSquarePlus,
  Minimize2,
  MoreHorizontal,
  PanelRightClose,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  TextCursorInput,
  WrapText,
  X,
} from "lucide-react";
import type { WorkspaceFileEntry, WorkspaceFileReadResult } from "@shared";
import { RuntimeClient } from "../../../../lib/runtimeClient";
import { IconButton } from "../../../v2/components/ui";
import { MarkdownContent } from "./MarkdownContent";

const fileWorkspaceClient = new RuntimeClient();
const WORKSPACE_PREVIEW_MAX_BYTES = 96 * 1024;
const ROOT_DIR = "";
const FILE_TREE_WIDTH_STORAGE_KEY = "session-file-tree-width";
const FILE_TREE_MIN_WIDTH = 280;
const FILE_VIEWER_MIN_WIDTH = 240;
const FILE_BROWSER_RESIZER_WIDTH = 7;
const FILE_TREE_KEYBOARD_STEP = 36;

const filePrismTheme: PrismTheme = {
  plain: {
    backgroundColor: "transparent",
    color: "#25303d",
  },
  styles: [
    { types: ["comment", "prolog", "doctype", "cdata"], style: { color: "#7d8794", fontStyle: "italic" } },
    { types: ["string", "attr-value", "template-string"], style: { color: "#087443" } },
    { types: ["keyword", "selector", "important", "atrule", "tag"], style: { color: "#b42318", fontWeight: "600" } },
    { types: ["function"], style: { color: "#8a4f16" } },
    { types: ["number", "boolean"], style: { color: "#8f482f" } },
    { types: ["operator"], style: { color: "#6b7280" } },
    { types: ["punctuation"], style: { color: "#8b8178" } },
    { types: ["property", "attr-name"], style: { color: "#7a4f12" } },
    { types: ["builtin", "class-name", "constant", "symbol"], style: { color: "#6d4ea3" } },
    { types: ["inserted"], style: { color: "#087443" } },
    { types: ["deleted"], style: { color: "#b42318" } },
  ],
};

type FilePreviewMode = "preview" | "source";
type FilePreviewKind = "file" | "diff";
type FileScopeMode = "all" | "changed";

export interface FileWorkspaceChangeEntry {
  path: string;
  status?: string;
  additions?: number;
  deletions?: number;
  source?: string;
}
interface WorkspacePathTarget {
  path: string;
  lineNumber: number | null;
}

interface FileContextMenuState {
  x: number;
  y: number;
  path: string;
  kind: WorkspaceFileEntry["kind"];
}

interface PreviewTabContextMenuState {
  x: number;
  y: number;
  path: string;
}

export interface FileWorkspaceTextSelection {
  text: string;
  note?: string;
  startLine?: number;
  endLine?: number;
}

interface FileSelectionMenuState extends FileWorkspaceTextSelection {
  x: number;
  y: number;
}

interface FileLineCommentState {
  lineNumber: number;
  draft: string;
}

function canUseTauriInvoke() {
  if (typeof window === "undefined") {
    return false;
  }
  const bridgeWindow = window as typeof window & {
    __TAURI__?: unknown;
    __TAURI_INTERNALS__?: unknown;
  };
  return Boolean(bridgeWindow.__TAURI__ || bridgeWindow.__TAURI_INTERNALS__);
}

function readStoredFileTreeWidth() {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const stored = window.localStorage.getItem(FILE_TREE_WIDTH_STORAGE_KEY);
    const parsed = stored ? Number.parseInt(stored, 10) : Number.NaN;
    return Number.isFinite(parsed) && parsed >= FILE_TREE_MIN_WIDTH ? parsed : null;
  } catch {
    return null;
  }
}

function clampFileTreeWidth(width: number, layoutWidth: number) {
  const maxWidth = Math.max(FILE_TREE_MIN_WIDTH, layoutWidth - FILE_VIEWER_MIN_WIDTH - FILE_BROWSER_RESIZER_WIDTH);
  return Math.round(Math.min(maxWidth, Math.max(FILE_TREE_MIN_WIDTH, width)));
}

function normalizeWorkspaceRelativePath(path: string) {
  return path
    .replace(/\\/g, "/")
    .replace(/^[MADRCU?!]{1,2}\s+/, "")
    .replace(/^"(.+)"$/, "$1")
    .trim();
}

function clampLineNumber(value: string | number | null | undefined) {
  const parsed = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function parseWorkspacePathTarget(path: string): WorkspacePathTarget {
  let normalizedPath = normalizeWorkspaceRelativePath(path);
  let lineNumber: number | null = null;

  const queryLineMatch = normalizedPath.match(/^(.*?)(?:\?|&)line=(\d+)(?:&.*)?$/i);
  if (queryLineMatch) {
    normalizedPath = queryLineMatch[1];
    lineNumber = clampLineNumber(queryLineMatch[2]);
  }

  const hashLineMatch = normalizedPath.match(/^(.*)#(?:L|line-)?(\d+)$/i);
  if (hashLineMatch) {
    normalizedPath = hashLineMatch[1];
    lineNumber = clampLineNumber(hashLineMatch[2]);
  }

  const colonLineMatch = normalizedPath.match(/^(.+):(\d+)(?::\d+)?$/);
  if (colonLineMatch && !/^[A-Za-z]:$/.test(colonLineMatch[1])) {
    normalizedPath = colonLineMatch[1];
    lineNumber = clampLineNumber(colonLineMatch[2]);
  }

  return {
    path: normalizeWorkspaceRelativePath(normalizedPath),
    lineNumber,
  };
}

function parentWorkspacePath(path: string) {
  const normalized = normalizeWorkspaceRelativePath(path);
  const index = normalized.lastIndexOf("/");
  return index > 0 ? normalized.slice(0, index) : ROOT_DIR;
}

function directoryAncestorPaths(path: string) {
  const parent = parentWorkspacePath(path);
  if (!parent || parent === ROOT_DIR) {
    return [];
  }
  const parts = parent.split("/").filter(Boolean);
  return parts.map((_, index) => parts.slice(0, index + 1).join("/"));
}

function fileNameFromPath(path: string) {
  const normalized = normalizeWorkspaceRelativePath(path);
  return normalized.split("/").filter(Boolean).at(-1) || normalized || ".";
}

function workspaceNameFromPath(path?: string) {
  const normalized = String(path ?? "").replace(/\\/g, "/").replace(/\/$/, "");
  return normalized.split("/").filter(Boolean).at(-1) || "workspace";
}

function formatFileSize(bytes?: number | null) {
  if (bytes === undefined || bytes === null) {
    return "";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function isMarkdownPath(path: string) {
  return /\.(md|markdown|mdx)$/i.test(path);
}

function isDatabasePath(path: string) {
  return /\.(db|sqlite|sqlite3)$/i.test(path);
}

function isLikelyTextPath(path: string) {
  return /\.(txt|md|markdown|mdx|json|jsonl|ya?ml|toml|ini|env|py|pyi|ts|tsx|js|jsx|css|scss|html|rs|go|java|kt|swift|c|cc|cpp|h|hpp|sql|sh|ps1|bat)$/i.test(path);
}

function languageFromPath(path: string) {
  const extension = fileNameFromPath(path).split(".").at(-1)?.toLowerCase();
  if (!extension || extension === fileNameFromPath(path).toLowerCase()) {
    return "text";
  }
  const aliases: Record<string, string> = {
    md: "markdown",
    markdown: "markdown",
    py: "python",
    pyi: "python",
    ts: "typescript",
    tsx: "tsx",
    js: "javascript",
    jsx: "jsx",
    rs: "rust",
    ps1: "powershell",
    sh: "bash",
    zsh: "bash",
    html: "markup",
    xml: "markup",
    yml: "yaml",
  };
  return aliases[extension] ?? extension;
}

function fileTypeBadgeLabel(path: string) {
  const name = fileNameFromPath(path);
  const extension = name.split(".").at(-1)?.toLowerCase();
  if (!extension || extension === name.toLowerCase()) {
    return "TXT";
  }
  const aliases: Record<string, string> = {
    css: "CSS",
    gif: "IMG",
    html: "H",
    jpeg: "IMG",
    jpg: "IMG",
    js: "JS",
    json: "{}",
    jsx: "JSX",
    log: "LOG",
    markdown: "MD",
    md: "MD",
    mjs: "MJS",
    png: "IMG",
    py: "PY",
    rs: "RS",
    svg: "SVG",
    ts: "TS",
    tsx: "TSX",
    txt: "TXT",
    yaml: "YML",
    yml: "YML",
  };
  return aliases[extension] ?? extension.slice(0, 3).toUpperCase();
}

function fileTypeBadgeTone(path: string) {
  const label = fileTypeBadgeLabel(path);
  if (["JS", "JSX", "MJS"].includes(label)) return "js";
  if (["TS", "TSX", "CSS"].includes(label)) return "ts";
  if (["IMG", "SVG"].includes(label)) return "media";
  if (label === "{}") return "json";
  if (label === "MD") return "markdown";
  return "text";
}

function compactPath(path: string, maxLength = 86) {
  if (path.length <= maxLength) {
    return path;
  }
  const parts = path.split("/");
  if (parts.length <= 2) {
    return `${path.slice(0, maxLength - 1)}...`;
  }
  const last = parts.at(-1) ?? "";
  const first = parts[0];
  const middle = path.length - first.length - last.length - 6;
  return `${first}/.../${last.length > maxLength - first.length - 6 ? last.slice(Math.max(0, middle)) : last}`;
}

function uniquePaths(paths: string[]) {
  const seen = new Set<string>();
  const result: string[] = [];
  paths.forEach((path) => {
    const normalized = parseWorkspacePathTarget(path).path;
    if (!normalized || seen.has(normalized)) {
      return;
    }
    seen.add(normalized);
    result.push(normalized);
  });
  return result;
}

function normalizeChangeStatus(value?: string) {
  const normalized = value?.trim().toLowerCase();
  if (!normalized) return "";
  if (["added", "created", "create", "new", "a"].includes(normalized)) return "新增";
  if (["deleted", "removed", "remove", "delete", "d"].includes(normalized)) return "删除";
  if (["renamed", "rename", "moved", "r"].includes(normalized)) return "重命名";
  if (["copied", "copy", "c"].includes(normalized)) return "复制";
  return "修改";
}

function normalizeChangeEntries(entries: FileWorkspaceChangeEntry[]) {
  const byPath = new Map<string, FileWorkspaceChangeEntry>();
  entries.forEach((entry) => {
    const path = parseWorkspacePathTarget(entry.path).path;
    if (!path) {
      return;
    }
    const existing = byPath.get(path);
    if (!existing) {
      byPath.set(path, { ...entry, path });
      return;
    }
    byPath.set(path, {
      ...existing,
      status: existing.status || entry.status,
      additions: existing.additions ?? entry.additions,
      deletions: existing.deletions ?? entry.deletions,
      source: existing.source || entry.source,
    });
  });
  return Array.from(byPath.values()).slice(0, 16);
}

function entryMatchesQuery(entry: WorkspaceFileEntry, query: string) {
  if (!query.trim()) {
    return true;
  }
  const normalizedQuery = query.trim().toLowerCase();
  return entry.name.toLowerCase().includes(normalizedQuery) || entry.path.toLowerCase().includes(normalizedQuery);
}

function breadcrumbParts(path: string) {
  return normalizeWorkspaceRelativePath(path).split("/").filter(Boolean);
}

function workspaceAbsolutePath(workspaceRoot?: string, path?: string) {
  const normalizedPath = normalizeWorkspaceRelativePath(path ?? "");
  if (!workspaceRoot) {
    return normalizedPath;
  }
  if (!normalizedPath) {
    return workspaceRoot;
  }
  const separator = workspaceRoot.includes("\\") ? "\\" : "/";
  return `${workspaceRoot.replace(/[\\/]+$/, "")}${separator}${normalizedPath.replace(/\//g, separator)}`;
}

function elementForNode(node: Node | null) {
  if (!node) return null;
  return node.nodeType === Node.ELEMENT_NODE ? node as Element : node.parentElement;
}

function lineNumberForNode(node: Node | null, root: HTMLElement) {
  const element = elementForNode(node);
  const row = element?.closest("[data-line-number]");
  if (!row || !root.contains(row)) return undefined;
  const lineNumber = Number.parseInt(row.getAttribute("data-line-number") ?? "", 10);
  return Number.isFinite(lineNumber) && lineNumber > 0 ? lineNumber : undefined;
}

function lineRangeForText(content: string, text: string) {
  const normalizedContent = content.replace(/\r\n/g, "\n");
  const normalizedText = text.replace(/\r\n/g, "\n");
  const index = normalizedContent.indexOf(normalizedText);
  if (index < 0) return {};
  const startLine = normalizedContent.slice(0, index).split("\n").length;
  const endLine = startLine + normalizedText.split("\n").length - 1;
  return { startLine, endLine };
}

function selectionMenuPosition(range: Range, root: HTMLElement, pointer: { clientX: number; clientY: number }) {
  const rangeRect = typeof range.getBoundingClientRect === "function"
    ? range.getBoundingClientRect()
    : ({ left: pointer.clientX, right: pointer.clientX, top: pointer.clientY, bottom: pointer.clientY, width: 0, height: 0 } as DOMRect);
  const rootRect = root.getBoundingClientRect();
  const baseX = rangeRect.width || rangeRect.height ? rangeRect.left + rangeRect.width / 2 : pointer.clientX || rootRect.left + rootRect.width / 2;
  const baseY = rangeRect.width || rangeRect.height ? rangeRect.bottom : pointer.clientY || rootRect.top + 40;
  const windowWidth = typeof window === "undefined" ? rootRect.right : window.innerWidth;
  const windowHeight = typeof window === "undefined" ? rootRect.bottom : window.innerHeight;
  return {
    x: Math.max(8, Math.min(Math.round(baseX - 82), windowWidth - 180)),
    y: Math.max(8, Math.min(Math.round(baseY + 10), windowHeight - 56)),
  };
}

function clearWindowSelection() {
  window.getSelection()?.removeAllRanges();
}

function FileIcon({ kind, path, expanded = false }: { kind: WorkspaceFileEntry["kind"]; path?: string; expanded?: boolean }) {
  const icon =
    kind === "directory" ? (
      expanded ? (
        <FolderOpen size={14} strokeWidth={1.9} />
      ) : (
        <Folder size={14} strokeWidth={1.9} />
      )
    ) : isDatabasePath(path || "") ? (
      <Database size={14} strokeWidth={1.9} />
    ) : isMarkdownPath(path || "") ? (
      <FileText size={14} strokeWidth={1.9} />
    ) : isLikelyTextPath(path || "") ? (
      <FileCode2 size={14} strokeWidth={1.9} />
    ) : (
      <File size={14} strokeWidth={1.9} />
    );
  const tone = kind === "file" && isDatabasePath(path || "") ? "database" : undefined;

  return (
    <span className="session-file-icon" data-kind={kind} data-tone={tone} aria-hidden="true">
      {icon}
    </span>
  );
}

function FileTypeBadge({ path, subtle = false }: { path: string; subtle?: boolean }) {
  return (
    <span
      className="session-file-type-badge"
      data-subtle={subtle ? "true" : undefined}
      data-tone={fileTypeBadgeTone(path)}
      aria-hidden="true"
    >
      {fileTypeBadgeLabel(path)}
    </span>
  );
}

function tokenClassName(token: Token) {
  if (token.types.some((type) => ["comment", "prolog", "doctype", "cdata"].includes(type))) {
    return "session-file-syntax-comment";
  }
  if (token.types.some((type) => ["string", "attr-value", "template-string"].includes(type))) {
    return "session-file-syntax-string";
  }
  if (token.types.some((type) => ["keyword", "selector", "important", "atrule", "tag"].includes(type))) {
    return "session-file-syntax-keyword";
  }
  if (token.types.some((type) => ["number", "boolean"].includes(type))) {
    return "session-file-syntax-number";
  }
  if (token.types.includes("operator")) {
    return "session-file-syntax-operator";
  }
  if (token.types.some((type) => ["property", "attr-name"].includes(type))) {
    return "session-file-syntax-property";
  }
  if (token.types.some((type) => ["builtin", "class-name", "constant", "symbol", "function"].includes(type))) {
    return "session-file-syntax-builtin";
  }
  return "";
}

function CodePreview({
  activeLine,
  commentDraft,
  commentLine,
  content,
  onCancelLineComment,
  onCommentDraftChange,
  onLineCommentClick,
  onSubmitLineComment,
  onSelection,
  path,
  wrapLines = false,
}: {
  activeLine?: number | null;
  commentDraft?: string;
  commentLine?: number | null;
  content: string;
  onCancelLineComment?: () => void;
  onCommentDraftChange?: (value: string) => void;
  onLineCommentClick?: (lineNumber: number) => void;
  onSubmitLineComment?: () => void;
  onSelection?: (selection: FileSelectionMenuState | null) => void;
  path: string;
  wrapLines?: boolean;
}) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const language = languageFromPath(path);
  const isDiff = language === "diff";
  const diffLineType = (line: string) => {
    if (!isDiff) return undefined;
    if (line.startsWith("+") && !line.startsWith("+++")) return "add";
    if (line.startsWith("-") && !line.startsWith("---")) return "remove";
    if (line.startsWith("@@") || line.startsWith("diff --git") || line.startsWith("---") || line.startsWith("+++")) return "header";
    return undefined;
  };
  return (
    <Highlight theme={filePrismTheme} code={content.replace(/\r\n/g, "\n")} language={language}>
      {({ tokens, getTokenProps }) => (
        <ol
          className="session-file-code-lines"
          data-language={language}
          data-wrap={wrapLines}
          onMouseUp={(event) => {
            if (!onSelection) return;
            const selection = window.getSelection();
            if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
              onSelection(null);
              return;
            }
            const range = selection.getRangeAt(0);
            const root = event.currentTarget;
            const startElement = elementForNode(range.startContainer);
            const endElement = elementForNode(range.endContainer);
            if (!startElement || !endElement || !root.contains(startElement) || !root.contains(endElement)) {
              onSelection(null);
              return;
            }
            const text = selection.toString().trim();
            if (!text) {
              onSelection(null);
              return;
            }
            const startLine = lineNumberForNode(range.startContainer, root);
            const endLine = lineNumberForNode(range.endContainer, root) ?? startLine;
            const orderedStart = startLine && endLine ? Math.min(startLine, endLine) : startLine;
            const orderedEnd = startLine && endLine ? Math.max(startLine, endLine) : endLine;
            onSelection({
              text: orderedStart && orderedEnd && orderedStart !== orderedEnd ? lines.slice(orderedStart - 1, orderedEnd).join("\n").trim() : text,
              ...(orderedStart ? { startLine: orderedStart } : {}),
              ...(orderedEnd ? { endLine: orderedEnd } : {}),
              ...selectionMenuPosition(range, root, event),
            });
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") onSelection?.(null);
          }}
          tabIndex={-1}
        >
          {lines.map((line, index) => {
            const prismLine = tokens[index] ?? [];
            const lineNumber = index + 1;
            return (
              <Fragment key={`${index}-${line.slice(0, 16)}`}>
                <li
                  data-active-line={activeLine === lineNumber ? "true" : undefined}
                  data-diff-line={diffLineType(line)}
                  data-line-number={lineNumber}
                >
                  <button
                    aria-label={`评论第 ${lineNumber} 行`}
                    className="session-file-line-number"
                    type="button"
                    onClick={() => onLineCommentClick?.(lineNumber)}
                  >
                    {lineNumber}
                  </button>
                  <span className="session-file-line-text">
                    {prismLine.length === 1 && prismLine[0]?.empty
                      ? " "
                      : prismLine.map((token, tokenIndex) => {
                          const { key: tokenKey, className, ...tokenProps } = getTokenProps({ token, key: tokenIndex });
                          const semanticClassName = tokenClassName(token);
                          return (
                            <span
                              key={String(tokenKey)}
                              {...tokenProps}
                              className={[className, semanticClassName].filter(Boolean).join(" ")}
                            />
                          );
                        })}
                  </span>
                </li>
                {commentLine === lineNumber ? (
                  <li className="session-file-line-comment" data-line-comment={lineNumber}>
                    <span aria-hidden="true" />
                    <div>
                      <textarea
                        aria-label={`第 ${lineNumber} 行备注`}
                        autoFocus
                        rows={3}
                        value={commentDraft ?? ""}
                        placeholder="给这行写一句备注..."
                        onChange={(event) => onCommentDraftChange?.(event.currentTarget.value)}
                      />
                      <footer>
                        <button type="button" onClick={onCancelLineComment}>取消</button>
                        <button
                          aria-label={`添加第 ${lineNumber} 行备注到聊天`}
                          type="button"
                          disabled={!commentDraft?.trim()}
                          onClick={onSubmitLineComment}
                        >
                          添加到聊天
                        </button>
                      </footer>
                    </div>
                  </li>
                ) : null}
              </Fragment>
            );
          })}
        </ol>
      )}
    </Highlight>
  );
}

interface FileTreeRowsProps {
  entries: WorkspaceFileEntry[];
  childrenByPath: Record<string, WorkspaceFileEntry[]>;
  depth?: number;
  expanded: Set<string>;
  loadingPath: string | null;
  query: string;
  selectedPath: string | null;
  onOpenDirectory: (path: string) => void;
  onOpenFile: (path: string) => void;
  onContextMenu: (event: ReactMouseEvent<HTMLElement>, target: { path: string; kind: WorkspaceFileEntry["kind"] }) => void;
}

function FileTreeRows({
  entries,
  childrenByPath,
  depth = 0,
  expanded,
  loadingPath,
  query,
  selectedPath,
  onOpenDirectory,
  onOpenFile,
  onContextMenu,
}: FileTreeRowsProps) {
  const visibleEntries = entries.filter((entry) => {
    if (entryMatchesQuery(entry, query)) {
      return true;
    }
    if (entry.kind !== "directory") {
      return false;
    }
    const children = childrenByPath[entry.path] ?? [];
    return children.some((child) => entryMatchesQuery(child, query));
  });

  return (
    <ul className="session-file-tree-list" role={depth === 0 ? "tree" : "group"}>
      {visibleEntries.map((entry) => {
        const isDirectory = entry.kind === "directory";
        const isExpanded = expanded.has(entry.path);
        const isSelected = selectedPath === entry.path;
        const children = childrenByPath[entry.path] ?? [];
        const rowStyle = { "--file-depth": depth } as CSSProperties;
        return (
          <li key={`${entry.kind}:${entry.path}`} role="none">
            <button
              className="session-file-tree-row"
              data-kind={entry.kind}
              data-selected={isSelected}
              aria-expanded={isDirectory ? isExpanded : undefined}
              role="treeitem"
              style={rowStyle}
              type="button"
              onClick={() => {
                if (isDirectory) {
                  onOpenDirectory(entry.path);
                } else {
                  onOpenFile(entry.path);
                }
              }}
              onContextMenu={(event) => onContextMenu(event, { path: entry.path, kind: entry.kind })}
            >
              <span className="session-file-disclosure" aria-hidden="true">
                {isDirectory ? (isExpanded ? <ChevronDown size={13} strokeWidth={2} /> : <ChevronRight size={13} strokeWidth={2} />) : null}
              </span>
              {isDirectory ? (
                <FileIcon kind={entry.kind} path={entry.path} expanded={isExpanded} />
              ) : (
                <FileTypeBadge path={entry.path} subtle={!isSelected} />
              )}
              <span className="session-file-tree-name">{entry.name}</span>
              <small>{isDirectory ? (loadingPath === entry.path ? "加载中" : "") : formatFileSize(entry.size)}</small>
            </button>
            {isDirectory && isExpanded && children.length ? (
              <FileTreeRows
                entries={children}
                childrenByPath={childrenByPath}
                depth={depth + 1}
                expanded={expanded}
                loadingPath={loadingPath}
                query={query}
                selectedPath={selectedPath}
                onOpenDirectory={onOpenDirectory}
                onOpenFile={onOpenFile}
                onContextMenu={onContextMenu}
              />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

interface FileWorkspacePanelProps {
  workspaceRoot?: string;
  workspaceLabel?: string;
  relatedFiles?: string[];
  changedFiles?: FileWorkspaceChangeEntry[];
  activeFilePath?: string | null;
  activeFileRequestKey?: number;
  focused?: boolean;
  onToggleFocus?: () => void;
  onOpenExternalFile?: (absolutePath: string, lineNumber?: number | null) => void | Promise<void>;
  onAddFileToChat?: (path: string) => void;
  onAddSelectionToChat?: (path: string, selection: FileWorkspaceTextSelection) => void;
  onPreviewStateChange?: (hasPreviewTabs: boolean) => void;
  onClose?: () => void;
}

export function FileWorkspacePanel({
  workspaceRoot,
  workspaceLabel,
  relatedFiles = [],
  changedFiles = [],
  activeFilePath = null,
  activeFileRequestKey = 0,
  focused = false,
  onToggleFocus,
  onOpenExternalFile,
  onAddFileToChat,
  onAddSelectionToChat,
  onPreviewStateChange,
  onClose,
}: FileWorkspacePanelProps) {
  const canBrowseFiles = canUseTauriInvoke();
  const normalizedRelatedFiles = useMemo(() => uniquePaths(relatedFiles), [relatedFiles]);
  const normalizedChangedFiles = useMemo(() => normalizeChangeEntries(changedFiles), [changedFiles]);
  const activeFileTarget = useMemo(() => parseWorkspacePathTarget(activeFilePath ?? ""), [activeFilePath]);
  const normalizedActiveFilePath = activeFileTarget.path;
  const activeFileLineNumber = activeFileTarget.lineNumber;
  const [childrenByPath, setChildrenByPath] = useState<Record<string, WorkspaceFileEntry[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([ROOT_DIR]));
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [openFiles, setOpenFiles] = useState<string[]>([]);
  const [openFileKinds, setOpenFileKinds] = useState<Record<string, FilePreviewKind>>({});
  const [filePreview, setFilePreview] = useState<WorkspaceFileReadResult | null>(null);
  const [filePreviewKind, setFilePreviewKind] = useState<FilePreviewKind>("file");
  const [query, setQuery] = useState("");
  const [fileScope, setFileScope] = useState<FileScopeMode>("all");
  const [workspaceSearchResults, setWorkspaceSearchResults] = useState<WorkspaceFileEntry[]>([]);
  const [workspaceSearchLoading, setWorkspaceSearchLoading] = useState(false);
  const [workspaceSearchTruncated, setWorkspaceSearchTruncated] = useState(false);
  const [loadingPath, setLoadingPath] = useState<string | null>(null);
  const [readingPath, setReadingPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wrapLines, setWrapLines] = useState(false);
  const [previewMode, setPreviewMode] = useState<FilePreviewMode>("preview");
  const [copiedPath, setCopiedPath] = useState(false);
  const [highlightedLine, setHighlightedLine] = useState<number | null>(null);
  const [fileTreeWidthPx, setFileTreeWidthPx] = useState<number | null>(() => readStoredFileTreeWidth());
  const [fileTreeResizing, setFileTreeResizing] = useState(false);
  const [contextMenu, setContextMenu] = useState<FileContextMenuState | null>(null);
  const [previewTabContextMenu, setPreviewTabContextMenu] = useState<PreviewTabContextMenuState | null>(null);
  const [selectionMenu, setSelectionMenu] = useState<FileSelectionMenuState | null>(null);
  const [lineComment, setLineComment] = useState<FileLineCommentState | null>(null);
  const [chatAddedPath, setChatAddedPath] = useState<string | null>(null);
  const externalOpenDoneRef = useRef("");
  const workspaceSearchRequestRef = useRef(0);
  const fileBrowserLayoutRef = useRef<HTMLDivElement | null>(null);
  const fileScopeMenuRef = useRef<HTMLDetailsElement | null>(null);
  const rootEntries = childrenByPath[ROOT_DIR] ?? [];
  const rootName = workspaceNameFromPath(workspaceRoot || workspaceLabel);
  const previewPath = selectedFile ?? filePreview?.path ?? "";
  const trimmedQuery = query.trim();
  const previewParent = previewPath ? parentWorkspacePath(previewPath) : ROOT_DIR;
  const currentAbsolutePath = workspaceAbsolutePath(workspaceRoot, previewPath || previewParent);
  const markdownPreviewAvailable = Boolean(filePreviewKind === "file" && filePreview?.path && isMarkdownPath(filePreview.path) && !filePreview.binary);
  const topTabs = useMemo(() => {
    const baseTabs = rootEntries.length ? rootEntries.filter((entry) => entry.kind === "file").slice(0, 4) : [];
    return uniquePaths([
      ...baseTabs.map((entry) => entry.path),
      ...normalizedRelatedFiles.slice(0, 4),
    ]).slice(0, 4);
  }, [normalizedRelatedFiles, rootEntries]);

  const loadDirectory = useCallback(
    async (path: string) => {
      if (!workspaceRoot || !canBrowseFiles) {
        return;
      }
      const normalizedPath = normalizeWorkspaceRelativePath(path);
      setLoadingPath(normalizedPath || ROOT_DIR);
      setError(null);
      try {
        const result = await fileWorkspaceClient.workspaceFileList({
          workspaceRoot,
          path: normalizedPath,
          maxEntries: 300,
        });
        const resultPath = normalizeWorkspaceRelativePath(result.path);
        setChildrenByPath((current) => ({
          ...current,
          [resultPath || ROOT_DIR]: result.entries,
        }));
        setExpanded((current) => {
          const next = new Set(current);
          next.add(resultPath || ROOT_DIR);
          return next;
        });
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setLoadingPath(null);
      }
    },
    [canBrowseFiles, workspaceRoot],
  );

  const openDirectory = useCallback(
    (path: string) => {
      const normalizedPath = normalizeWorkspaceRelativePath(path);
      setExpanded((current) => {
        const next = new Set(current);
        if (next.has(normalizedPath)) {
          next.delete(normalizedPath);
        } else {
          next.add(normalizedPath);
        }
        return next;
      });
      if (!childrenByPath[normalizedPath]) {
        void loadDirectory(normalizedPath);
      }
    },
    [childrenByPath, loadDirectory],
  );

  const openFile = useCallback(
    async (path: string, options?: { lineNumber?: number | null }) => {
      if (!workspaceRoot || !canBrowseFiles) {
        return;
      }
      const normalizedPath = normalizeWorkspaceRelativePath(path);
      const nextLineNumber = options?.lineNumber ?? null;
      setReadingPath(normalizedPath);
      setSelectedFile(normalizedPath);
      setHighlightedLine(nextLineNumber);
      setFilePreviewKind("file");
      if (isMarkdownPath(normalizedPath)) {
        setPreviewMode(nextLineNumber ? "source" : "preview");
      }
      setOpenFiles((current) => (current.includes(normalizedPath) ? current : [...current, normalizedPath].slice(-8)));
      setOpenFileKinds((current) => ({ ...current, [normalizedPath]: "file" }));
      setError(null);
      try {
        const result = await fileWorkspaceClient.workspaceFileRead({
          workspaceRoot,
          path: normalizedPath,
          maxBytes: WORKSPACE_PREVIEW_MAX_BYTES,
        });
        setFilePreview(result);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setReadingPath(null);
      }
    },
    [canBrowseFiles, workspaceRoot],
  );

  const openChangedFile = useCallback(
    async (path: string) => {
      if (!workspaceRoot || !canBrowseFiles) {
        return;
      }
      const normalizedPath = normalizeWorkspaceRelativePath(path);
      setReadingPath(normalizedPath);
      setSelectedFile(normalizedPath);
      setHighlightedLine(null);
      setPreviewMode("source");
      setOpenFiles((current) => (current.includes(normalizedPath) ? current : [...current, normalizedPath].slice(-8)));
      setOpenFileKinds((current) => ({ ...current, [normalizedPath]: "diff" }));
      setError(null);
      try {
        const result = await fileWorkspaceClient.gitLocalDiff({
          cwd: workspaceRoot,
          path: normalizedPath,
        });
        const diff = result.diff || "";
        if (diff.trim()) {
          setFilePreview({
            rootPath: result.repoRoot || workspaceRoot,
            path: normalizedPath,
            content: diff,
            bytes: diff.length,
            truncated: Boolean(result.truncated),
            binary: false,
            encoding: "utf-8",
          });
          setFilePreviewKind("diff");
          return;
        }
        setFilePreviewKind("file");
        setOpenFileKinds((current) => ({ ...current, [normalizedPath]: "file" }));
        await openFile(normalizedPath);
      } catch (reason) {
        setFilePreviewKind("file");
        setOpenFileKinds((current) => ({ ...current, [normalizedPath]: "file" }));
        try {
          await openFile(normalizedPath);
        } catch {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        setReadingPath(null);
      }
    },
    [canBrowseFiles, openFile, workspaceRoot],
  );

  const openLocatedEntry = useCallback(
    (entry: WorkspaceFileEntry) => {
      const normalizedPath = normalizeWorkspaceRelativePath(entry.path);
      if (!normalizedPath) {
        return;
      }
      directoryAncestorPaths(normalizedPath).forEach((path) => {
        if (!childrenByPath[path]) {
          void loadDirectory(path);
        }
      });
      if (entry.kind === "directory") {
        setExpanded((current) => {
          const next = new Set(current);
          next.add(normalizedPath);
          return next;
        });
        if (!childrenByPath[normalizedPath]) {
          void loadDirectory(normalizedPath);
        }
        return;
      }
      void openFile(normalizedPath);
    },
    [childrenByPath, loadDirectory, openFile],
  );

  const copyCurrentPath = useCallback(async () => {
    if (!currentAbsolutePath || typeof navigator === "undefined" || typeof navigator.clipboard?.writeText !== "function") {
      return;
    }
    await navigator.clipboard.writeText(currentAbsolutePath);
    setCopiedPath(true);
  }, [currentAbsolutePath]);

  const copyText = useCallback(async (text: string) => {
    if (!text || typeof navigator === "undefined" || typeof navigator.clipboard?.writeText !== "function") {
      return;
    }
    await navigator.clipboard.writeText(text);
    setCopiedPath(true);
  }, []);

  const copyRelativePath = useCallback(async (path: string) => {
    await copyText(normalizeWorkspaceRelativePath(path));
  }, [copyText]);

  const copyWorkspacePath = useCallback(async (path: string) => {
    const absolutePath = workspaceAbsolutePath(workspaceRoot, path);
    await copyText(absolutePath);
  }, [copyText, workspaceRoot]);

  const openContainingFolder = useCallback((path: string, kind: WorkspaceFileEntry["kind"]) => {
    const targetPath = kind === "directory" ? path : parentWorkspacePath(path);
    const absolutePath = workspaceAbsolutePath(workspaceRoot, targetPath);
    if (!absolutePath || !onOpenExternalFile) return;
    void onOpenExternalFile(absolutePath);
  }, [onOpenExternalFile, workspaceRoot]);

  const openExternalFile = useCallback(() => {
    if (!currentAbsolutePath || !onOpenExternalFile) return;
    if (activeFileTarget.lineNumber != null) {
      void onOpenExternalFile(currentAbsolutePath, activeFileTarget.lineNumber);
    } else {
      void onOpenExternalFile(currentAbsolutePath);
    }
  }, [currentAbsolutePath, onOpenExternalFile, activeFileTarget.lineNumber]);

  const openExternalPath = useCallback((path: string) => {
    const target = parseWorkspacePathTarget(path);
    const absolutePath = workspaceAbsolutePath(workspaceRoot, target.path);
    if (!absolutePath || !onOpenExternalFile) return;
    if (target.lineNumber != null) {
      void onOpenExternalFile(absolutePath, target.lineNumber);
    } else {
      void onOpenExternalFile(absolutePath);
    }
  }, [onOpenExternalFile, workspaceRoot]);

  const addPathToChat = useCallback((path: string) => {
    const normalizedPath = normalizeWorkspaceRelativePath(path);
    if (!normalizedPath || !onAddFileToChat) {
      return;
    }
    onAddFileToChat(normalizedPath);
    setChatAddedPath(normalizedPath);
  }, [onAddFileToChat]);

  const openPreviewTab = useCallback((path: string) => {
    const kind = openFileKinds[path] ?? "file";
    if (kind === "diff") {
      void openChangedFile(path);
      return;
    }
    void openFile(path);
  }, [openChangedFile, openFile, openFileKinds]);

  const closeOpenFiles = useCallback((paths: string[]) => {
    const normalizedTargets = new Set(paths.map((path) => normalizeWorkspaceRelativePath(path)).filter(Boolean));
    if (!normalizedTargets.size) {
      return;
    }
    const nextOpenFiles = openFiles.filter((item) => !normalizedTargets.has(item));
    setOpenFiles(nextOpenFiles);
    setOpenFileKinds((current) => {
      const next = { ...current };
      normalizedTargets.forEach((path) => {
        delete next[path];
      });
      return next;
    });
    setPreviewTabContextMenu(null);
    if (!selectedFile || !normalizedTargets.has(selectedFile)) {
      return;
    }
    const nextPath = nextOpenFiles.at(-1) ?? null;
    if (nextPath) {
      openPreviewTab(nextPath);
      return;
    }
    setSelectedFile(null);
    setFilePreview(null);
    setFilePreviewKind("file");
    setHighlightedLine(null);
  }, [openFiles, openPreviewTab, selectedFile]);

  const closeOpenFile = useCallback((path: string) => {
    closeOpenFiles([path]);
  }, [closeOpenFiles]);

  const selectFileScope = useCallback((scope: FileScopeMode) => {
    setFileScope(scope);
    fileScopeMenuRef.current?.removeAttribute("open");
    setContextMenu(null);
  }, []);

  const openContextMenu = useCallback((
    event: ReactMouseEvent<HTMLElement>,
    target: { path: string; kind: WorkspaceFileEntry["kind"] },
  ) => {
    event.preventDefault();
    event.stopPropagation();
    const normalizedPath = normalizeWorkspaceRelativePath(target.path);
    if (!normalizedPath) {
      return;
    }
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      path: normalizedPath,
      kind: target.kind,
    });
  }, []);

  const startFileTreeResize = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const layout = event.currentTarget.closest(".session-file-browser-layout") as HTMLElement | null;
      const treePane = layout?.querySelector(".session-file-tree-pane") as HTMLElement | null;
      const layoutWidth = layout?.getBoundingClientRect().width ?? 0;
      if (!layout || layoutWidth <= 0) {
        return;
      }
      const startWidth = fileTreeWidthPx ?? treePane?.getBoundingClientRect().width ?? layoutWidth * 0.48;
      const startX = event.clientX;
      event.preventDefault();
      event.currentTarget.setPointerCapture?.(event.pointerId);
      setFileTreeResizing(true);

      const onMove = (moveEvent: PointerEvent) => {
        const nextWidth = startWidth - (moveEvent.clientX - startX);
        setFileTreeWidthPx(clampFileTreeWidth(nextWidth, layoutWidth));
      };
      const onUp = () => {
        setFileTreeResizing(false);
        window.removeEventListener("pointermove", onMove);
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp, { once: true });
    },
    [fileTreeWidthPx],
  );

  const handleFileTreeResizeKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        return;
      }
      const layout = event.currentTarget.closest(".session-file-browser-layout") as HTMLElement | null;
      const treePane = layout?.querySelector(".session-file-tree-pane") as HTMLElement | null;
      const layoutWidth = layout?.getBoundingClientRect().width ?? 0;
      if (!layout || layoutWidth <= 0) {
        return;
      }
      event.preventDefault();
      const currentWidth = fileTreeWidthPx ?? treePane?.getBoundingClientRect().width ?? layoutWidth * 0.48;
      const maxWidth = layoutWidth - FILE_VIEWER_MIN_WIDTH - FILE_BROWSER_RESIZER_WIDTH;
      const nextWidth =
        event.key === "ArrowLeft"
          ? currentWidth + FILE_TREE_KEYBOARD_STEP
          : event.key === "ArrowRight"
            ? currentWidth - FILE_TREE_KEYBOARD_STEP
            : event.key === "End"
              ? maxWidth
              : FILE_TREE_MIN_WIDTH;
      setFileTreeWidthPx(clampFileTreeWidth(nextWidth, layoutWidth));
    },
    [fileTreeWidthPx],
  );

  const addCurrentSelectionToChat = useCallback(() => {
    if (!selectionMenu || !previewPath || !onAddSelectionToChat) {
      return;
    }
    onAddSelectionToChat(previewPath, {
      text: selectionMenu.text,
      startLine: selectionMenu.startLine,
      endLine: selectionMenu.endLine,
    });
    setSelectionMenu(null);
    clearWindowSelection();
  }, [onAddSelectionToChat, previewPath, selectionMenu]);

  const submitLineCommentToChat = useCallback(() => {
    if (!lineComment || !previewPath || !filePreview?.content || !onAddSelectionToChat) {
      return;
    }
    const lines = filePreview.content.replace(/\r\n/g, "\n").split("\n");
    const quote = lines[lineComment.lineNumber - 1] ?? "";
    const note = lineComment.draft.trim();
    if (!note) {
      return;
    }
    onAddSelectionToChat(previewPath, {
      text: quote,
      note,
      startLine: lineComment.lineNumber,
      endLine: lineComment.lineNumber,
    });
    setLineComment(null);
  }, [filePreview?.content, lineComment, onAddSelectionToChat, previewPath]);

  useEffect(() => {
    setChildrenByPath({});
    setExpanded(new Set([ROOT_DIR]));
    setSelectedFile(null);
    setOpenFiles([]);
    setOpenFileKinds({});
    setFilePreview(null);
    setFilePreviewKind("file");
    setError(null);
    setQuery("");
    setWorkspaceSearchResults([]);
    setWorkspaceSearchLoading(false);
    setWorkspaceSearchTruncated(false);
    setHighlightedLine(null);
    setSelectionMenu(null);
    setLineComment(null);
    externalOpenDoneRef.current = "";
  }, [workspaceRoot]);

  useEffect(() => {
    setCopiedPath(false);
    setSelectionMenu(null);
    setLineComment(null);
  }, [currentAbsolutePath]);

  useEffect(() => {
    if (fileScope === "changed" && !normalizedChangedFiles.length) {
      setFileScope("all");
    }
  }, [fileScope, normalizedChangedFiles.length]);

  useEffect(() => {
    if (!contextMenu && !previewTabContextMenu) {
      return undefined;
    }
    const close = () => {
      setContextMenu(null);
      setPreviewTabContextMenu(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        close();
      }
    };
    window.addEventListener("click", close);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("scroll", close, true);
    };
  }, [contextMenu, previewTabContextMenu]);

  useEffect(() => {
    if (!highlightedLine || !filePreview?.path || filePreview.binary || previewMode !== "source" && isMarkdownPath(filePreview.path)) {
      return undefined;
    }
    const frame = window.requestAnimationFrame?.(() => {
      const viewer = fileBrowserLayoutRef.current?.querySelector(".session-file-viewer") as HTMLElement | null;
      const target = viewer?.querySelector(`[data-line-number="${highlightedLine}"]`) as HTMLElement | null;
      if (!viewer || !target) {
        return;
      }
      const viewerRect = viewer.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      viewer.scrollTop += targetRect.top - viewerRect.top - 64;
    });
    return () => {
      if (typeof frame === "number") {
        window.cancelAnimationFrame?.(frame);
      }
    };
  }, [filePreview?.binary, filePreview?.content, filePreview?.path, highlightedLine, previewMode]);

  useEffect(() => {
    if (typeof window === "undefined" || fileTreeWidthPx === null) {
      return;
    }
    try {
      window.localStorage.setItem(FILE_TREE_WIDTH_STORAGE_KEY, String(fileTreeWidthPx));
    } catch {
      // Persisting the splitter is a convenience; browsing still works without storage.
    }
  }, [fileTreeWidthPx]);

  useEffect(() => {
    const layout = fileBrowserLayoutRef.current;
    if (!layout || typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(([entry]) => {
      const layoutWidth = entry?.contentRect.width ?? 0;
      if (layoutWidth <= 0) {
        return;
      }
      setFileTreeWidthPx((current) => (current === null ? current : clampFileTreeWidth(current, layoutWidth)));
    });
    observer.observe(layout);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!workspaceRoot || !canBrowseFiles) {
      return;
    }
    void loadDirectory(ROOT_DIR);
  }, [canBrowseFiles, loadDirectory, workspaceRoot]);

  useEffect(() => {
    const requestId = workspaceSearchRequestRef.current + 1;
    workspaceSearchRequestRef.current = requestId;
    if (!workspaceRoot || !canBrowseFiles || trimmedQuery.length < 2) {
      setWorkspaceSearchResults([]);
      setWorkspaceSearchLoading(false);
      setWorkspaceSearchTruncated(false);
      return undefined;
    }

    let cancelled = false;
    setWorkspaceSearchLoading(true);
    setWorkspaceSearchTruncated(false);
    const timer = window.setTimeout(() => {
      fileWorkspaceClient
        .workspaceFileSearch({
          workspaceRoot,
          query: trimmedQuery,
          maxEntries: 18,
        })
        .then((result) => {
          if (cancelled || workspaceSearchRequestRef.current !== requestId) {
            return;
          }
          setWorkspaceSearchResults(result.entries);
          setWorkspaceSearchTruncated(Boolean(result.truncated));
        })
        .catch(() => {
          if (cancelled || workspaceSearchRequestRef.current !== requestId) {
            return;
          }
          setWorkspaceSearchResults([]);
          setWorkspaceSearchTruncated(false);
        })
        .finally(() => {
          if (cancelled || workspaceSearchRequestRef.current !== requestId) {
            return;
          }
          setWorkspaceSearchLoading(false);
        });
    }, 150);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [canBrowseFiles, trimmedQuery, workspaceRoot]);

  useEffect(() => {
    if (!normalizedActiveFilePath || !workspaceRoot || !canBrowseFiles) {
      return;
    }
    const requestKey = `${workspaceRoot}\n${normalizedActiveFilePath}\n${activeFileLineNumber ?? ""}\n${activeFileRequestKey}`;
    if (externalOpenDoneRef.current === requestKey) {
      return;
    }
    externalOpenDoneRef.current = requestKey;
    void openFile(normalizedActiveFilePath, { lineNumber: activeFileLineNumber });
    directoryAncestorPaths(normalizedActiveFilePath).forEach((path) => {
      if (!childrenByPath[path]) {
        void loadDirectory(path);
      }
    });
  }, [activeFileLineNumber, activeFileRequestKey, canBrowseFiles, childrenByPath, loadDirectory, normalizedActiveFilePath, openFile, workspaceRoot]);

  const breadcrumbs = breadcrumbParts(previewPath || previewParent);
  const fileBrowserClassName = [
    "session-file-browser-layout",
    fileTreeResizing ? "session-file-browser-layout-resizing" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const fileBrowserStyle = fileTreeWidthPx
    ? ({ "--session-file-tree-width": `${fileTreeWidthPx}px` } as CSSProperties)
    : undefined;
  const contextMenuStyle = contextMenu
    ? ({
        left: Math.max(8, Math.min(contextMenu.x, (typeof window === "undefined" ? contextMenu.x : window.innerWidth) - 232)),
        top: Math.max(8, Math.min(contextMenu.y, (typeof window === "undefined" ? contextMenu.y : window.innerHeight) - 210)),
      } as CSSProperties)
    : undefined;
  const previewTabContextMenuStyle = previewTabContextMenu
    ? ({
        left: Math.max(8, Math.min(previewTabContextMenu.x, (typeof window === "undefined" ? previewTabContextMenu.x : window.innerWidth) - 180)),
        top: Math.max(8, Math.min(previewTabContextMenu.y, (typeof window === "undefined" ? previewTabContextMenu.y : window.innerHeight) - 190)),
      } as CSSProperties)
    : undefined;
  const previewTabIndex = previewTabContextMenu ? openFiles.indexOf(previewTabContextMenu.path) : -1;
  const previewTabsToLeft = previewTabIndex > 0 ? openFiles.slice(0, previewTabIndex) : [];
  const previewTabsToRight = previewTabIndex >= 0 ? openFiles.slice(previewTabIndex + 1) : [];
  const fileScopeLabel = fileScope === "changed" ? "已更改文件" : "所有文件";
  const hasPreviewTabs = openFiles.length > 0;

  useEffect(() => {
    onPreviewStateChange?.(hasPreviewTabs);
  }, [hasPreviewTabs, onPreviewStateChange]);

  return (
    <section className="session-file-workspace" data-has-preview={hasPreviewTabs ? "true" : "false"} aria-label="文件浏览器">
      <header className="session-file-modebar">
        <div className="session-file-mode-tabs" role="tablist" aria-label="工作区模式">
          <button aria-selected="true" role="tab" type="button">
            <Folder size={16} strokeWidth={2} aria-hidden="true" />
            文件
          </button>
          <button aria-selected="false" disabled role="tab" type="button">
            <Globe2 size={16} strokeWidth={2} aria-hidden="true" />
            浏览器
          </button>
        </div>
        {onClose ? (
          <button className="session-file-close" type="button" aria-label="隐藏文件区" onClick={onClose}>
            <PanelRightClose size={16} strokeWidth={2} aria-hidden="true" />
          </button>
        ) : null}
      </header>
      {hasPreviewTabs ? (
      <header className="session-file-workspace-toolbar">
        <div className="session-file-breadcrumbs" aria-label="当前位置">
          <span title={workspaceRoot}>{rootName}</span>
          {breadcrumbs.map((part, index) => (
            <span key={`${part}-${index}`}>{part}</span>
          ))}
        </div>
        <div className="session-file-toolbar-actions">
          <button
            className="session-file-add-chat"
            disabled={!previewPath || !onAddFileToChat}
            type="button"
            onClick={() => addPathToChat(previewPath)}
          >
            <MessageSquarePlus size={17} strokeWidth={1.9} aria-hidden="true" />
            <span>{chatAddedPath === previewPath ? "已添加到聊天" : "添加到聊天"}</span>
          </button>
          {previewPath ? <span className="session-file-kind-chip">{filePreviewKind === "diff" ? "Diff" : "文件"}</span> : null}
          {onToggleFocus ? (
            <IconButton
              label={focused ? "退出专注" : "专注文件"}
              icon={focused ? <Minimize2 size={15} strokeWidth={2} /> : <Maximize2 size={15} strokeWidth={2} />}
              disabled={!workspaceRoot}
              onClick={() => {
                onToggleFocus();
              }}
            />
          ) : null}
          <details className="session-file-more">
            <summary aria-label="更多文件操作" title="更多文件操作">
              <MoreHorizontal size={16} strokeWidth={2} aria-hidden="true" />
            </summary>
            <div className="session-file-more-menu" role="menu">
              <button
                disabled={!previewPath || !onAddFileToChat}
                role="menuitem"
                type="button"
                onClick={() => addPathToChat(previewPath)}
              >
                <MessageSquarePlus size={14} strokeWidth={1.9} aria-hidden="true" />
                <span>{chatAddedPath === previewPath ? "已添加到聊天" : "添加到聊天"}</span>
              </button>
              <button disabled={!currentAbsolutePath} role="menuitem" type="button" onClick={() => void copyCurrentPath()}>
                <Copy size={14} strokeWidth={1.9} aria-hidden="true" />
                <span>{copiedPath ? "已复制路径" : "复制路径"}</span>
              </button>
              <button
                aria-checked={wrapLines}
                role="menuitemcheckbox"
                type="button"
                onClick={() => {
                  setWrapLines((current) => !current);
                }}
              >
                <WrapText size={14} strokeWidth={1.9} aria-hidden="true" />
                <span>{wrapLines ? "关闭自动换行" : "启用自动换行"}</span>
              </button>
              <button
                aria-checked={previewMode === "source"}
                disabled={!markdownPreviewAvailable}
                role="menuitemcheckbox"
                type="button"
                onClick={() => {
                  setPreviewMode((current) => (current === "preview" ? "source" : "preview"));
                }}
              >
                <TextCursorInput size={14} strokeWidth={1.9} aria-hidden="true" />
                <span>{previewMode === "source" ? "显示 Markdown 预览" : "查看 Markdown 源码"}</span>
              </button>
              <button
                disabled={!currentAbsolutePath || !onOpenExternalFile}
                role="menuitem"
                title={onOpenExternalFile ? "在外部编辑器中打开" : "等待宿主接入打开编辑器能力"}
                type="button"
                onClick={openExternalFile}
              >
                <Pencil size={14} strokeWidth={1.9} aria-hidden="true" />
                <span>{onOpenExternalFile ? "在编辑器中打开" : "编辑器打开待接入"}</span>
              </button>
            </div>
          </details>
          <IconButton
            label="刷新目录"
            icon={<RefreshCw size={15} strokeWidth={2} />}
            disabled={!workspaceRoot || !canBrowseFiles}
            loading={loadingPath === ROOT_DIR}
            onClick={() => {
              void loadDirectory(ROOT_DIR);
            }}
          />
        </div>
      </header>
      ) : null}

      {openFiles.length ? (
        <div className="session-file-open-tabs" aria-label="已打开文件">
          {openFiles.map((path) => (
            <div
              key={path}
              aria-selected={selectedFile === path}
              className="session-file-open-tab"
              role="tab"
              title={path}
              onContextMenu={(event) => {
                event.preventDefault();
                event.stopPropagation();
                setContextMenu(null);
                setPreviewTabContextMenu({
                  x: event.clientX,
                  y: event.clientY,
                  path,
                });
              }}
            >
              <button
                className="session-file-open-tab-main"
                type="button"
                onClick={() => {
                  openPreviewTab(path);
                }}
              >
                <FileTypeBadge path={path} subtle={selectedFile !== path} />
                <span>{fileNameFromPath(path)}</span>
              </button>
              <button
                aria-label={`关闭 ${fileNameFromPath(path)}`}
                className="session-file-open-tab-close"
                type="button"
                onClick={() => closeOpenFile(path)}
              >
                <X size={12} strokeWidth={2.1} aria-hidden="true" />
              </button>
            </div>
          ))}
          {normalizedRelatedFiles.length ? (
            <button
              className="session-file-open-tab session-file-open-tab-plus"
              title="打开相关文件"
              type="button"
              onClick={() => {
                const next = normalizedRelatedFiles.find((path) => !openFiles.includes(path)) ?? normalizedRelatedFiles[0];
                if (next) {
                  void openFile(next);
                }
              }}
            >
              <Plus size={14} strokeWidth={2.5} />
            </button>
          ) : null}
        </div>
      ) : null}

      {!canBrowseFiles ? (
        <div className="session-file-runtime-note">
          桌面运行时中会显示完整目录树；当前预览只能展示本轮任务关联文件。
        </div>
      ) : null}
      {error ? <p className="session-tool-error">{error}</p> : null}

      <div ref={fileBrowserLayoutRef} className={fileBrowserClassName} style={fileBrowserStyle}>
        <aside className="session-file-tree-pane" aria-label="项目文件">
          <header className="session-file-tree-head">
            <details ref={fileScopeMenuRef} className="session-file-scope-menu">
              <summary aria-label="选择文件范围">
                <span>{fileScopeLabel}</span>
                <ChevronDown size={15} strokeWidth={2} aria-hidden="true" />
              </summary>
              <div className="session-file-scope-menu-list" role="menu">
                <button
                  data-selected={fileScope === "changed"}
                  disabled={!normalizedChangedFiles.length}
                  role="menuitemradio"
                  aria-checked={fileScope === "changed"}
                  type="button"
                  onClick={() => selectFileScope("changed")}
                >
                  已更改文件
                  <span>{normalizedChangedFiles.length}</span>
                </button>
                <button
                  data-selected={fileScope === "all"}
                  role="menuitemradio"
                  aria-checked={fileScope === "all"}
                  type="button"
                  onClick={() => selectFileScope("all")}
                >
                  所有文件
                </button>
              </div>
            </details>
            <button
              aria-label="刷新目录"
              type="button"
              disabled={!workspaceRoot || !canBrowseFiles}
              onClick={() => {
                void loadDirectory(ROOT_DIR);
              }}
            >
              <RefreshCw size={17} strokeWidth={2} aria-hidden="true" />
            </button>
          </header>
          <label className="session-file-search">
            <span>筛选文件</span>
            <Search size={14} strokeWidth={2} aria-hidden="true" />
            <input
              aria-label="筛选文件"
              value={query}
              placeholder="筛选文件..."
              onChange={(event) => setQuery(event.target.value)}
            />
            {query ? (
              <button
                type="button"
                aria-label="清空筛选文件"
                onClick={() => setQuery("")}
              >
                <X size={13} strokeWidth={2.1} aria-hidden="true" />
              </button>
            ) : null}
          </label>
          {trimmedQuery.length >= 2 ? (
            <section className="session-file-search-results" aria-label="全局文件搜索">
              <header>
                <strong>全局匹配</strong>
                <span>
                  {workspaceSearchLoading ? "搜索中" : `${workspaceSearchResults.length}${workspaceSearchTruncated ? "+" : ""} 项`}
                </span>
              </header>
              {workspaceSearchLoading ? (
                <div className="session-file-search-empty" role="status">正在搜索...</div>
              ) : workspaceSearchResults.length ? (
                <ul>
                  {workspaceSearchResults.map((entry) => (
                    <li key={`${entry.kind}:${entry.path}`}>
                      <button
                        type="button"
                        onClick={() => {
                          openLocatedEntry(entry);
                        }}
                        onContextMenu={(event) => openContextMenu(event, { path: entry.path, kind: entry.kind })}
                      >
                        <FileIcon kind={entry.kind} path={entry.path} />
                        <span>{entry.name || fileNameFromPath(entry.path)}</span>
                        <small>{entry.kind === "directory" ? entry.path : `${entry.path}${entry.size ? ` · ${formatFileSize(entry.size)}` : ""}`}</small>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="session-file-search-empty">没有找到匹配文件。</p>
              )}
            </section>
          ) : null}
          <div className="session-file-tree-scroll">
            {fileScope === "changed" ? (
              <section className="session-file-change-strip" aria-label="本轮改动文件">
                <header>
                  <strong>本轮改动</strong>
                  <span>{normalizedChangedFiles.length} 个文件</span>
                </header>
                <ul>
                  {normalizedChangedFiles.slice(0, 8).map((file) => {
                    const selected = selectedFile === file.path;
                    const meta = [
                      normalizeChangeStatus(file.status),
                      file.additions !== undefined ? `+${file.additions}` : "",
                      file.deletions !== undefined ? `-${file.deletions}` : "",
                    ].filter(Boolean).join(" ");
                    return (
                      <li key={file.path}>
                        <button
                          type="button"
                          data-selected={selected}
                          disabled={!canBrowseFiles || !workspaceRoot}
                          onClick={() => {
                            void openChangedFile(file.path);
                          }}
                          onContextMenu={(event) => openContextMenu(event, { path: file.path, kind: "file" })}
                        >
                          <FileIcon kind="file" path={file.path} />
                          <span>{fileNameFromPath(file.path)}</span>
                          <small title={file.path}>{file.path}</small>
                          {meta ? <em>{meta}</em> : null}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ) : rootEntries.length ? (
              <FileTreeRows
                entries={rootEntries}
                childrenByPath={childrenByPath}
                expanded={expanded}
                loadingPath={loadingPath}
                query={query}
                selectedPath={selectedFile}
                onOpenDirectory={openDirectory}
                onOpenFile={openFile}
                onContextMenu={openContextMenu}
              />
            ) : loadingPath === ROOT_DIR ? (
              <div className="session-file-tree-empty">正在加载目录...</div>
            ) : (
              <div className="session-file-tree-empty">还没有目录数据。</div>
            )}
          </div>

          <section className="session-file-related">
            <strong>任务相关文件</strong>
            {normalizedRelatedFiles.length ? (
              <ul>
                {normalizedRelatedFiles.slice(0, 10).map((path) => (
                  <li key={path}>
                    <button
                      disabled={!canBrowseFiles || !workspaceRoot}
                      type="button"
                      onClick={() => {
                        void openFile(path);
                      }}
                    >
                      <span>{fileNameFromPath(path)}</span>
                      <small>{path}</small>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="session-tool-muted">本轮还没有记录关联文件。</p>
            )}
          </section>
        </aside>

        {hasPreviewTabs ? (
          <>
        <div
          aria-label="调整文件列表宽度"
          aria-orientation="vertical"
          aria-valuemin={FILE_TREE_MIN_WIDTH}
          aria-valuenow={fileTreeWidthPx ?? undefined}
          className="session-file-browser-resizer"
          onKeyDown={handleFileTreeResizeKeyDown}
          onPointerDown={startFileTreeResize}
          role="separator"
          tabIndex={0}
          title="拖动以调整文件列表宽度"
        >
          <GripVertical size={13} strokeWidth={1.9} aria-hidden="true" />
        </div>

        <main className="session-file-viewer" aria-label="文件内容">
          <header className="session-file-viewer-header">
            <div>
              <strong>{filePreview?.path ? fileNameFromPath(filePreview.path) : "选择文件"}</strong>
              <small title={filePreview?.path || workspaceRoot}>{filePreview?.path ? compactPath(filePreview.path) : workspaceLabel || workspaceRoot || "当前工作区"}</small>
            </div>
            {filePreview ? (
              <span>
                {filePreviewKind === "diff" ? "diff" : filePreview.binary ? "binary" : languageFromPath(filePreview.path)}
                {filePreview.bytes ? ` · ${formatFileSize(filePreview.bytes)}` : ""}
                {filePreview.truncated ? " · 已截断" : ""}
              </span>
            ) : null}
          </header>
          {markdownPreviewAvailable ? (
            <div className="session-file-view-mode" aria-label="Markdown 查看模式">
              <button
                type="button"
                aria-pressed={previewMode === "preview"}
                onClick={() => setPreviewMode("preview")}
              >
                预览
              </button>
              <button
                type="button"
                aria-pressed={previewMode === "source"}
                onClick={() => setPreviewMode("source")}
              >
                源码
              </button>
            </div>
          ) : null}
          {topTabs.length ? (
            <div className="session-file-inline-tabs" aria-label="文件标签">
              {topTabs.map((path) => (
                <button
                  key={path}
                  aria-selected={selectedFile === path}
                  className="session-file-inline-tab"
                  type="button"
                  onClick={() => {
                    void openFile(path);
                  }}
                >
                  <FileIcon kind="file" path={path} />
                  <span>{fileNameFromPath(path)}</span>
                </button>
              ))}
            </div>
          ) : null}

          {readingPath ? (
            <div className="session-file-viewer-empty" role="status">正在读取 {fileNameFromPath(readingPath)}</div>
          ) : filePreview?.binary ? (
            <div className="session-file-viewer-empty">
              <Binary size={18} strokeWidth={1.8} />
              <strong>这是二进制文件</strong>
              <span>已跳过文本预览。</span>
            </div>
          ) : filePreview?.content !== undefined ? (
            filePreviewKind === "file" && isMarkdownPath(filePreview.path) && previewMode !== "source" ? (
              <div
                className="session-file-markdown-layout"
                onMouseUp={(event) => {
                  if (!onAddSelectionToChat) return;
                  const selection = window.getSelection();
                  if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
                    setSelectionMenu(null);
                    return;
                  }
                  const range = selection.getRangeAt(0);
                  const root = event.currentTarget;
                  const startElement = elementForNode(range.startContainer);
                  const endElement = elementForNode(range.endContainer);
                  if (!startElement || !endElement || !root.contains(startElement) || !root.contains(endElement)) {
                    setSelectionMenu(null);
                    return;
                  }
                  const text = selection.toString().trim();
                  if (!text) {
                    setSelectionMenu(null);
                    return;
                  }
                  setSelectionMenu({
                    text,
                    ...lineRangeForText(filePreview.content ?? "", text),
                    ...selectionMenuPosition(range, root, event),
                  });
                }}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setSelectionMenu(null);
                }}
              >
                <article className="session-file-markdown">
                  <MarkdownContent content={filePreview.content} />
                </article>
              </div>
            ) : (
              <CodePreview
                activeLine={highlightedLine}
                commentDraft={lineComment?.draft ?? ""}
                commentLine={lineComment?.lineNumber ?? null}
                content={filePreview.content}
                onCancelLineComment={() => setLineComment(null)}
                onCommentDraftChange={(draft) => setLineComment((current) => current ? { ...current, draft } : null)}
                onLineCommentClick={(lineNumber) => {
                  setSelectionMenu(null);
                  setLineComment({ lineNumber, draft: "" });
                }}
                onSubmitLineComment={submitLineCommentToChat}
                onSelection={onAddSelectionToChat ? setSelectionMenu : undefined}
                path={filePreviewKind === "diff" ? `${filePreview.path}.diff` : filePreview.path}
                wrapLines={wrapLines}
              />
            )
          ) : (
            <div className="session-file-viewer-empty">
              <strong>从目录树打开文件</strong>
              <span>Markdown 会渲染为文档，代码会保留行号和等宽排版。</span>
            </div>
          )}
        </main>
          </>
        ) : null}
      </div>
      {selectionMenu ? (
        <button
          aria-label="添加选中内容到聊天"
          className="session-file-selection-menu"
          type="button"
          style={{ left: selectionMenu.x, top: selectionMenu.y }}
          onMouseDown={(event) => event.preventDefault()}
          onClick={addCurrentSelectionToChat}
        >
          <MessageSquarePlus size={16} strokeWidth={2} aria-hidden="true" />
          <span>添加到聊天</span>
        </button>
      ) : null}
      {previewTabContextMenu ? (
        <div className="session-file-context-menu session-file-tab-context-menu" role="menu" style={previewTabContextMenuStyle}>
          <button
            role="menuitem"
            type="button"
            onClick={() => closeOpenFile(previewTabContextMenu.path)}
          >
            <X size={14} strokeWidth={1.9} aria-hidden="true" />
            <span>关闭</span>
          </button>
          <button
            disabled={openFiles.length <= 1}
            role="menuitem"
            type="button"
            onClick={() => closeOpenFiles(openFiles.filter((path) => path !== previewTabContextMenu.path))}
          >
            <X size={14} strokeWidth={1.9} aria-hidden="true" />
            <span>关闭其他</span>
          </button>
          <button
            disabled={!previewTabsToLeft.length}
            role="menuitem"
            type="button"
            onClick={() => closeOpenFiles(previewTabsToLeft)}
          >
            <X size={14} strokeWidth={1.9} aria-hidden="true" />
            <span>关闭左侧</span>
          </button>
          <button
            disabled={!previewTabsToRight.length}
            role="menuitem"
            type="button"
            onClick={() => closeOpenFiles(previewTabsToRight)}
          >
            <X size={14} strokeWidth={1.9} aria-hidden="true" />
            <span>关闭右侧</span>
          </button>
          <span role="separator" aria-hidden="true" />
          <button
            disabled={!openFiles.length}
            role="menuitem"
            type="button"
            onClick={() => closeOpenFiles(openFiles)}
          >
            <X size={14} strokeWidth={1.9} aria-hidden="true" />
            <span>关闭全部</span>
          </button>
        </div>
      ) : null}
      {contextMenu ? (
        <div className="session-file-context-menu" role="menu" style={contextMenuStyle}>
          <button
            data-action="add"
            disabled={!onAddFileToChat}
            role="menuitem"
            type="button"
            onClick={() => {
              addPathToChat(contextMenu.path);
              setContextMenu(null);
            }}
          >
            <MessageSquarePlus size={14} strokeWidth={1.9} aria-hidden="true" />
            <span>{chatAddedPath === contextMenu.path ? "已添加到聊天" : "添加到聊天"}</span>
          </button>
          <button
            data-action="copy"
            role="menuitem"
            type="button"
            onClick={() => {
              void copyRelativePath(contextMenu.path);
              setContextMenu(null);
            }}
          >
            <Copy size={14} strokeWidth={1.9} aria-hidden="true" />
            <span>复制路径</span>
          </button>
          <button
            data-action="copy-absolute"
            role="menuitem"
            type="button"
            onClick={() => {
              void copyWorkspacePath(contextMenu.path);
              setContextMenu(null);
            }}
          >
            <Copy size={14} strokeWidth={1.9} aria-hidden="true" />
            <span>复制绝对路径</span>
          </button>
          <button
            data-action="external"
            disabled={!onOpenExternalFile}
            role="menuitem"
            type="button"
            onClick={() => {
              openExternalPath(contextMenu.path);
              setContextMenu(null);
            }}
          >
            <Pencil size={14} strokeWidth={1.9} aria-hidden="true" />
            <span>在编辑器中打开</span>
          </button>
          <button
            data-action="reveal"
            disabled={!onOpenExternalFile}
            role="menuitem"
            type="button"
            onClick={() => {
              openContainingFolder(contextMenu.path, contextMenu.kind);
              setContextMenu(null);
            }}
          >
            <FolderOpen size={14} strokeWidth={1.9} aria-hidden="true" />
            <span>在 Explorer 中显示</span>
          </button>
        </div>
      ) : null}
    </section>
  );
}
