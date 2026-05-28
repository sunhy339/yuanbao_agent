import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
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
  GripVertical,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Search,
  WrapText,
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

function parentWorkspacePath(path: string) {
  const normalized = normalizeWorkspaceRelativePath(path);
  const index = normalized.lastIndexOf("/");
  return index > 0 ? normalized.slice(0, index) : ROOT_DIR;
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
    yml: "yaml",
  };
  return aliases[extension] ?? extension;
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
    const normalized = normalizeWorkspaceRelativePath(path);
    if (!normalized || seen.has(normalized)) {
      return;
    }
    seen.add(normalized);
    result.push(normalized);
  });
  return result;
}

function chooseInitialFile(relatedFiles: string[], rootEntries: WorkspaceFileEntry[]) {
  const related = relatedFiles.find((path) => path && isLikelyTextPath(path));
  if (related) {
    return related;
  }
  const rootFiles = rootEntries.filter((entry) => entry.kind === "file");
  return (
    rootFiles.find((entry) => /^readme(\.|$)/i.test(entry.name))?.path ??
    rootFiles.find((entry) => /^(package|pyproject|cargo|go\.mod|pom)\b/i.test(entry.name))?.path ??
    rootFiles.find((entry) => isLikelyTextPath(entry.path))?.path ??
    null
  );
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

const FILE_CODE_KEYWORDS = new Set([
  "and",
  "as",
  "async",
  "await",
  "break",
  "case",
  "catch",
  "class",
  "const",
  "continue",
  "def",
  "elif",
  "else",
  "enum",
  "except",
  "export",
  "finally",
  "for",
  "from",
  "function",
  "if",
  "import",
  "in",
  "interface",
  "let",
  "not",
  "or",
  "return",
  "switch",
  "try",
  "type",
  "while",
]);

function highlightCodeLine(line: string, keyPrefix: string) {
  const tokens: Array<string | JSX.Element> = [];
  const pattern =
    /(#.*$|\/\/.*$|\/\*.*?\*\/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][\w]*\b|[{}[\]().,:;+\-*/%=<>!|&]+)/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(line)) !== null) {
    if (match.index > cursor) tokens.push(line.slice(cursor, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${match.index}`;
    if (/^(#|\/\/|\/\*)/.test(token)) {
      tokens.push(<span className="session-file-syntax-comment" key={key}>{token}</span>);
    } else if (/^["'`]/.test(token)) {
      tokens.push(<span className="session-file-syntax-string" key={key}>{token}</span>);
    } else if (/^\d/.test(token)) {
      tokens.push(<span className="session-file-syntax-number" key={key}>{token}</span>);
    } else if (FILE_CODE_KEYWORDS.has(token)) {
      tokens.push(<span className="session-file-syntax-keyword" key={key}>{token}</span>);
    } else if (/^[{}[\]().,:;+\-*/%=<>!|&]+$/.test(token)) {
      tokens.push(<span className="session-file-syntax-operator" key={key}>{token}</span>);
    } else {
      tokens.push(token);
    }
    cursor = match.index + token.length;
  }
  if (cursor < line.length) tokens.push(line.slice(cursor));
  return tokens.length ? tokens : " ";
}

function CodePreview({ content, path, wrapLines = false }: { content: string; path: string; wrapLines?: boolean }) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const language = languageFromPath(path);
  return (
    <ol className="session-file-code-lines" data-language={language} data-wrap={wrapLines}>
      {lines.map((line, index) => (
        <li key={`${index}-${line.slice(0, 16)}`}>
          <span className="session-file-line-number">{index + 1}</span>
          <span className="session-file-line-text">{highlightCodeLine(line, `${language}-${index}`)}</span>
        </li>
      ))}
    </ol>
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
            >
              <span className="session-file-disclosure" aria-hidden="true">
                {isDirectory ? (isExpanded ? <ChevronDown size={13} strokeWidth={2} /> : <ChevronRight size={13} strokeWidth={2} />) : null}
              </span>
              <FileIcon kind={entry.kind} path={entry.path} expanded={isExpanded} />
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
  focused?: boolean;
  onToggleFocus?: () => void;
}

export function FileWorkspacePanel({
  workspaceRoot,
  workspaceLabel,
  relatedFiles = [],
  focused = false,
  onToggleFocus,
}: FileWorkspacePanelProps) {
  const canBrowseFiles = canUseTauriInvoke();
  const normalizedRelatedFiles = useMemo(() => uniquePaths(relatedFiles), [relatedFiles]);
  const [childrenByPath, setChildrenByPath] = useState<Record<string, WorkspaceFileEntry[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([ROOT_DIR]));
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [openFiles, setOpenFiles] = useState<string[]>([]);
  const [filePreview, setFilePreview] = useState<WorkspaceFileReadResult | null>(null);
  const [query, setQuery] = useState("");
  const [loadingPath, setLoadingPath] = useState<string | null>(null);
  const [readingPath, setReadingPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wrapLines, setWrapLines] = useState(false);
  const [copiedPath, setCopiedPath] = useState(false);
  const [fileTreeWidthPx, setFileTreeWidthPx] = useState<number | null>(() => readStoredFileTreeWidth());
  const [fileTreeResizing, setFileTreeResizing] = useState(false);
  const initialOpenDoneRef = useRef(false);
  const fileBrowserLayoutRef = useRef<HTMLDivElement | null>(null);
  const rootEntries = childrenByPath[ROOT_DIR] ?? [];
  const rootName = workspaceNameFromPath(workspaceRoot || workspaceLabel);
  const previewPath = selectedFile ?? filePreview?.path ?? "";
  const previewParent = previewPath ? parentWorkspacePath(previewPath) : ROOT_DIR;
  const currentAbsolutePath = workspaceAbsolutePath(workspaceRoot, previewPath || previewParent);
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
    async (path: string) => {
      if (!workspaceRoot || !canBrowseFiles) {
        return;
      }
      const normalizedPath = normalizeWorkspaceRelativePath(path);
      setReadingPath(normalizedPath);
      setSelectedFile(normalizedPath);
      setOpenFiles((current) => (current.includes(normalizedPath) ? current : [...current, normalizedPath].slice(-8)));
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

  const copyCurrentPath = useCallback(async () => {
    if (!currentAbsolutePath || typeof navigator === "undefined" || typeof navigator.clipboard?.writeText !== "function") {
      return;
    }
    await navigator.clipboard.writeText(currentAbsolutePath);
    setCopiedPath(true);
  }, [currentAbsolutePath]);

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

  useEffect(() => {
    setChildrenByPath({});
    setExpanded(new Set([ROOT_DIR]));
    setSelectedFile(null);
    setOpenFiles([]);
    setFilePreview(null);
    setError(null);
    setQuery("");
    initialOpenDoneRef.current = false;
  }, [workspaceRoot]);

  useEffect(() => {
    setCopiedPath(false);
  }, [currentAbsolutePath]);

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
    if (initialOpenDoneRef.current || !workspaceRoot || !canBrowseFiles || !rootEntries.length) {
      return;
    }
    initialOpenDoneRef.current = true;
    const candidate = chooseInitialFile(normalizedRelatedFiles, rootEntries);
    if (candidate) {
      void openFile(candidate);
    }
  }, [canBrowseFiles, normalizedRelatedFiles, openFile, rootEntries, workspaceRoot]);

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

  return (
    <section className="session-file-workspace" aria-label="文件浏览器">
      <header className="session-file-workspace-toolbar">
        <div className="session-file-breadcrumbs" aria-label="当前位置">
          <span title={workspaceRoot}>{rootName}</span>
          {breadcrumbs.map((part, index) => (
            <span key={`${part}-${index}`}>{part}</span>
          ))}
        </div>
        <div className="session-file-toolbar-actions">
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

      {openFiles.length ? (
        <div className="session-file-open-tabs" aria-label="已打开文件">
          {openFiles.map((path) => (
            <button
              key={path}
              aria-selected={selectedFile === path}
              className="session-file-open-tab"
              title={path}
              type="button"
              onClick={() => {
                void openFile(path);
              }}
            >
              <FileIcon kind="file" path={path} />
              <span>{fileNameFromPath(path)}</span>
            </button>
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
          <label className="session-file-search">
            <span>筛选文件</span>
            <Search size={14} strokeWidth={2} aria-hidden="true" />
            <input
              aria-label="筛选文件"
              value={query}
              placeholder="筛选文件..."
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="session-file-tree-scroll">
            {rootEntries.length ? (
              <FileTreeRows
                entries={rootEntries}
                childrenByPath={childrenByPath}
                expanded={expanded}
                loadingPath={loadingPath}
                query={query}
                selectedPath={selectedFile}
                onOpenDirectory={openDirectory}
                onOpenFile={openFile}
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
                {filePreview.binary ? "binary" : languageFromPath(filePreview.path)}
                {filePreview.bytes ? ` · ${formatFileSize(filePreview.bytes)}` : ""}
                {filePreview.truncated ? " · 已截断" : ""}
              </span>
            ) : null}
          </header>
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
            isMarkdownPath(filePreview.path) ? (
              <article className="session-file-markdown">
                <MarkdownContent content={filePreview.content} />
              </article>
            ) : (
              <CodePreview content={filePreview.content} path={filePreview.path} wrapLines={wrapLines} />
            )
          ) : (
            <div className="session-file-viewer-empty">
              <strong>从目录树打开文件</strong>
              <span>Markdown 会渲染为文档，代码会保留行号和等宽排版。</span>
            </div>
          )}
        </main>
      </div>
    </section>
  );
}
