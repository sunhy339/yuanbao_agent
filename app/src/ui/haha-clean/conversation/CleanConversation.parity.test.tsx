import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { CleanSpecialEventBlock, CleanToolMessageBlock } from "./CleanConversation";

describe("CleanConversation parity", () => {
  it("renders tool display fields without exposing raw argument JSON", async () => {
    const user = userEvent.setup();

    render(
      <CleanToolMessageBlock
        message={{
          id: "tool-read",
          role: "assistant",
          content: "read src/app.ts",
          toolName: "read_file",
          status: "completed",
          metadata: {
            kind: "tool_use",
            displayTitle: "Read file",
            displaySummary: "Read app shell",
            displayTarget: "src/app.ts",
            inputText: "read src/app.ts",
            rawInputText: JSON.stringify({ path: "src/app.ts", workspaceRoot: "D:/py/yuanbao_agent" }),
            input: { path: "src/app.ts", workspaceRoot: "D:/py/yuanbao_agent" },
            resultSummary: "Read app shell summary",
            resultPreview: [{ label: "File", value: "src/app.ts" }],
          },
        }}
      />,
    );

    expect(screen.getByRole("button", { name: /Read file/ })).toBeInTheDocument();
    expect(screen.getByText("src/app.ts")).toBeInTheDocument();
    expect(screen.queryByText(/workspaceRoot/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Read file/ }));
    expect(screen.getByText("File")).toBeInTheDocument();
    expect(screen.queryByText(/workspaceRoot/)).not.toBeInTheDocument();
  });

  it("keeps ask-user tool rows readable without raw option JSON", () => {
    render(
      <CleanToolMessageBlock
        message={{
          id: "tool-ask",
          role: "assistant",
          content: JSON.stringify({ options: [{ label: "status list" }, { label: "priority list" }] }),
          toolName: "ask_user_question",
          status: "completed",
          metadata: {
            inputText: JSON.stringify({
              question: "Which task list do you want?",
              options: [
                { label: "status list", description: "Group by done / doing / todo." },
                { label: "priority list", description: "Sort by priority and next step." },
              ],
            }),
            resultText: JSON.stringify({
              status: "waiting_user",
              requestId: "ask_123",
            }),
            requestId: "ask_123",
            question: "Which task list do you want?",
          },
        }}
      />,
    );

    expect(screen.getByRole("button", { name: /Which task list do you want/ })).toBeInTheDocument();
    expect(screen.getByText("Which task list do you want?")).toBeInTheDocument();
    expect(screen.queryByText(/options/)).not.toBeInTheDocument();
    expect(screen.queryByText(/requestId/)).not.toBeInTheDocument();
  });

  it("hides internal special-event payload keys from expanded details", async () => {
    const user = userEvent.setup();

    render(
      <CleanSpecialEventBlock
        transcriptKind="plan_update"
        message={{
          id: "plan-update-1",
          role: "assistant",
          content: "Working on the task list",
          metadata: {
            kind: "plan_update",
            title: "Plan update",
            summary: "Organizing current task list",
            status: "running",
            activeStep: "locating files",
            completedSteps: 1,
            _chatCompat: true,
            fingerprint: "inspect_workspace|completed",
            taskId: "task_1",
          },
        }}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Plan update/ }));
    const expanded = document.querySelector(".hc-special-event") as HTMLElement;
    expect(within(expanded).getByText((_, node) => node?.tagName === "PRE" && node.textContent?.includes("Working on the task list") === true)).toBeInTheDocument();
    expect(within(expanded).queryByText(/activeStep/)).not.toBeInTheDocument();
    expect(within(expanded).queryByText(/_chatCompat/)).not.toBeInTheDocument();
    expect(within(expanded).queryByText(/fingerprint/)).not.toBeInTheDocument();
  });
});
