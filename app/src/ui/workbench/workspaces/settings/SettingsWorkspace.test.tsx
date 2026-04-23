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

  it("renders providers by default and switches settings tabs", async () => {
    const user = userEvent.setup();
    const { container } = render(<SettingsWorkspace />);

    expect(screen.getByRole("heading", { name: "服务商" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加服务商" })).toBeInTheDocument();
    expect(container.querySelector(".settings-content-panel")).toBeInTheDocument();
    expect(container.querySelector(".settings-provider-grid")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "权限" }));
    expect(screen.getByRole("heading", { name: "权限模式" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "通用" }));
    expect(screen.getByRole("heading", { name: "通用" })).toBeInTheDocument();
  });

  it("submits provider modal values through the add callback", async () => {
    const user = userEvent.setup();
    const onAddProvider = vi.fn();
    const onTestProviderConfig = vi.fn();
    render(
      <SettingsWorkspace
        onAddProvider={onAddProvider}
        onTestProviderConfig={onTestProviderConfig}
      />,
    );

    await user.click(screen.getByRole("button", { name: "添加服务商" }));

    const dialog = screen.getByRole("dialog", { name: "添加服务商" });
    await user.click(within(dialog).getByRole("button", { name: "MiniMax" }));
    await user.clear(within(dialog).getByLabelText("名称 *"));
    await user.type(within(dialog).getByLabelText("名称 *"), "Acme AI");
    await user.type(within(dialog).getByLabelText("备注"), "Workbench provider");
    await user.clear(within(dialog).getByLabelText("接口地址"));
    await user.type(
      within(dialog).getByLabelText("接口地址"),
      "https://api.acme.example/v1",
    );
    await user.type(within(dialog).getByLabelText("API 密钥 *"), "sk-test");
    await user.clear(within(dialog).getByLabelText("主模型 *"));
    await user.type(within(dialog).getByLabelText("主模型 *"), "acme-main");
    await user.clear(within(dialog).getByLabelText("Haiku 模型"));
    await user.type(within(dialog).getByLabelText("Haiku 模型"), "acme-haiku");
    await user.clear(within(dialog).getByLabelText("Sonnet 模型"));
    await user.type(within(dialog).getByLabelText("Sonnet 模型"), "acme-sonnet");
    await user.clear(within(dialog).getByLabelText("Opus 模型"));
    await user.type(within(dialog).getByLabelText("Opus 模型"), "acme-opus");
    await user.clear(within(dialog).getByLabelText("设置 JSON"));
    await user.click(within(dialog).getByLabelText("设置 JSON"));
    await user.paste('{"timeout":12000}');

    await user.click(within(dialog).getByRole("button", { name: "测试连接" }));
    expect(onTestProviderConfig).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Acme AI",
        mainModel: "acme-main",
        preset: "minimax",
      }),
    );

    await user.click(within(dialog).getByRole("button", { name: "添加" }));

    expect(onAddProvider).toHaveBeenCalledWith({
      name: "Acme AI",
      note: "Workbench provider",
      endpoint: "https://api.acme.example/v1",
      apiKey: "sk-test",
      modelMapping:
        "main=acme-main\nhaiku=acme-haiku\nsonnet=acme-sonnet\nopus=acme-opus",
      mainModel: "acme-main",
      haikuModel: "acme-haiku",
      sonnetModel: "acme-sonnet",
      opusModel: "acme-opus",
      testConnection: "GET /models",
      jsonConfig: '{"timeout":12000}',
      preset: "minimax",
    });
    expect(screen.queryByRole("dialog", { name: "添加服务商" })).not.toBeInTheDocument();
  });

  it("uses provider selection, test and save callbacks", async () => {
    const user = userEvent.setup();
    const onSelectProvider = vi.fn();
    const onTestProvider = vi.fn();
    const onSaveProvider = vi.fn();
    render(
      <SettingsWorkspace
        providers={providers}
        activeProviderId="primary"
        onSelectProvider={onSelectProvider}
        onTestProvider={onTestProvider}
        onSaveProvider={onSaveProvider}
      />,
    );

    await user.click(screen.getByRole("button", { name: "选择服务商 Backup Provider" }));
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(onSelectProvider).toHaveBeenCalledWith("backup");
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

  it("uses controlled general props and emits full next values", async () => {
    const user = userEvent.setup();
    const onGeneralChange = vi.fn();
    render(
      <SettingsWorkspace
        general={{
          theme: "light",
          language: "zh",
          reasoningEffort: "medium",
          webFetchPreflight: true,
        }}
        onGeneralChange={onGeneralChange}
      />,
    );

    await user.click(screen.getByRole("button", { name: "通用" }));
    await user.click(screen.getByRole("radio", { name: "暗色" }));

    expect(onGeneralChange).toHaveBeenCalledWith({
      theme: "dark",
      language: "zh",
      reasoningEffort: "medium",
      webFetchPreflight: true,
    });

    await user.click(screen.getByRole("checkbox", { name: /跳过 WebFetch 域名预检/ }));

    expect(onGeneralChange).toHaveBeenCalledWith({
      theme: "dark",
      language: "zh",
      reasoningEffort: "medium",
      webFetchPreflight: false,
    });
  });

  it("keeps secondary sections as connectable skeletons", async () => {
    const user = userEvent.setup();
    const onOpenSkillsFolder = vi.fn();
    render(<SettingsWorkspace onOpenSkillsFolder={onOpenSkillsFolder} />);

    await user.click(screen.getByRole("button", { name: "技能" }));
    expect(screen.getByText("暂无已安装技能")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "打开目录" }));
    expect(onOpenSkillsFolder).toHaveBeenCalled();
  });
});
