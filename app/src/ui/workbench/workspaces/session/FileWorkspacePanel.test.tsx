import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkspaceFileListParams, WorkspaceFileReadParams } from "@shared";
import { FileWorkspacePanel } from "./FileWorkspacePanel";

const runtimeMocks = vi.hoisted(() => ({
  workspaceFileList: vi.fn(),
  workspaceFileRead: vi.fn(),
}));

vi.mock("../../../../lib/runtimeClient", () => ({
  RuntimeClient: vi.fn(function RuntimeClient() {
    return {
      workspaceFileList: runtimeMocks.workspaceFileList,
      workspaceFileRead: runtimeMocks.workspaceFileRead,
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
          content: "# Blog Fixture\n\n- Run `pytest` before shipping.",
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
  });

  afterEach(() => {
    removeDesktopBridge();
    vi.restoreAllMocks();
    runtimeMocks.workspaceFileList.mockReset();
    runtimeMocks.workspaceFileRead.mockReset();
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

    expect(await screen.findByRole("heading", { name: "Blog Fixture" })).toBeInTheDocument();
    expect(screen.getByText("pytest")).toBeInTheDocument();

    const tree = screen.getByLabelText("项目文件");
    await user.click(within(tree).getByRole("treeitem", { name: /src/ }));
    expect(runtimeMocks.workspaceFileList).toHaveBeenCalledWith(
      expect.objectContaining({ workspaceRoot: "D:/demo-blog", path: "src" }),
    );

    await user.click(await within(tree).findByRole("treeitem", { name: /app\.py/ }));
    expect(await screen.findByText("def hello():")).toBeInTheDocument();
    expect(screen.getByText("return 'world'")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "专注文件" }));
    expect(onToggleFocus).toHaveBeenCalledTimes(1);
  });

  it("copies the current path and toggles code wrapping from the lightweight file menu", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <FileWorkspacePanel
        workspaceRoot="D:/demo-blog"
        workspaceLabel="demo-blog"
        relatedFiles={["src/app.py"]}
      />,
    );

    const tree = await screen.findByLabelText("项目文件");
    await user.click(within(tree).getByRole("treeitem", { name: /src/ }));
    await user.click(await within(tree).findByRole("treeitem", { name: /app\.py/ }));
    const code = await screen.findByText("def hello():");
    const codeLines = code.closest(".session-file-code-lines");
    expect(codeLines).toHaveAttribute("data-wrap", "false");

    await user.click(screen.getByLabelText("更多文件操作"));
    await user.click(screen.getByRole("menuitem", { name: "复制路径" }));
    expect(writeText).toHaveBeenCalledWith("D:/demo-blog/src/app.py");
    expect(screen.getByRole("menuitem", { name: "已复制路径" })).toBeInTheDocument();

    await user.click(screen.getByRole("menuitemcheckbox", { name: "启用自动换行" }));
    expect(codeLines).toHaveAttribute("data-wrap", "true");
  });
});
