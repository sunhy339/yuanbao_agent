import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
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
    expect(screen.getByText(/begin chatting here/i)).toBeInTheDocument();
  });

  it("renders only the conversation area for an active session", () => {
    const { container } = render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "running",
          goal: "Patch the session workspace",
        }}
        composerContext={{
          cwd: "D:/py/yuanbao_agent",
          repo: "NanmiCoder/cc-haha",
          branch: "feat/dev-desktop",
          model: "MiniMax-M2.7-highspeed",
          permissionMode: "bypass",
        }}
        messages={[
          { id: "m1", role: "user", content: "Check the current failing test.", createdAt: 1 },
          { id: "m2", role: "assistant", content: "I found the failure in the session renderer.", streaming: true, createdAt: 4 },
          { id: "m3", role: "system", content: "Runtime resumed session state." },
          { id: "m4", role: "tool", toolName: "shell_command", status: "completed", content: "Tests passed." },
        ]}
        taskCount={3}
        approvals={[
          {
            id: "approval_1",
            title: "Allow npm test",
            status: "pending",
            kind: "shell",
            command: "npm test",
          },
        ]}
        patches={[
          {
            id: "patch_1",
            summary: "Updated session layout",
            status: "applied",
            filesChanged: 1,
            additions: 12,
            deletions: 4,
          },
        ]}
        traces={[
          {
            id: "trace_1",
            type: "provider.response",
            title: "Provider response",
            status: "completed",
          },
        ]}
        toolCalls={[
          {
            id: "tool_1",
            toolName: "apply_patch",
            status: "completed",
            resultSummary: "Patch applied.",
            time: 2,
          },
        ]}
        backgroundJobs={[
          {
            id: "job_1",
            command: "npm run typecheck",
            status: "completed",
            cwd: "D:/py/yuanbao_agent/app",
            startedAt: 3,
          },
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Investigate runtime boot" })).toBeInTheDocument();
    expect(screen.getByText("Check the current failing test.")).toBeInTheDocument();
    expect(screen.getByText("I found the failure in the session renderer.")).toBeInTheDocument();
    expect(screen.getByText("Runtime resumed session state.")).toBeInTheDocument();
    expect(screen.getByText("shell_command")).toBeInTheDocument();
    expect(screen.queryByLabelText("Runtime timeline")).not.toBeInTheDocument();
    expect(screen.queryByText("Patch the session workspace")).not.toBeInTheDocument();
    expect(screen.getByText("Allow npm test")).toBeInTheDocument();
    expect(screen.queryByText("Updated session layout")).not.toBeInTheDocument();
    expect(screen.queryByText("Provider response")).not.toBeInTheDocument();
    expect(screen.getByText("apply_patch")).toBeInTheDocument();
    expect(screen.getByText("npm run typecheck")).toBeInTheDocument();
    expect(screen.getByLabelText("Conversation activity")).toBeInTheDocument();
    const activityText = Array.from(container.querySelectorAll("[data-activity-kind]")).map((item) =>
      item.textContent ?? "",
    );
    expect(activityText[0]).toContain("Check the current failing test.");
    expect(activityText[1]).toContain("apply_patch");
    expect(activityText[2]).toContain("npm run typecheck");
    expect(activityText[3]).toContain("I found the failure in the session renderer.");

    expect(screen.queryByRole("heading", { name: "Active task" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Runtime shelf" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Collaboration" })).not.toBeInTheDocument();
    expect(screen.queryByText("MiniMax-M2.7-highspeed")).not.toBeInTheDocument();
  });

  it("renders a message empty state inside the conversation area", () => {
    render(<SessionWorkspace session={session} activeTask={null} messages={[]} />);

    expect(screen.getByRole("heading", { name: "No messages yet" })).toBeInTheDocument();
    expect(screen.getByText(/send the first message from the composer below/i)).toBeInTheDocument();
  });

  it("does not render a runtime divider when only chat messages are visible", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "你好", createdAt: 1 }]}
      />,
    );

    expect(screen.getByText("你好")).toBeInTheDocument();
    expect(screen.queryByLabelText("Runtime timeline")).not.toBeInTheDocument();
  });

  it("renders runtime cards collapsed by default and expands details on demand", async () => {
    const user = userEvent.setup();
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Run tests", createdAt: 1 }]}
        toolCalls={[
          {
            id: "tool_1",
            toolName: "run_command",
            status: "failed",
            resultSummary: "Command failed with exit 1.",
            argsPreview: '{"command":"npm test","cwd":"app"}',
            time: 2,
          },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: /Tool run_command failed/ })).toBeInTheDocument();
    expect(screen.queryByText("Command failed with exit 1.")).not.toBeInTheDocument();
    expect(screen.queryByText(/"command":"npm test"/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Tool run_command failed/ }));

    expect(screen.getByText("Command failed with exit 1.")).toBeInTheDocument();
    expect(screen.getByText(/"command":"npm test"/)).toBeInTheDocument();
  });

  it("shows pending approval actions so commands do not wait invisibly", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    const onReject = vi.fn();

    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "assistant", content: "需要确认执行。", createdAt: 1 }]}
        approvals={[
          {
            id: "approval_1",
            title: "apply_patch",
            status: "pending",
            kind: "apply_patch",
            command: "apply_patch",
          },
        ]}
        onApprove={onApprove}
        onReject={onReject}
      />,
    );

    await user.click(screen.getByRole("button", { name: "批准 apply_patch" }));
    await user.click(screen.getByRole("button", { name: "拒绝 apply_patch" }));

    expect(onApprove).toHaveBeenCalledWith("approval_1");
    expect(onReject).toHaveBeenCalledWith("approval_1");
  });
});
