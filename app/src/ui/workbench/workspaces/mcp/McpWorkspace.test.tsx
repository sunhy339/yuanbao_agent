import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { McpWorkspace } from "./McpWorkspace";

afterEach(() => {
  cleanup();
});

describe("McpWorkspace", () => {
  const handlers = () => ({
    onRefreshServers: vi.fn(),
    onCreateServer: vi.fn().mockResolvedValue(undefined),
    onUpdateServer: vi.fn().mockResolvedValue(undefined),
    onToggleServer: vi.fn().mockResolvedValue(undefined),
    onRefreshTools: vi.fn().mockResolvedValue(undefined),
    onDeleteServer: vi.fn().mockResolvedValue(undefined),
  });

  it("creates and edits MCP servers through runtime callbacks", async () => {
    const user = userEvent.setup();
    const actions = handlers();
    render(
      <McpWorkspace
        servers={[
          {
            id: "srv_files",
            name: "filesystem",
            transport: "stdio",
            command: "npx",
            args: ["@modelcontextprotocol/server-filesystem", "D:/py/yuanbao_agent"],
            enabled: true,
            createdAt: 1,
            updatedAt: 2,
          },
        ]}
        {...actions}
      />,
    );

    await user.type(screen.getByLabelText("Name"), "search");
    await user.selectOptions(screen.getByLabelText("Transport"), "http");
    await user.type(screen.getByLabelText("URL"), "http://127.0.0.1:8787/sse");
    await user.click(screen.getByRole("button", { name: "Create server" }));

    expect(actions.onCreateServer).toHaveBeenCalledWith({
      name: "search",
      transport: "http",
      command: "",
      args: "",
      url: "http://127.0.0.1:8787/sse",
      enabled: true,
    });

    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "filesystem local");
    await user.click(screen.getByRole("button", { name: "Save server" }));

    expect(actions.onUpdateServer).toHaveBeenCalledWith(
      "srv_files",
      expect.objectContaining({
        name: "filesystem local",
        transport: "stdio",
        command: "npx",
        args: "@modelcontextprotocol/server-filesystem\nD:/py/yuanbao_agent",
      }),
    );
  });

  it("uses selected server actions for toggle, tool refresh, and delete", async () => {
    const user = userEvent.setup();
    const actions = handlers();
    render(
      <McpWorkspace
        servers={[
          {
            id: "srv_files",
            name: "filesystem",
            transport: "stdio",
            command: "npx",
            args: [],
            enabled: true,
          },
        ]}
        {...actions}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Disable" }));
    await user.click(screen.getByRole("button", { name: "Refresh tools" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));

    expect(actions.onToggleServer).toHaveBeenCalledWith("srv_files", false);
    expect(actions.onRefreshTools).toHaveBeenCalledWith("srv_files");
    expect(actions.onDeleteServer).toHaveBeenCalledWith("srv_files");
  });
});
