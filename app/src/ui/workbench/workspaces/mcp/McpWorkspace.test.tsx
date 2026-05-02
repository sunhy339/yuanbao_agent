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

    await user.type(screen.getByLabelText("名称"), "search");
    await user.selectOptions(screen.getByLabelText("传输方式"), "http");
    await user.type(screen.getByLabelText("URL"), "http://127.0.0.1:8787/sse");
    await user.click(screen.getByRole("button", { name: "创建服务器" }));

    expect(actions.onCreateServer).toHaveBeenCalledWith({
      name: "search",
      transport: "http",
      command: "",
      args: "",
      url: "http://127.0.0.1:8787/sse",
      enabled: true,
    });

    await user.click(screen.getByRole("button", { name: "编辑" }));
    await user.clear(screen.getByLabelText("名称"));
    await user.type(screen.getByLabelText("名称"), "filesystem local");
    await user.click(screen.getByRole("button", { name: "保存服务器" }));

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

    await user.click(screen.getByRole("button", { name: "停用" }));
    await user.click(screen.getByRole("button", { name: "刷新工具" }));
    await user.click(screen.getByRole("button", { name: "删除" }));

    expect(actions.onToggleServer).toHaveBeenCalledWith("srv_files", false);
    expect(actions.onRefreshTools).toHaveBeenCalledWith("srv_files");
    expect(actions.onDeleteServer).toHaveBeenCalledWith("srv_files");
  });

  it("shows persistent errors and keeps the draft when create fails", async () => {
    const user = userEvent.setup();
    const actions = handlers();
    actions.onCreateServer.mockRejectedValue(new Error("Command is required"));
    const onDismissError = vi.fn();

    render(
      <McpWorkspace
        servers={[]}
        errorMessage="Command is required"
        onDismissError={onDismissError}
        {...actions}
      />,
    );

    await user.type(screen.getByLabelText("名称"), "broken server");
    await user.click(screen.getByRole("button", { name: "创建服务器" }));

    expect(screen.getByRole("alert", { name: "MCP 错误" })).toHaveTextContent("Command is required");
    expect(screen.getByLabelText("名称")).toHaveValue("broken server");

    await user.click(screen.getByRole("button", { name: "关闭" }));

    expect(onDismissError).toHaveBeenCalledTimes(1);
  });
});
