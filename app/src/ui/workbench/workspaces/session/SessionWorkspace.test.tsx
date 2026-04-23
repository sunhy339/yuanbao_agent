import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionWorkspace } from "./SessionWorkspace";

afterEach(() => {
  cleanup();
});

const session = {
  id: "sess_1",
  title: "Investigate runtime boot",
  status: "active",
  updatedAt: Date.UTC(2026, 3, 23, 3, 30),
  tokenCount: 36115,
};

describe("SessionWorkspace", () => {
  it("renders a calm empty state when no session is selected", () => {
    render(<SessionWorkspace session={null} activeTask={null} messages={[]} />);

    expect(screen.getByRole("heading", { name: "Open or create a session" })).toBeInTheDocument();
    expect(screen.getByText(/approvals, patches, and runtime trace/i)).toBeInTheDocument();
  });

  it("renders session metadata, composer context, messages, and plan steps", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "running",
          goal: "Patch the session workspace",
          resultSummary: "Tests are being prepared.",
          planSteps: [
            {
              id: "step_1",
              title: "Inspect old runtime events",
              status: "done",
              summary: "Mapped approvals and patches.",
              durationMs: 1280,
            },
          ],
        }}
        composerContext={{
          cwd: "D:/py/yuanbao_agent",
          repo: "NanmiCoder/cc-haha",
          branch: "feat/dev-desktop",
          model: "MiniMax-M2.7-highspeed",
          permissionMode: "bypass",
        }}
        messages={[
          { id: "m1", role: "user", content: "Check the current failing test." },
          { id: "m2", role: "assistant", content: "I found the failure in the session renderer.", streaming: true },
          { id: "m3", role: "system", content: "Runtime resumed session state." },
          { id: "m4", role: "tool", toolName: "shell_command", status: "completed", content: "Tests passed." },
        ]}
        taskCount={3}
      />,
    );

    expect(screen.getByRole("heading", { name: "Investigate runtime boot" })).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByText("3 tasks")).toBeInTheDocument();
    expect(screen.getByText("36,115")).toBeInTheDocument();
    expect(screen.getByText("D:/py/yuanbao_agent")).toBeInTheDocument();
    expect(screen.getByText("MiniMax-M2.7-highspeed")).toBeInTheDocument();
    expect(screen.getByText("I found the failure in the session renderer.")).toBeInTheDocument();
    expect(screen.getByText("Runtime resumed session state.")).toBeInTheDocument();
    expect(screen.getByText("shell_command")).toBeInTheDocument();
    expect(screen.getByText("Inspect old runtime events")).toBeInTheDocument();
    expect(screen.getByText("1.3s")).toBeInTheDocument();
  });

  it("renders a compact collaboration lane with workers, claimed tasks, and latest results", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[]}
        collaboration={{
          workers: [
            {
              id: "worker_1",
              name: "Planner",
              status: "active",
              mode: "analysis",
              claimedTaskId: "child_1",
            },
          ],
          childTasks: [
            {
              id: "child_1",
              title: "Refine session lane",
              status: "claimed",
              workerId: "worker_1",
              summary: "Focus on the read-only worker rail.",
            },
          ],
          results: [
            {
              id: "result_1",
              taskId: "child_1",
              status: "done",
              summary: "Lane draft ready.",
              updatedAt: Date.UTC(2026, 3, 23, 3, 40),
            },
          ],
        }}
      />,
    );

    expect(screen.getByRole("heading", { name: "Collaboration" })).toBeInTheDocument();
    expect(screen.getByText("Planner")).toBeInTheDocument();
    expect(screen.getAllByText("Refine session lane")).toHaveLength(2);
    expect(screen.getByText("Lane draft ready.")).toBeInTheDocument();
  });

  it("handles approval buttons and full input disclosure", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    const onApproveForSession = vi.fn();
    const onReject = vi.fn();

    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[]}
        approvals={[
          {
            id: "approval_1",
            title: "Allow npm test",
            kind: "command",
            status: "pending",
            summary: "Run the focused session workspace test.",
            parametersPreview: "npm run test -- SessionWorkspace",
            fullInput: '{"command":"npm run test -- SessionWorkspace","cwd":"D:/py/yuanbao_agent"}',
            cwd: "D:/py/yuanbao_agent",
          },
        ]}
        onApprove={onApprove}
        onApproveForSession={onApproveForSession}
        onReject={onReject}
      />,
    );

    expect(screen.getByText("Run the focused session workspace test.")).toBeInTheDocument();
    expect(screen.getByLabelText("Allow npm test parameters preview")).toHaveTextContent("npm run test");

    await user.click(screen.getByRole("button", { name: "Show full input" }));
    expect(screen.getByLabelText("Allow npm test full input")).toHaveTextContent('"cwd":"D:/py/yuanbao_agent"');

    await user.click(screen.getByRole("button", { name: "Allow Allow npm test" }));
    await user.click(screen.getByRole("button", { name: "Allow Allow npm test for session" }));
    await user.click(screen.getByRole("button", { name: "Deny Allow npm test" }));

    expect(onApprove).toHaveBeenCalledWith("approval_1");
    expect(onApproveForSession).toHaveBeenCalledWith("approval_1");
    expect(onReject).toHaveBeenCalledWith("approval_1");
  });

  it("expands patch cards, copies paths, and loads details", async () => {
    const user = userEvent.setup();
    const onLoadPatch = vi.fn();
    const onCopyPatchPath = vi.fn();

    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[]}
        patches={[
          {
            id: "patch_1",
            summary: "Session runtime shelf",
            status: "ready",
            filesChanged: 1,
            additions: 12,
            deletions: 3,
            files: [
              {
                path: "app/src/ui/workbench/workspaces/session/SessionWorkspace.tsx",
                status: "modified",
                additions: 12,
                deletions: 3,
                diff: "+ added runtime shelf\n- old shelf",
              },
            ],
          },
        ]}
        onLoadPatch={onLoadPatch}
        onCopyPatchPath={onCopyPatchPath}
      />,
    );

    expect(screen.queryByText("+ added runtime shelf")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Expand patch" }));

    expect(screen.getByText("app/src/ui/workbench/workspaces/session/SessionWorkspace.tsx")).toBeInTheDocument();
    expect(screen.getByLabelText("Session runtime shelf diff")).toHaveTextContent("+ added runtime shelf");

    await user.click(screen.getByRole("button", { name: /Copy path app\/src\/ui\/workbench\/workspaces\/session\/SessionWorkspace\.tsx/i }));
    await user.click(screen.getByRole("button", { name: "Load patch details Session runtime shelf" }));

    expect(onCopyPatchPath).toHaveBeenCalledWith(
      "patch_1",
      "app/src/ui/workbench/workspaces/session/SessionWorkspace.tsx",
    );
    expect(onLoadPatch).toHaveBeenCalledWith("patch_1");
  });

  it("expands trace and tool cards with runtime details", async () => {
    const user = userEvent.setup();
    const onRefreshTrace = vi.fn();

    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[]}
        traces={[
          {
            id: "trace_1",
            type: "thinking",
            source: "runtime",
            title: "Thinking",
            summary: "Planning next action",
            detail: "Need to inspect the focused component before editing.",
            durationMs: 410,
            tokenCount: 5817,
            stdout: "planner ready",
            stderr: "warning: stale snapshot",
          },
        ]}
        toolCalls={[
          {
            id: "tool_1",
            toolName: "shell_command",
            status: "completed",
            resultSummary: "Tests passed",
            durationMs: 1280,
            tokenCount: 80,
            argsPreview: '{"command":"npm test"}',
            output: "PASS SessionWorkspace.test.tsx",
          },
        ]}
        onRefreshTrace={onRefreshTrace}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Refresh trace" }));
    expect(onRefreshTrace).toHaveBeenCalledOnce();

    const traceCard = screen.getByText("Thinking").closest("article");
    expect(traceCard).not.toBeNull();
    await user.click(within(traceCard as HTMLElement).getByRole("button", { name: "Expand trace" }));
    expect(screen.getByText("Need to inspect the focused component before editing.")).toBeInTheDocument();
    expect(screen.getByLabelText("Thinking stdout")).toHaveTextContent("planner ready");
    expect(screen.getByLabelText("Thinking stderr")).toHaveTextContent("stale snapshot");

    const toolCard = screen.getByText("shell_command").closest("article");
    expect(toolCard).not.toBeNull();
    await user.click(within(toolCard as HTMLElement).getByRole("button", { name: "Expand tool" }));
    expect(screen.getByLabelText("shell_command args preview")).toHaveTextContent('"command":"npm test"');
    expect(screen.getByLabelText("shell_command output")).toHaveTextContent("PASS SessionWorkspace.test.tsx");
    expect(screen.getByText("1.3s")).toBeInTheDocument();
    expect(screen.getByText("5,817 tokens")).toBeInTheDocument();
  });
});
