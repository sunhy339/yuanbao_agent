import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ScheduledWorkspace,
  type ExecutionLog,
  type ScheduledTask,
} from "./ScheduledWorkspace";

afterEach(() => {
  cleanup();
});

const tasks: ScheduledTask[] = [
  {
    id: "morning",
    title: "Morning sync",
    description: "Collect yesterday's notes.",
    status: "active",
    scheduleText: "Every day 08:30",
    lastRunText: "Last run: today 08:30",
  },
  {
    id: "archive",
    title: "Archive records",
    status: "disabled",
    scheduleText: "Paused",
  },
  {
    id: "cleanup",
    title: "Clean temp folders",
    status: "failed",
    scheduleText: "Every 4 hours",
  },
];

const logsByTaskId: Record<string, ExecutionLog[]> = {
  morning: [
    {
      id: "morning-log",
      time: "08:30",
      result: "completed",
      message: "Morning sync completed.",
    },
  ],
  cleanup: [
    {
      id: "cleanup-log",
      time: "12:00",
      result: "failed",
      message: "Cleanup failed on locked files.",
    },
  ],
};

describe("ScheduledWorkspace", () => {
  it("renders task metrics from provided tasks", () => {
    const html = renderToStaticMarkup(<ScheduledWorkspace tasks={tasks} />);

    expect(html).toMatch(/Scheduled Tasks/);
    expect(html).toMatch(/Total[\s\S]*3/);
    expect(html).toMatch(/Active[\s\S]*1/);
    expect(html).toMatch(/Paused[\s\S]*1/);
  });

  it("renders the workbench regions used by the scheduled task page", () => {
    render(<ScheduledWorkspace tasks={tasks} logsByTaskId={logsByTaskId} />);

    expect(screen.getByRole("region", { name: "Schedule command strip" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Scheduled tasks" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Execution log" })).toBeInTheDocument();
    expect(screen.getByText("Runtime schedule")).toBeInTheDocument();
    expect(screen.getByText("Run tail")).toBeInTheDocument();
  });

  it("renders a real empty state when no tasks are provided", () => {
    render(<ScheduledWorkspace />);

    expect(screen.getByText("No scheduled tasks")).toBeInTheDocument();
    expect(screen.queryByText("Morning sync")).not.toBeInTheDocument();
    expect(screen.getByText(/0 items/)).toBeInTheDocument();
    expect(screen.getByText("Select a task to see recent execution logs.")).toBeInTheDocument();
  });

  it("renders scheduled task list item details", () => {
    const html = renderToStaticMarkup(<ScheduledWorkspace tasks={[tasks[0]]} />);

    expect(html).toMatch(/Morning sync/);
    expect(html).toMatch(/Collect yesterday&#x27;s notes\./);
    expect(html).toMatch(/Every day 08:30/);
    expect(html).toMatch(/Last run: today 08:30/);
    expect(html).toMatch(/active/);
  });

  it("selects a task and notifies the host", async () => {
    const user = userEvent.setup();
    const onSelectTask = vi.fn();
    render(
      <ScheduledWorkspace
        tasks={tasks}
        logsByTaskId={logsByTaskId}
        onSelectTask={onSelectTask}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Select task Archive records/ }));

    expect(onSelectTask).toHaveBeenCalledWith("archive");
    expect(screen.getByRole("button", { name: /Select task Archive records/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("renders logs for the selected task from logsByTaskId", () => {
    render(
      <ScheduledWorkspace
        tasks={tasks}
        selectedTaskId="cleanup"
        logsByTaskId={logsByTaskId}
      />,
    );

    expect(screen.getByText("Cleanup failed on locked files.")).toBeInTheDocument();
    expect(screen.queryByText("Morning sync completed.")).not.toBeInTheDocument();
  });

  it("renders flat logs for the selected task when logs is provided", () => {
    render(
      <ScheduledWorkspace
        tasks={tasks}
        selectedTaskId="morning"
        logs={[
          {
            id: "flat-log",
            taskId: "morning",
            time: "09:00",
            result: "completed",
            message: "Flat log source rendered.",
          },
          {
            id: "other-log",
            taskId: "cleanup",
            time: "10:00",
            result: "failed",
            message: "Other task log is hidden.",
          },
        ]}
      />,
    );

    expect(screen.getByText("Flat log source rendered.")).toBeInTheDocument();
    expect(screen.queryByText("Other task log is hidden.")).not.toBeInTheDocument();
  });

  it("calls run and toggle callbacks", async () => {
    const user = userEvent.setup();
    const onCreateTask = vi.fn();
    const onRunTask = vi.fn();
    const onToggleTask = vi.fn();
    render(
      <ScheduledWorkspace
        tasks={tasks}
        onCreateTask={onCreateTask}
        onRunTask={onRunTask}
        onToggleTask={onToggleTask}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Run task Morning sync" }));
    await user.click(screen.getByRole("button", { name: "Disable task Morning sync" }));
    await user.click(screen.getByRole("button", { name: "Enable task Archive records" }));

    expect(onCreateTask).not.toHaveBeenCalled();
    expect(onRunTask).toHaveBeenCalledWith("morning");
    expect(onToggleTask).toHaveBeenCalledWith("morning");
    expect(onToggleTask).toHaveBeenCalledWith("archive");
  });

  it("opens a scheduled task dialog and submits the draft", async () => {
    const user = userEvent.setup();
    const onCreateTask = vi.fn();
    render(<ScheduledWorkspace onCreateTask={onCreateTask} workspacePath="D:\\py\\yuanbao_agent" />);

    await user.click(screen.getByRole("button", { name: "Create scheduled task" }));

    expect(screen.getByRole("dialog", { name: "Create scheduled task" })).toBeInTheDocument();
    expect(screen.getByText("Local scheduled tasks run when the runtime host is awake.")).toBeInTheDocument();
    expect(screen.getAllByText(/yuanbao_agent/)).toHaveLength(2);

    await user.type(screen.getByLabelText("Name"), "daily-code-review");
    await user.type(screen.getByLabelText("Description"), "Review yesterday's commits");
    await user.type(
      screen.getByLabelText("Prompt"),
      "Check the repository status and summarize anything that needs attention.",
    );
    await user.selectOptions(screen.getByLabelText("Frequency"), "every 24 hours");
    await user.click(screen.getByRole("button", { name: "Create task" }));

    expect(onCreateTask).toHaveBeenCalledWith({
      name: "daily-code-review",
      description: "Review yesterday's commits",
      prompt: "Check the repository status and summarize anything that needs attention.",
      schedule: "every 24 hours",
      enabled: true,
    });
  });

  it("marks the busy task controls as disabled", () => {
    render(
      <ScheduledWorkspace
        tasks={tasks}
        busyTaskId="morning"
        onRunTask={vi.fn()}
        onToggleTask={vi.fn()}
      />,
    );

    const row = screen.getByRole("listitem", { name: /Morning sync/ });
    expect(within(row).getByRole("button", { name: "Run task Morning sync" })).toBeDisabled();
    expect(within(row).getByRole("button", { name: "Disable task Morning sync" })).toBeDisabled();
    expect(within(row).getByText("Working")).toBeInTheDocument();
  });
});
