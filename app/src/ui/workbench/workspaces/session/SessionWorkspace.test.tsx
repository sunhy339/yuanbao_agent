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
  summary: undefined,
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
    expect(screen.getByText("Patch the session workspace")).toBeInTheDocument();
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

  it("renders assistant markdown as structured content", () => {
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[
          {
            id: "m1",
            role: "assistant",
            content: "## Can do\n- **Read files**\n- Run `npm test`\n\n| Tool | Use |\n| --- | --- |\n| list_dir | Browse |",
            createdAt: 1,
          },
        ]}
      />,
    );

    expect(screen.getByRole("heading", { name: "Can do" })).toBeInTheDocument();
    expect(screen.getByText("Read files")).toBeInTheDocument();
    expect(screen.getByText("npm test")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Tool" })).toBeInTheDocument();
    expect(screen.queryByText(/## Can do/)).not.toBeInTheDocument();
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
            argsPreview: "执行 npm test",
            rawInput: '{"command":"npm test","cwd":"app"}',
            time: 2,
          },
        ]}
      />,
    );

    expect(screen.getByRole("button", { name: /Bash npm test failed/ })).toBeInTheDocument();
    expect(screen.getByText("Command failed with exit 1.")).toBeInTheDocument();
    expect(screen.queryByText(/"command":"npm test"/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Bash npm test failed/ }));

    expect(screen.getByText("Command failed with exit 1.")).toBeInTheDocument();
    expect(screen.getByText("查看原始数据")).toBeInTheDocument();
    expect(screen.getByText(/"command":"npm test"/)).not.toBeVisible();
  });

  it("keeps raw tool JSON behind a secondary details control", async () => {
    const user = userEvent.setup();
    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "List files", createdAt: 1 }]}
        toolCalls={[
          {
            id: "tool_1",
            toolName: "list_dir",
            status: "completed",
            resultSummary: "Found 2 items: app, docs",
            argsPreview: "列出 .",
            input: "列出 .",
            rawInput: '{"ignore":["node_modules"],"path":"."}',
            rawOutput: '{"items":[{"name":"app","type":"directory"}]}',
            time: 2,
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Tool list_dir \. completed/ }));

    expect(screen.getByText("Found 2 items: app, docs")).toBeInTheDocument();
    expect(screen.getAllByText("列出 .").length).toBeGreaterThan(0);
    expect(screen.getByText(/node_modules/)).not.toBeVisible();

    await user.click(screen.getByText("查看原始数据"));

    expect(screen.getByText(/node_modules/)).toBeVisible();
  });

  it("renders active task execution progress as compact readable cards", async () => {
    const user = userEvent.setup();

    render(
      <SessionWorkspace
        session={session}
        activeTask={{
          id: "task_1",
          status: "verifying",
          goal: "Create a pixel-art image tool",
          currentStep: "Run the generated CLI against a sample image",
          acceptanceCriteria: ["Script exists", "Help command works"],
          outOfScope: ["No GUI in this pass"],
          changedFiles: [
            {
              path: "tools/bead_art_generator.py",
              status: "added",
              additions: 148,
              deletions: 0,
              reason: "Created the CLI entry point",
              patchId: "patch_1",
            },
          ],
          commands: [
            {
              id: "cmd_1",
              command: "python tools/bead_art_generator.py --help",
              cwd: "D:/py/yuanbao_agent",
              status: "completed",
              exitCode: 0,
              durationMs: 312,
              summary: "Help text printed.",
            },
          ],
          verification: [
            {
              id: "verify_1",
              command: "python tools/bead_art_generator.py --help",
              status: "passed",
              exitCode: 0,
              durationMs: 312,
              summary: "CLI help is available.",
            },
          ],
        }}
        messages={[{ id: "m1", role: "user", content: "Build the tool", createdAt: 1 }]}
      />,
    );

    expect(screen.getByRole("button", { name: /Task Task focus verifying/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Task Changed files recorded/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Bash Command runs completed/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Task Verification passed/ })).toBeInTheDocument();
    expect(screen.queryByText(/sessionId/)).not.toBeInTheDocument();
    expect(screen.queryByText(/taskId/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Task Task focus verifying/ }));
    expect(screen.getByText("Run the generated CLI against a sample image")).toBeInTheDocument();
    expect(screen.getByText(/Acceptance/)).toHaveTextContent("Script exists");

    await user.click(screen.getByRole("button", { name: /Task Changed files recorded/ }));
    expect(screen.getAllByText(/tools\/bead_art_generator.py/).at(-1)).toHaveTextContent("+148");

    await user.click(screen.getByRole("button", { name: /Bash Command runs completed/ }));
    expect(screen.getByText(/python tools\/bead_art_generator.py --help/)).toHaveTextContent("exit 0");

    await user.click(screen.getByRole("button", { name: /Task Verification passed/ }));
    expect(screen.getByText(/CLI help is available/)).toHaveTextContent("passed");
  });

  it("keeps session memory visible but collapsed by default", async () => {
    const user = userEvent.setup();

    render(
      <SessionWorkspace
        session={{
          ...session,
          summary:
            "Task memory:\n- completed: add a focused project checklist\n  result: Created the checklist and verified it.",
        }}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Continue", createdAt: 1 }]}
      />,
    );

    expect(screen.getByRole("button", { name: /Memory Session memory recorded/ })).toBeInTheDocument();
    expect(screen.queryByText(/add a focused project checklist/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Memory Session memory recorded/ }));

    expect(screen.getByText(/add a focused project checklist/)).toBeInTheDocument();
    expect(screen.getByText(/Created the checklist/)).toBeInTheDocument();
  });

  it("shows a compact context preview with focus memory and budget details", async () => {
    const user = userEvent.setup();

    render(
      <SessionWorkspace
        session={session}
        activeTask={null}
        messages={[{ id: "m1", role: "user", content: "Continue", createdAt: 1 }]}
        contextPreview={{
          projectFocus: "Keep attention on large-project iteration.",
          projectMemory: "Project memory:\n- completed: task run UI V1",
          workspaceRoot: "D:/py/yuanbao_agent",
          searchMode: "content",
          searchQuery: "context preview",
          toolCount: 8,
          budgetStats: {
            estimatedInputTokens: 6200,
            messageTokens: 2200,
            toolSchemaTokens: 4000,
            maxContextTokens: 8000,
            droppedSections: ["patch_diff:old"],
            trimmedSections: ["session_summary"],
          },
          taskFocus: {
            currentStep: "Inspect current context handoff",
            acceptanceCriteriaCount: 3,
            outOfScopeCount: 2,
          },
        }}
      />,
    );

    expect(screen.getByRole("button", { name: /Context preview/ })).toBeInTheDocument();
    expect(screen.getByText("Focus active")).toBeInTheDocument();
    expect(screen.getByText("Project memory")).toBeInTheDocument();
    expect(screen.getByText("6200/8000 tokens")).toBeInTheDocument();
    expect(screen.queryByText(/Keep attention on large-project iteration/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Context preview/ }));

    expect(screen.getByText(/Keep attention on large-project iteration/)).toBeInTheDocument();
    expect(screen.getByText(/task run UI V1/)).toBeInTheDocument();
    expect(screen.getByText(/session_summary/)).toHaveTextContent("Trimmed");
    expect(screen.getByText(/patch_diff:old/)).toHaveTextContent("Dropped");
    expect(screen.getByText(/Inspect current context handoff/)).toBeInTheDocument();
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
