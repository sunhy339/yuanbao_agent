import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { McpWorkspace } from "./McpWorkspace";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("McpWorkspace", () => {
  const handlers = () => ({
    onRefreshServers: vi.fn(),
    onCreateServer: vi.fn().mockResolvedValue(undefined),
    onImportServers: vi.fn().mockResolvedValue(undefined),
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
      headers: "",
      env: "",
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

  it("imports mcpServers JSON with args and env", async () => {
    const user = userEvent.setup();
    const actions = handlers();
    render(<McpWorkspace servers={[]} {...actions} />);

    fireEvent.change(screen.getByLabelText("MCP JSON"), {
      target: {
        value: JSON.stringify({
          mcpServers: {
            "firecrawl-mcp": {
              command: "npx",
              args: ["-y", "firecrawl-mcp"],
              env: { FIRECRAWL_API_KEY: "secret" },
            },
          },
        }),
      },
    });
    await user.click(screen.getByRole("button", { name: "Import JSON" }));

    expect(actions.onImportServers).toHaveBeenCalledWith([
      expect.objectContaining({
        name: "firecrawl-mcp",
        transport: "stdio",
        command: "npx",
        args: "-y\nfirecrawl-mcp",
        env: "FIRECRAWL_API_KEY=secret",
        enabled: true,
      }),
    ]);
  });

  it("uses selected server actions for toggle, tool refresh, and delete", async () => {
    const user = userEvent.setup();
    const actions = handlers();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
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
    expect(confirm).toHaveBeenCalledWith("删除 MCP 服务器“filesystem”？");
    expect(actions.onDeleteServer).toHaveBeenCalledWith("srv_files");
  });

  it("requires transport-specific fields before submitting MCP servers", async () => {
    const user = userEvent.setup();
    const actions = handlers();
    render(<McpWorkspace servers={[]} {...actions} />);

    await user.type(screen.getByLabelText("名称"), "missing command");

    expect(screen.getByText("请输入启动命令。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建服务器" })).toBeDisabled();
    expect(actions.onCreateServer).not.toHaveBeenCalled();

    await user.selectOptions(screen.getByLabelText("传输方式"), "http");

    expect(screen.getByText("请输入服务器 URL。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建服务器" })).toBeDisabled();
  });

  it("filters MCP servers by search text and enabled state", async () => {
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
            args: ["@modelcontextprotocol/server-filesystem"],
            enabled: true,
          },
          {
            id: "srv_web",
            name: "web-search",
            transport: "http",
            url: "http://127.0.0.1:8787/sse",
            enabled: false,
          },
        ]}
        {...actions}
      />,
    );

    const registry = screen.getByRole("heading", { name: "运行时注册表" }).closest("section");
    expect(registry).not.toBeNull();

    await user.type(screen.getByRole("textbox", { name: "搜索 MCP 服务器" }), "web");

    expect(within(registry as HTMLElement).queryByText("filesystem")).not.toBeInTheDocument();
    expect(within(registry as HTMLElement).getByText("web-search")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "启用" }));

    expect(within(registry as HTMLElement).queryByText("web-search")).not.toBeInTheDocument();
    expect(within(registry as HTMLElement).getByText("没有匹配的 MCP 服务器")).toBeInTheDocument();
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
    await user.type(screen.getByLabelText("命令"), "npx");
    await user.click(screen.getByRole("button", { name: "创建服务器" }));

    expect(screen.getByRole("alert", { name: "MCP 错误" })).toHaveTextContent("Command is required");
    expect(screen.getByLabelText("名称")).toHaveValue("broken server");

    await user.click(screen.getByRole("button", { name: "关闭" }));

    expect(onDismissError).toHaveBeenCalledTimes(1);
  });
});
