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
});
