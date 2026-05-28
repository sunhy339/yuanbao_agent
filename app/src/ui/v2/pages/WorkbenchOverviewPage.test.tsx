import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkbenchOverviewPage } from "./WorkbenchOverviewPage";

describe("WorkbenchOverviewPage", () => {
  it("uses useful recent-session badges instead of marking every active session as active", () => {
    render(
      <WorkbenchOverviewPage
        workspace={null}
        workspacePath="D:/work"
        providerLabel="gpt-5.4"
        runtimeStatus="ready"
        sessions={[
          {
            id: "sess_recent",
            workspaceId: "ws_1",
            title: "Recent work",
            status: "active",
            createdAt: 1,
            updatedAt: 3,
          },
          {
            id: "sess_continue",
            workspaceId: "ws_1",
            title: "Older work",
            status: "active",
            createdAt: 1,
            updatedAt: 2,
          },
        ]}
        tasks={[]}
        mcpServers={[]}
        skills={[]}
        onOpenNewSession={vi.fn()}
        onOpenSession={vi.fn()}
        onOpenMcp={vi.fn()}
      />,
    );

    const recentPanel = screen.getByText("对话通道").closest("section");
    expect(recentPanel).not.toBeNull();
    expect(within(recentPanel as HTMLElement).getByText("最近")).toBeInTheDocument();
    expect(within(recentPanel as HTMLElement).getByText("可继续")).toBeInTheDocument();
    expect(within(recentPanel as HTMLElement).queryByText("活跃")).not.toBeInTheDocument();
  });
});
