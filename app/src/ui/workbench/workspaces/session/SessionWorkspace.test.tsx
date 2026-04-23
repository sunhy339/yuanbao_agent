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
};

describe("SessionWorkspace", () => {
  it("renders a calm empty state when no session is selected", () => {
    render(
      <SessionWorkspace
        session={null}
        activeTask={null}
        messages={[]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Open or create a session" })).toBeInTheDocument();
    expect(screen.getByText(/Choose a session from the rail/i)).toBeInTheDocument();
  });

  it("renders the session title, status, and task count", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[]}
        taskCount={3}
      />,
    );

    expect(screen.getByRole("heading", { name: "Investigate runtime boot" })).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByText("3 tasks")).toBeInTheDocument();
  });

  it("renders user and assistant messages in the stream", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[
          { id: "m1", role: "user", content: "Check the current failing test." },
          { id: "m2", role: "assistant", content: "I found the failure in the session renderer.", streaming: true },
        ]}
      />,
    );

    expect(screen.getByText("Check the current failing test.")).toBeInTheDocument();
    expect(screen.getByText("I found the failure in the session renderer.")).toBeInTheDocument();
    expect(screen.getByText("Streaming")).toBeInTheDocument();
  });

  it("renders active task status and goal", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "running",
          goal: "Patch the session workspace",
          resultSummary: "Tests are being prepared.",
        }}
        messages={[]}
      />,
    );

    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("Patch the session workspace")).toBeInTheDocument();
    expect(screen.getByText("Tests are being prepared.")).toBeInTheDocument();
  });

  it("renders runtime approvals, patches, traces, and tool calls", () => {
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
            requestedAt: Date.UTC(2026, 3, 23, 4, 5),
          },
        ]}
        patches={[
          {
            id: "patch_1",
            summary: "Session runtime shelf",
            status: "ready",
            filesChanged: 2,
            updatedAt: Date.UTC(2026, 3, 23, 4, 10),
          },
        ]}
        traces={[
          {
            id: "trace_1",
            type: "activity",
            source: "runtime",
            summary: "Loaded command output",
            time: Date.UTC(2026, 3, 23, 4, 15),
          },
        ]}
        toolCalls={[
          {
            id: "tool_1",
            toolName: "shell_command",
            status: "completed",
            resultSummary: "Tests passed",
            durationMs: 1280,
          },
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Approvals" })).toBeInTheDocument();
    expect(screen.getByText("Allow npm test")).toBeInTheDocument();
    expect(screen.getByText("Run the focused session workspace test.")).toBeInTheDocument();
    expect(screen.getByText("command")).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Patches" })).toBeInTheDocument();
    expect(screen.getByText("Session runtime shelf")).toBeInTheDocument();
    expect(screen.getByText("2 files changed")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Trace and tools" })).toBeInTheDocument();
    expect(screen.getByText("Loaded command output")).toBeInTheDocument();
    expect(screen.getByText("shell_command")).toBeInTheDocument();
    expect(screen.getByText("Tests passed")).toBeInTheDocument();
    expect(screen.getByText("1.3s")).toBeInTheDocument();
  });

  it("calls runtime action handlers from shelf controls", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    const onReject = vi.fn();
    const onLoadPatch = vi.fn();
    const onRefreshTrace = vi.fn();

    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[]}
        approvals={[{ id: "approval_1", title: "Allow npm test", status: "pending" }]}
        patches={[{ id: "patch_1", summary: "Session runtime shelf", status: "ready" }]}
        traces={[{ id: "trace_1", type: "activity", summary: "Loaded command output" }]}
        toolCalls={[]}
        onApprove={onApprove}
        onReject={onReject}
        onLoadPatch={onLoadPatch}
        onRefreshTrace={onRefreshTrace}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Approve Allow npm test" }));
    await user.click(screen.getByRole("button", { name: "Reject Allow npm test" }));
    await user.click(screen.getByRole("button", { name: "Open patch Session runtime shelf" }));
    await user.click(screen.getByRole("button", { name: "Refresh trace" }));

    expect(onApprove).toHaveBeenCalledWith("approval_1");
    expect(onReject).toHaveBeenCalledWith("approval_1");
    expect(onLoadPatch).toHaveBeenCalledWith("patch_1");
    expect(onRefreshTrace).toHaveBeenCalledOnce();
  });
});
