import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsWorkspace } from "./SettingsWorkspace";

afterEach(() => {
  cleanup();
});

describe("SettingsWorkspace", () => {
  const providers = [
    {
      id: "primary",
      name: "Primary Provider",
      endpoint: "https://primary.example.com",
      note: "Primary runtime",
      models: ["primary-chat"],
      status: "ready",
    },
    {
      id: "backup",
      name: "Backup Provider",
      endpoint: "https://backup.example.com",
      note: "Backup runtime",
      models: ["backup-chat"],
      status: "standby",
    },
  ];

  it("renders the providers section by default with fallback providers", () => {
    render(<SettingsWorkspace />);

    expect(
      screen.getByRole("heading", { name: "服务商设置" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "添加服务商" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /DeepSeek/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("switches to the permissions section", async () => {
    const user = userEvent.setup();
    render(<SettingsWorkspace />);

    await user.click(screen.getByRole("button", { name: "权限" }));

    expect(screen.getByRole("heading", { name: "权限模式" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /访问权限/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /跳过全部/ })).toBeInTheDocument();
  });

  it("opens and closes the provider modal", async () => {
    const user = userEvent.setup();
    render(<SettingsWorkspace />);

    await user.click(screen.getByRole("button", { name: "添加服务商" }));

    expect(screen.getByRole("dialog", { name: "添加服务商" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "DeepSeek" })).toBeInTheDocument();
    expect(screen.getByLabelText("API 密钥")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "取消" }));

    expect(
      screen.queryByRole("dialog", { name: "添加服务商" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "添加服务商" }));
    await user.click(screen.getByRole("button", { name: "关闭添加服务商" }));

    expect(
      screen.queryByRole("dialog", { name: "添加服务商" }),
    ).not.toBeInTheDocument();
  });

  it("calls the provider selection callback when a provider is selected", async () => {
    const user = userEvent.setup();
    const onSelectProvider = vi.fn();
    render(
      <SettingsWorkspace
        providers={providers}
        activeProviderId="primary"
        onSelectProvider={onSelectProvider}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Backup Provider/ }));

    expect(onSelectProvider).toHaveBeenCalledWith("backup");
    expect(screen.getByRole("button", { name: /Backup Provider/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("submits provider form values through the add callback and closes the modal", async () => {
    const user = userEvent.setup();
    const onAddProvider = vi.fn();
    render(<SettingsWorkspace onAddProvider={onAddProvider} />);

    await user.click(screen.getByRole("button", { name: "添加服务商" }));

    const dialog = screen.getByRole("dialog", { name: "添加服务商" });
    await user.type(within(dialog).getByLabelText("名称"), "Acme AI");
    await user.type(within(dialog).getByLabelText("备注"), "Workbench provider");
    await user.type(
      within(dialog).getByLabelText("接口地址"),
      "https://api.acme.example/v1",
    );
    await user.type(within(dialog).getByLabelText("API 密钥"), "sk-test");
    await user.type(within(dialog).getByLabelText("model mapping"), "chat=acme-chat");
    await user.type(within(dialog).getByLabelText("test connection"), "GET /models");
    await user.click(within(dialog).getByLabelText("JSON config"));
    await user.paste('{"timeout":12000}');
    await user.click(within(dialog).getByRole("button", { name: "添加" }));

    expect(onAddProvider).toHaveBeenCalledWith({
      name: "Acme AI",
      note: "Workbench provider",
      endpoint: "https://api.acme.example/v1",
      apiKey: "sk-test",
      modelMapping: "chat=acme-chat",
      testConnection: "GET /models",
      jsonConfig: '{"timeout":12000}',
    });
    expect(
      screen.queryByRole("dialog", { name: "添加服务商" }),
    ).not.toBeInTheDocument();
  });

  it("calls test and save callbacks for the selected provider", async () => {
    const user = userEvent.setup();
    const onTestProvider = vi.fn();
    const onSaveProvider = vi.fn();
    render(
      <SettingsWorkspace
        providers={providers}
        activeProviderId="backup"
        onTestProvider={onTestProvider}
        onSaveProvider={onSaveProvider}
      />,
    );

    await user.click(screen.getByRole("button", { name: "测试连接" }));
    await user.click(screen.getByRole("button", { name: "保存配置" }));

    expect(onTestProvider).toHaveBeenCalledWith("backup");
    expect(onSaveProvider).toHaveBeenCalledWith("backup");
  });

  it("uses the permission mode prop and calls the permission change callback", async () => {
    const user = userEvent.setup();
    const onPermissionModeChange = vi.fn();
    render(
      <SettingsWorkspace
        permissionMode="plan"
        onPermissionModeChange={onPermissionModeChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "权限" }));

    expect(screen.getByRole("radio", { name: /计划模式/ })).toBeChecked();

    await user.click(screen.getByRole("radio", { name: /跳过全部/ }));

    expect(onPermissionModeChange).toHaveBeenCalledWith("skip");
    expect(screen.getByRole("radio", { name: /跳过全部/ })).toBeChecked();
  });
});
