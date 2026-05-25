import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GitWorkspacePanel } from "./GitWorkspacePanel";

const runtimeMocks = vi.hoisted(() => ({
  gitLocalStatus: vi.fn(),
  gitLocalDiff: vi.fn(),
  gitLocalInit: vi.fn(),
  gitLocalCheckout: vi.fn(),
  gitLocalCommit: vi.fn(),
}));

vi.mock("../../../../lib/runtimeClient", () => ({
  RuntimeClient: vi.fn(function RuntimeClient() {
    return runtimeMocks;
  }),
}));

describe("GitWorkspacePanel", () => {
  beforeEach(() => {
    runtimeMocks.gitLocalStatus.mockResolvedValue({
      cwd: "D:/demo",
      repoRoot: "D:/demo",
      branch: "main",
      upstream: "origin/main",
      ahead: 1,
      behind: 0,
      dirtyFiles: 1,
      files: [{ path: "src/app.ts", status: "M", raw: " M src/app.ts" }],
      branches: [
        { name: "main", current: true },
        { name: "feature/ui", current: false },
      ],
      clean: false,
      rawStatus: "## main...origin/main [ahead 1]\n M src/app.ts\n",
    });
    runtimeMocks.gitLocalDiff.mockResolvedValue({
      cwd: "D:/demo",
      repoRoot: "D:/demo",
      diff: [
        "diff --git a/src/app.ts b/src/app.ts",
        "--- a/src/app.ts",
        "+++ b/src/app.ts",
        "@@ -1 +1 @@",
        "-old",
        "+new",
      ].join("\n"),
      stat: " src/app.ts | 2 +-",
      files: ["src/app.ts"],
      truncated: false,
    });
    runtimeMocks.gitLocalCheckout.mockResolvedValue({
      cwd: "D:/demo",
      repoRoot: "D:/demo",
      branch: "feature/ui",
      stdout: "",
      stderr: "",
      status: {
        cwd: "D:/demo",
        repoRoot: "D:/demo",
        branch: "feature/ui",
        dirtyFiles: 0,
        files: [],
        branches: [
          { name: "main", current: false },
          { name: "feature/ui", current: true },
        ],
        clean: true,
        rawStatus: "## feature/ui\n",
      },
    });
    runtimeMocks.gitLocalCommit.mockResolvedValue({
      cwd: "D:/demo",
      repoRoot: "D:/demo",
      stdout: "[main abc123] Update UI",
      stderr: "",
      status: {
        cwd: "D:/demo",
        repoRoot: "D:/demo",
        branch: "main",
        dirtyFiles: 0,
        files: [],
        branches: [{ name: "main", current: true }],
        clean: true,
        rawStatus: "## main\n",
      },
    });
  });

  afterEach(() => {
    cleanup();
    runtimeMocks.gitLocalStatus.mockReset();
    runtimeMocks.gitLocalDiff.mockReset();
    runtimeMocks.gitLocalCheckout.mockReset();
    runtimeMocks.gitLocalInit.mockReset();
    runtimeMocks.gitLocalCommit.mockReset();
  });

  it("loads local git status, renders real diff, and exposes checkout/commit actions", async () => {
    const user = userEvent.setup();
    render(
      <GitWorkspacePanel
        composerContext={{
          cwd: "D:/demo",
          branch: "main",
        }}
      />,
    );

    await waitFor(() => expect(runtimeMocks.gitLocalStatus).toHaveBeenCalledWith({ cwd: "D:/demo" }));
    expect(await screen.findByText("src/app.ts")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /查看 diff/ }));
    expect(runtimeMocks.gitLocalDiff).toHaveBeenCalledWith({ cwd: "D:/demo", path: undefined });
    expect(await screen.findByText("src/app.ts | 2 +-")).toBeInTheDocument();
    expect(screen.getByText("new")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("切换 Git 分支"), "feature/ui");
    await user.click(screen.getByRole("button", { name: "切换" }));
    expect(runtimeMocks.gitLocalCheckout).toHaveBeenCalledWith({ cwd: "D:/demo", branch: "feature/ui" });

    runtimeMocks.gitLocalStatus.mockResolvedValueOnce({
      cwd: "D:/demo",
      repoRoot: "D:/demo",
      branch: "main",
      dirtyFiles: 1,
      files: [{ path: "README.md", status: "M", raw: " M README.md" }],
      branches: [{ name: "main", current: true }],
      clean: false,
      rawStatus: "## main\n M README.md\n",
    });
    await user.click(screen.getByRole("button", { name: /状态/ }));
    await waitFor(() => expect(screen.getAllByText("README.md").length).toBeGreaterThan(0));

    const commitSection = screen.getByRole("textbox", { name: "Git 提交信息" }).closest("section");
    expect(commitSection).toBeTruthy();
    await user.type(within(commitSection as HTMLElement).getByLabelText("Git 提交信息"), "Update UI");
    await user.click(within(commitSection as HTMLElement).getByRole("button", { name: /提交/ }));
    expect(runtimeMocks.gitLocalCommit).toHaveBeenCalledWith({ cwd: "D:/demo", message: "Update UI" });
  });

  it("offers repository initialization when the workspace is not a git repository", async () => {
    const user = userEvent.setup();
    runtimeMocks.gitLocalStatus.mockRejectedValueOnce(new Error("fatal: not a git repository (or any of the parent directories): .git"));
    runtimeMocks.gitLocalInit.mockResolvedValueOnce({
      cwd: "D:/demo",
      repoRoot: "D:/demo",
      stdout: "Initialized empty Git repository",
      stderr: "",
      status: {
        cwd: "D:/demo",
        repoRoot: "D:/demo",
        branch: "main",
        dirtyFiles: 0,
        files: [],
        branches: [{ name: "main", current: true }],
        clean: true,
        rawStatus: "## main\n",
      },
    });

    render(<GitWorkspacePanel composerContext={{ cwd: "D:/demo", branch: "main" }} />);

    await screen.findByText(/not a git repository/i);
    await user.click(screen.getByRole("button", { name: /初始化/ }));
    expect(runtimeMocks.gitLocalInit).toHaveBeenCalledWith({ cwd: "D:/demo" });
  });
});
