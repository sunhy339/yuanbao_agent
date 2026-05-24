import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GlobalSidebar } from "./GlobalSidebar";

describe("GlobalSidebar", () => {
  it("marks only the selected session as current and uses timestamps for other active sessions", () => {
    render(
      <GlobalSidebar
        activeSessionId="sess_current"
        workspaceName="Workspace"
        sessions={[
          {
            id: "sess_current",
            workspaceId: "ws_1",
            title: "Current task",
            status: "active",
            createdAt: Date.UTC(2026, 4, 24, 10, 0),
            updatedAt: Date.UTC(2026, 4, 24, 10, 5),
          },
          {
            id: "sess_other",
            workspaceId: "ws_1",
            title: "Other task",
            status: "active",
            createdAt: Date.UTC(2026, 4, 24, 9, 0),
            updatedAt: Date.UTC(2026, 4, 24, 9, 30),
          },
        ]}
        onOpenSystemTab={vi.fn()}
        onOpenSessionTab={vi.fn()}
        onRenameSession={vi.fn()}
        onDeleteSession={vi.fn()}
      />,
    );

    expect(screen.getByText("当前")).toBeInTheDocument();
    expect(screen.queryByText("活跃")).not.toBeInTheDocument();
  });
});
