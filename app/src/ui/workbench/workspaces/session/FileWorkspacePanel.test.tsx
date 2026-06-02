import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GitLocalDiffParams, WorkspaceFileListParams, WorkspaceFileReadParams, WorkspaceFileSearchParams } from "@shared";
import { FileWorkspacePanel } from "./FileWorkspacePanel";

const runtimeMocks = vi.hoisted(() => ({
  workspaceFileList: vi.fn(),
  workspaceFileRead: vi.fn(),
  workspaceFileSearch: vi.fn(),
  gitLocalDiff: vi.fn(),
}));

vi.mock("../../../../lib/runtimeClient", () => ({
  RuntimeClient: vi.fn(function RuntimeClient() {
    return {
      workspaceFileList: runtimeMocks.workspaceFileList,
      workspaceFileRead: runtimeMocks.workspaceFileRead,
      workspaceFileSearch: runtimeMocks.workspaceFileSearch,
      gitLocalDiff: runtimeMocks.gitLocalDiff,
    };
  }),
}));

function installDesktopBridge() {
  Object.defineProperty(window, "__TAURI_INTERNALS__", {
    configurable: true,
    value: {},
  });
}

function removeDesktopBridge() {
  delete (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
}

function findCodeLine(text: string) {
  return screen.findByText((_, element) =>
    element?.classList.contains("session-file-line-text") === true &&
    element.textContent?.trim() === text.trim(),
  );
}

async function openTreeFile(user: ReturnType<typeof userEvent.setup>, name: RegExp, parent?: RegExp) {
  const tree = await screen.findByLabelText("项目文件");
  if (parent) {
    await user.click(await within(tree).findByRole("treeitem", { name: parent }));
  }
  await user.click(await within(tree).findByRole("treeitem", { name }));
  return tree;
}

describe("FileWorkspacePanel", () => {
  beforeEach(() => {
    installDesktopBridge();
    runtimeMocks.workspaceFileList.mockImplementation(async (payload: WorkspaceFileListParams) => {
      const path = payload.path ?? "";
      if (!path) {
        return {
          rootPath: "D:/demo-blog",
          path: "",
          entries: [
            { name: "README.md", path: "README.md", kind: "file", size: 42 },
            { name: "src", path: "src", kind: "directory" },
            { name: "tests", path: "tests", kind: "directory" },
          ],
        };
      }
      if (path === "src") {
        return {
          rootPath: "D:/demo-blog",
          path: "src",
          entries: [{ name: "app.py", path: "src/app.py", kind: "file", size: 66 }],
        };
      }
      return {
        rootPath: "D:/demo-blog",
        path,
        entries: [],
      };
    });
    runtimeMocks.workspaceFileRead.mockImplementation(async (payload: WorkspaceFileReadParams) => {
      if (payload.path === "README.md") {
        return {
          rootPath: "D:/demo-blog",
          path: "README.md",
          content: "# Blog Fixture\n\n## Usage\n\n- Run `pytest` before shipping.\n\n### Notes\n\nKeep it tidy.",
          bytes: 46,
          truncated: false,
          binary: false,
        };
      }
      return {
        rootPath: "D:/demo-blog",
        path: payload.path,
        content: "def hello():\n    return 'world'\n",
        bytes: 32,
        truncated: false,
        binary: false,
      };
    });
    runtimeMocks.workspaceFileSearch.mockImplementation(async (payload: WorkspaceFileSearchParams) => {
      if (payload.query === "deep") {
        return {
          rootPath: "D:/demo-blog",
          query: payload.query,
          entries: [{ name: "deep.py", path: "src/deep.py", kind: "file", size: 88 }],
          truncated: false,
          scanned: 4,
        };
      }
      return {
        rootPath: "D:/demo-blog",
        query: payload.query ?? "",
        entries: [],
        truncated: false,
        scanned: 4,
      };
    });
    runtimeMocks.gitLocalDiff.mockImplementation(async (payload: GitLocalDiffParams) => ({
      cwd: payload.cwd,
      repoRoot: payload.cwd,
      diff: [
        "diff --git a/src/app.py b/src/app.py",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1,2 +1,2 @@",
        "-def old():",
        "+def hello():",
        "     return 'world'",
      ].join("\n"),
      stat: "src/app.py | 2 +-",
      files: payload.path ? [payload.path] : ["src/app.py"],
      truncated: false,
    }));
  });

  afterEach(() => {
    removeDesktopBridge();
    vi.restoreAllMocks();
    runtimeMocks.workspaceFileList.mockReset();
    runtimeMocks.workspaceFileRead.mockReset();
    runtimeMocks.workspaceFileSearch.mockReset();
    runtimeMocks.gitLocalDiff.mockReset();
    cleanup();
  });

  it("loads the workspace tree, renders markdown, and opens code files", async () => {
    const user = userEvent.setup();
    const onToggleFocus = vi.fn();
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        relatedFiles={["README.md", "src/app.py"]}
        onToggleFocus={onToggleFocus}
      />,
    );

    const tree = await openTreeFile(user, /README\.md/);
    expect(await screen.findByRole("heading", { name: "Blog Fixture" })).toBeInTheDocument();
    expect(screen.getByText("pytest")).toBeInTheDocument();
    expect(screen.queryByLabelText("Markdown 大纲")).not.toBeInTheDocument();

    await user.click(within(tree).getByRole("treeitem", { name: /src/ }));
    expect(runtimeMocks.workspaceFileList).toHaveBeenCalledWith(
      expect.objectContaining({ workspaceRoot: "D:/demo-blog", path: "src" }),
    );

    await user.click(await within(tree).findByRole("treeitem", { name: /app\.py/ }));
    const functionLine = await findCodeLine("def hello():");
    const returnLine = await findCodeLine("return 'world'");
    expect(functionLine).toBeInTheDocument();
    expect(returnLine).toBeInTheDocument();
    expect(within(functionLine).getByText("def")).toHaveClass("session-file-syntax-keyword");
    expect(within(returnLine).getByText("'world'")).toHaveClass("session-file-syntax-string");

    await user.click(screen.getByRole("button", { name: "专注文件" }));
    expect(onToggleFocus).toHaveBeenCalledTimes(1);
  });

  it("switches markdown files between rendered preview and source code", async () => {
    const user = userEvent.setup();
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        relatedFiles={["README.md"]}
      />,
    );

    await openTreeFile(user, /README\.md/);
    expect(await screen.findByRole("heading", { name: "Blog Fixture" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Markdown 大纲")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "预览" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "源码" }));
    const headingLine = await findCodeLine("# Blog Fixture");
    expect(headingLine).toBeInTheDocument();
    expect(screen.queryByLabelText("Markdown 大纲")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Blog Fixture" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "预览" }));
    expect(await screen.findByRole("heading", { name: "Blog Fixture" })).toBeInTheDocument();
  });

  it("opens an externally selected file and loads its parent directory", async () => {
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        activeFilePath="src/app.py"
        activeFileRequestKey={1}
      />,
    );

    expect(await findCodeLine("def hello():")).toBeInTheDocument();
    expect(runtimeMocks.workspaceFileRead).toHaveBeenCalledWith(
      expect.objectContaining({ workspaceRoot: "D:/demo-blog", path: "src/app.py" }),
    );
    expect(runtimeMocks.workspaceFileList).toHaveBeenCalledWith(
      expect.objectContaining({ workspaceRoot: "D:/demo-blog", path: "src" }),
    );

    const tree = screen.getByRole("tree");
    expect(await within(tree).findByRole("treeitem", { name: /app\.py/ })).toHaveAttribute("data-selected", "true");
  });

  it("shows changed files above the tree and opens their diff as review context", async () => {
    const user = userEvent.setup();
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        changedFiles={[
          { path: "src/app.py", status: "modified", additions: 2, deletions: 1 },
          { path: "README.md", status: "added", additions: 4 },
        ]}
      />,
    );

    await user.click(await screen.findByLabelText("选择文件范围"));
    await user.click(screen.getByRole("menuitemradio", { name: /已更改文件/ }));

    const changes = await screen.findByLabelText("本轮改动文件");
    expect(within(changes).getByText("本轮改动")).toBeInTheDocument();
    expect(within(changes).getByText("2 个文件")).toBeInTheDocument();
    expect(within(changes).getByRole("button", { name: /app\.py/ })).toHaveTextContent("修改 +2 -1");
    expect(within(changes).getByRole("button", { name: /README\.md/ })).toHaveTextContent("新增 +4");

    await user.click(within(changes).getByRole("button", { name: /app\.py/ }));
    expect(await findCodeLine("+def hello():")).toBeInTheDocument();
    expect(screen.getByText("Diff")).toBeInTheDocument();
    expect(runtimeMocks.gitLocalDiff).toHaveBeenCalledWith({ cwd: "D:/demo-blog", path: "src/app.py" });
  });

  it("opens an externally selected file target with a highlighted line", async () => {
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        activeFilePath="src/app.py:2"
        activeFileRequestKey={1}
      />,
    );

    const returnLine = await findCodeLine("return 'world'");
    expect(returnLine.closest("li")).toHaveAttribute("data-active-line", "true");
    expect(runtimeMocks.workspaceFileRead).toHaveBeenCalledWith(
      expect.objectContaining({ workspaceRoot: "D:/demo-blog", path: "src/app.py" }),
    );
    expect(runtimeMocks.workspaceFileRead).not.toHaveBeenCalledWith(
      expect.objectContaining({ path: "src/app.py:2" }),
    );
  });

  it("searches the workspace globally and opens a matched file", async () => {
    const user = userEvent.setup();
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
      />,
    );

    await screen.findByLabelText("项目文件");
    await user.type(screen.getByRole("textbox", { name: "筛选文件" }), "deep");

    const globalResults = await screen.findByLabelText("全局文件搜索");
    expect(await within(globalResults).findByText("deep.py")).toBeInTheDocument();
    await user.click(within(globalResults).getByRole("button", { name: /deep\.py/ }));

    expect(await findCodeLine("def hello():")).toBeInTheDocument();
    expect(runtimeMocks.workspaceFileSearch).toHaveBeenCalledWith({
      workspaceRoot: "D:/demo-blog",
      query: "deep",
      maxEntries: 18,
    });
    expect(runtimeMocks.workspaceFileRead).toHaveBeenCalledWith(
      expect.objectContaining({ workspaceRoot: "D:/demo-blog", path: "src/deep.py" }),
    );
    expect(runtimeMocks.workspaceFileList).toHaveBeenCalledWith(
      expect.objectContaining({ workspaceRoot: "D:/demo-blog", path: "src" }),
    );
  });

  it("clears the workspace file filter", async () => {
    const user = userEvent.setup();
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
      />,
    );

    await screen.findByLabelText("项目文件");
    const filterInput = screen.getByRole("textbox", { name: "筛选文件" });
    await user.type(filterInput, "deep");
    expect(filterInput).toHaveValue("deep");

    await user.click(screen.getByRole("button", { name: "清空筛选文件" }));
    expect(filterInput).toHaveValue("");
    expect(screen.queryByLabelText("全局文件搜索")).not.toBeInTheDocument();
  });

  it("lets the file list fill the panel before a preview tab is opened", async () => {
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
      />,
    );

    expect(await screen.findByLabelText("项目文件")).toBeInTheDocument();
    const panel = screen.getByLabelText("文件浏览器");
    expect(panel).toHaveAttribute("data-has-preview", "false");
    expect(screen.queryByLabelText("文件内容")).not.toBeInTheDocument();
    expect(screen.queryByRole("separator", { name: "调整文件列表宽度" })).not.toBeInTheDocument();
  });

  it("reports when preview tabs are opened and closed", async () => {
    const user = userEvent.setup();
    const onPreviewStateChange = vi.fn();
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        onPreviewStateChange={onPreviewStateChange}
      />,
    );

    expect(await screen.findByLabelText("项目文件")).toBeInTheDocument();
    expect(onPreviewStateChange).toHaveBeenLastCalledWith(false);

    await openTreeFile(user, /README\.md/);
    expect(await screen.findByRole("heading", { name: "Blog Fixture" })).toBeInTheDocument();
    expect(onPreviewStateChange).toHaveBeenLastCalledWith(true);

    await user.click(screen.getByRole("button", { name: "关闭 README.md" }));
    expect(onPreviewStateChange).toHaveBeenLastCalledWith(false);
  });

  it("offers haha-cc style close actions from open file tabs", async () => {
    const user = userEvent.setup();
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        relatedFiles={["README.md", "src/app.py"]}
      />,
    );

    const tree = await openTreeFile(user, /README\.md/);
    await user.click(within(tree).getByRole("treeitem", { name: /src/ }));
    await user.click(await within(tree).findByRole("treeitem", { name: /app\.py/ }));
    expect(await findCodeLine("def hello():")).toBeInTheDocument();

    const tabList = screen.getByLabelText("已打开文件");
    fireEvent.contextMenu(within(tabList).getByRole("tab", { name: /README\.md/ }));
    let menu = document.querySelector(".session-file-tab-context-menu") as HTMLElement;
    await user.click(within(menu).getByRole("menuitem", { name: "关闭右侧" }));
    expect(within(tabList).queryByRole("tab", { name: /app\.py/ })).not.toBeInTheDocument();
    expect(within(tabList).getByRole("tab", { name: /README\.md/ })).toBeInTheDocument();

    fireEvent.contextMenu(within(tabList).getByRole("tab", { name: /README\.md/ }));
    menu = document.querySelector(".session-file-tab-context-menu") as HTMLElement;
    await user.click(within(menu).getByRole("menuitem", { name: "关闭全部" }));
    expect(screen.queryByLabelText("已打开文件")).not.toBeInTheDocument();
  });

  it("copies the current path and toggles code wrapping from the lightweight file menu", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    const onOpenExternalFile = vi.fn();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        relatedFiles={["src/app.py"]}
        onOpenExternalFile={onOpenExternalFile}
      />,
    );

    const tree = await screen.findByLabelText("项目文件");
    await user.click(within(tree).getByRole("treeitem", { name: /src/ }));
    await user.click(await within(tree).findByRole("treeitem", { name: /app\.py/ }));
    const code = await findCodeLine("def hello():");
    const codeLines = code.closest(".session-file-code-lines");
    expect(codeLines).toHaveAttribute("data-wrap", "false");

    await user.click(screen.getByLabelText("更多文件操作"));
    await user.click(screen.getByRole("menuitem", { name: "复制路径" }));
    expect(writeText).toHaveBeenCalledWith("D:/demo-blog/src/app.py");
    expect(screen.getByRole("menuitem", { name: "已复制路径" })).toBeInTheDocument();

    await user.click(screen.getByRole("menuitemcheckbox", { name: "启用自动换行" }));
    expect(codeLines).toHaveAttribute("data-wrap", "true");

    await user.click(screen.getByRole("menuitem", { name: "在编辑器中打开" }));
    expect(onOpenExternalFile).toHaveBeenCalledWith("D:/demo-blog/src/app.py");
  });

  it("offers haha-cc style context actions from the file tree", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    const onOpenExternalFile = vi.fn();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        onOpenExternalFile={onOpenExternalFile}
        onAddFileToChat={vi.fn()}
      />,
    );

    const tree = await screen.findByLabelText("项目文件");
    fireEvent.contextMenu(within(tree).getByRole("treeitem", { name: /README\.md/ }));

    let contextMenu = document.querySelector(".session-file-context-menu") as HTMLElement;
    await user.click(within(contextMenu).getByRole("menuitem", { name: "复制路径" }));
    expect(writeText).toHaveBeenCalledWith("README.md");

    fireEvent.contextMenu(within(tree).getByRole("treeitem", { name: /README\.md/ }));
    contextMenu = document.querySelector(".session-file-context-menu") as HTMLElement;
    await user.click(within(contextMenu).getByRole("menuitem", { name: "复制绝对路径" }));
    expect(writeText).toHaveBeenCalledWith("D:/demo-blog/README.md");

    fireEvent.contextMenu(within(tree).getByRole("treeitem", { name: /README\.md/ }));
    contextMenu = document.querySelector(".session-file-context-menu") as HTMLElement;
    await user.click(within(contextMenu).getByRole("menuitem", { name: "在 Explorer 中显示" }));
    expect(onOpenExternalFile).toHaveBeenCalledWith("D:/demo-blog");
  });

  it("adds selected code text to chat with line numbers", async () => {
    const user = userEvent.setup();
    const onAddSelectionToChat = vi.fn();
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        relatedFiles={["src/app.py"]}
        onAddSelectionToChat={onAddSelectionToChat}
      />,
    );

    await openTreeFile(user, /app\.py/, /src/);
    const line = await findCodeLine("def hello():");
    const row = line.closest("li") as HTMLElement;
    const range = document.createRange();
    range.selectNodeContents(line);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    fireEvent.mouseUp(row);

    await user.click(screen.getByRole("button", { name: "添加选中内容到聊天" }));
    expect(onAddSelectionToChat).toHaveBeenCalledWith("src/app.py", {
      text: "def hello():",
      startLine: 1,
      endLine: 1,
    });
    expect(window.getSelection()?.rangeCount).toBe(0);
  });

  it("adds a line comment to chat from the code gutter", async () => {
    const user = userEvent.setup();
    const onAddSelectionToChat = vi.fn();
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        relatedFiles={["src/app.py"]}
        onAddSelectionToChat={onAddSelectionToChat}
      />,
    );

    await openTreeFile(user, /app\.py/, /src/);
    await findCodeLine("def hello():");
    await user.click(screen.getByRole("button", { name: "评论第 2 行" }));
    await user.type(screen.getByRole("textbox", { name: "第 2 行备注" }), "确认返回值");
    await user.click(screen.getByRole("button", { name: "添加第 2 行备注到聊天" }));

    expect(onAddSelectionToChat).toHaveBeenCalledWith("src/app.py", {
      text: "    return 'world'",
      note: "确认返回值",
      startLine: 2,
      endLine: 2,
    });
  });

  it("resizes the file tree pane with the lightweight separator", async () => {
    const user = userEvent.setup();
    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        relatedFiles={["src/app.py"]}
      />,
    );

    await openTreeFile(user, /app\.py/, /src/);
    await findCodeLine("def hello():");
    const layout = document.querySelector(".session-file-browser-layout") as HTMLElement;
    const treePane = document.querySelector(".session-file-tree-pane") as HTMLElement;
    Object.defineProperty(layout, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ width: 900, height: 620, top: 0, left: 0, right: 900, bottom: 620, x: 0, y: 0, toJSON: () => ({}) }),
    });
    Object.defineProperty(treePane, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ width: 360, height: 620, top: 0, left: 540, right: 900, bottom: 620, x: 540, y: 0, toJSON: () => ({}) }),
    });

    const separator = screen.getByRole("separator", { name: "调整文件列表宽度" });
    fireEvent.pointerDown(separator, { clientX: 500, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 420 });
    fireEvent.pointerUp(window);

    expect(layout.getAttribute("style")).toContain("--session-file-tree-width: 440px");
  });
});
