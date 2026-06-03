import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsWorkspace } from "./SettingsWorkspace";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function getProviderButton(name: string) {
  const label = screen.getByText(name);
  const button = label.closest("button");
  if (!button) {
    throw new Error(`Provider button not found for ${name}`);
  }
  return button;
}

function openAddProviderModal(container: HTMLElement) {
  const button = container.querySelector(
    ".settings-panel-header .settings-primary-action",
  ) as HTMLElement | null;
  if (!button) {
    throw new Error("Add provider button not found");
  }
  return userEvent.setup().click(button);
}

async function openSettingsSection(container: HTMLElement, label: string) {
  const nav = container.querySelector(".settings-nav");
  if (!nav) {
    throw new Error("Settings nav not found");
  }
  await userEvent.setup().click(within(nav as HTMLElement).getByRole("button", { name: label }));
}

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
    const navButtons = container.querySelectorAll(".settings-nav button");

    expect(container.querySelector(".settings-content-panel")).toBeInTheDocument();
    expect(container.querySelector(".settings-provider-grid")).toBeInTheDocument();

    await user.click(navButtons[1] as HTMLElement);
    expect(container.querySelector(".settings-card-stack")).toBeInTheDocument();

    await user.click(navButtons[2] as HTMLElement);
    expect(container.querySelector(".settings-form-stack")).toBeInTheDocument();
  });

  it("submits provider modal values through the add callback", async () => {
    const user = userEvent.setup();
    const onAddProvider = vi.fn();
    const onTestProviderConfig = vi.fn();
    const { container } = render(
      <SettingsWorkspace
        onAddProvider={onAddProvider}
        onTestProviderConfig={onTestProviderConfig}
      />,
    );

    await openAddProviderModal(container);

    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "MiniMax" }));
    await user.clear(dialog.querySelector("#provider-name") as HTMLInputElement);
    await user.type(dialog.querySelector("#provider-name") as HTMLInputElement, "Acme AI");
    await user.type(
      dialog.querySelector("#provider-note") as HTMLInputElement,
      "Workbench provider",
    );
    await user.clear(dialog.querySelector("#provider-endpoint") as HTMLInputElement);
    await user.type(
      dialog.querySelector("#provider-endpoint") as HTMLInputElement,
      "https://api.acme.example/v1",
    );
    await user.selectOptions(dialog.querySelector("#provider-api-format") as HTMLSelectElement, "openai-chat");
    await user.type(dialog.querySelector("#provider-api-key") as HTMLInputElement, "sk-test");
    await user.clear(dialog.querySelector("#provider-main-model") as HTMLInputElement);
    await user.type(
      dialog.querySelector("#provider-main-model") as HTMLInputElement,
      "acme-main",
    );
    await user.clear(dialog.querySelector("#provider-haiku-model") as HTMLInputElement);
    await user.type(
      dialog.querySelector("#provider-haiku-model") as HTMLInputElement,
      "acme-haiku",
    );
    await user.clear(dialog.querySelector("#provider-sonnet-model") as HTMLInputElement);
    await user.type(
      dialog.querySelector("#provider-sonnet-model") as HTMLInputElement,
      "acme-sonnet",
    );
    await user.clear(dialog.querySelector("#provider-opus-model") as HTMLInputElement);
    await user.type(
      dialog.querySelector("#provider-opus-model") as HTMLInputElement,
      "acme-opus",
    );
    await user.clear(dialog.querySelector("#provider-json") as HTMLTextAreaElement);
    await user.click(dialog.querySelector("#provider-json") as HTMLTextAreaElement);
    await user.paste('{"timeout":12000}');

    const testButton = dialog.querySelectorAll("footer button")[1] as HTMLElement;
    await user.click(testButton);
    expect(onTestProviderConfig).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Acme AI",
        mainModel: "acme-main",
        apiFormat: "openai-chat",
        preset: "minimax",
      }),
    );

    await user.click(dialog.querySelector('button[type="submit"]') as HTMLElement);

    expect(onAddProvider).toHaveBeenCalledWith({
      name: "Acme AI",
      note: "Workbench provider",
      endpoint: "https://api.acme.example/v1",
      apiFormat: "openai-chat",
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
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows expanded provider API formats as available", async () => {
    const { container } = render(<SettingsWorkspace />);

    await openAddProviderModal(container);

    const dialog = screen.getByRole("dialog");
    const select = dialog.querySelector("#provider-api-format") as HTMLSelectElement;
    const responsesOption = Array.from(select.options).find((option) => option.value === "openai-responses");
    const anthropicOption = Array.from(select.options).find((option) => option.value === "anthropic-messages");

    expect(responsesOption).toBeDefined();
    expect(responsesOption).not.toBeDisabled();
    expect(anthropicOption).toBeDefined();
    expect(anthropicOption).not.toBeDisabled();
  });

  it("validates provider modal fields before testing or saving", async () => {
    const user = userEvent.setup();
    const onAddProvider = vi.fn();
    const onTestProviderConfig = vi.fn();
    const { container } = render(
      <SettingsWorkspace
        onAddProvider={onAddProvider}
        onTestProviderConfig={onTestProviderConfig}
      />,
    );

    await openAddProviderModal(container);

    const dialog = screen.getByRole("dialog");
    await user.clear(dialog.querySelector("#provider-endpoint") as HTMLInputElement);
    await user.type(dialog.querySelector("#provider-endpoint") as HTMLInputElement, "api.invalid.local");

    expect(within(dialog).getByText("接口地址应以 http:// 或 https:// 开头。")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "测试连接" })).toBeDisabled();
    expect(dialog.querySelector('button[type="submit"]')).toBeDisabled();

    expect(onTestProviderConfig).not.toHaveBeenCalled();
    expect(onAddProvider).not.toHaveBeenCalled();
  });

  it("derives the API key env var from pasted env config", async () => {
    const user = userEvent.setup();
    const onAddProvider = vi.fn();
    const { container } = render(<SettingsWorkspace onAddProvider={onAddProvider} />);

    await openAddProviderModal(container);

    const dialog = screen.getByRole("dialog");
    await user.clear(dialog.querySelector("#provider-name") as HTMLInputElement);
    await user.type(dialog.querySelector("#provider-name") as HTMLInputElement, "Env Provider");
    await user.clear(dialog.querySelector("#provider-main-model") as HTMLInputElement);
    await user.type(dialog.querySelector("#provider-main-model") as HTMLInputElement, "env-model");
    await user.clear(dialog.querySelector("#provider-json") as HTMLTextAreaElement);
    await user.click(dialog.querySelector("#provider-json") as HTMLTextAreaElement);
    await user.paste(
      "OPENAI_API_KEY=sk-from-shell\nOPENAI_BASE_URL=https://api.env.example/v1\nOPENAI_MODEL=env-model",
    );

    expect(within(dialog).getByText("检测到环境变量：OPENAI_API_KEY")).toBeInTheDocument();

    await user.click(dialog.querySelector('button[type="submit"]') as HTMLElement);

    expect(onAddProvider).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Env Provider",
        apiKey: "",
        apiKeyEnvVarName: "OPENAI_API_KEY",
        jsonConfig:
          "OPENAI_API_KEY=sk-from-shell\nOPENAI_BASE_URL=https://api.env.example/v1\nOPENAI_MODEL=env-model",
      }),
    );
  });

  it("shows readable provider test success and failure summaries", () => {
    render(
      <SettingsWorkspace
        providers={[
          {
            id: "primary",
            name: "Primary Provider",
            endpoint: "https://primary.example.com",
            models: ["primary-chat"],
            status: "ready",
            lastTest: {
              ok: true,
              status: "ok",
              model: "primary-chat",
              finishReason: "stop",
              message: "Connection succeeded.",
              checkedAt: 1710000000000,
            },
          },
          {
            id: "backup",
            name: "Backup Provider",
            endpoint: "https://backup.example.com",
            models: ["backup-chat"],
            status: "failed",
            lastTest: {
              ok: false,
              status: "missing_env",
              message: "Missing OPENAI_API_KEY.",
              errorSummary: "OPENAI_API_KEY is not set.",
            },
          },
        ]}
        activeProviderId="backup"
      />,
    );

    expect(screen.getAllByText(/最近测试：/).length).toBeGreaterThan(0);
    expect(screen.getByText("失败原因")).toBeInTheDocument();
    expect(screen.getAllByText("OPENAI_API_KEY is not set.").length).toBeGreaterThan(0);
    expect(screen.getByText("primary-chat / stop")).toBeInTheDocument();
  });

  it("shows active provider, current model, save notice, and connection state", () => {
    render(
      <SettingsWorkspace
        providers={[
          {
            id: "primary",
            name: "Primary Provider",
            endpoint: "https://primary.example.com",
            models: ["primary-chat"],
            status: "ready",
            lastTest: {
              ok: true,
              status: "ok",
              model: "primary-chat",
              finishReason: "stop",
              message: "Connection succeeded.",
            },
          },
        ]}
        activeProviderId="primary"
        providerFeedback={{
          providerId: "primary",
          tone: "success",
          title: "Saved and activated",
          message: "Primary Provider is now the active provider.",
          detail: "Model: primary-chat",
        }}
      />,
    );

    expect(screen.getAllByText("当前").length).toBeGreaterThan(0);
    expect(screen.getByText("当前供应商")).toBeInTheDocument();
    expect(screen.getByText("当前模型")).toBeInTheDocument();
    expect(screen.getByText("测试通过")).toBeInTheDocument();
    expect(screen.getByText("Saved and activated")).toBeInTheDocument();
    expect(screen.getByText("Primary Provider is now the active provider.")).toBeInTheDocument();
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

    await user.click(getProviderButton("Backup Provider"));
    const providerActions = document.querySelectorAll(
      ".settings-provider-detail .settings-provider-actions button",
    );
    await user.click(providerActions[1] as HTMLElement);
    await user.click(providerActions[2] as HTMLElement);

    expect(onSelectProvider).toHaveBeenCalledWith("backup");
    expect(onTestProvider).toHaveBeenCalledWith("backup");
    expect(onSaveProvider).toHaveBeenCalledWith("backup");
  });

  it("uses settings manager shortcuts and edits provider modal values through callbacks", async () => {
    const user = userEvent.setup();
    const onOpenMcpManager = vi.fn();
    const onOpenSkillsManager = vi.fn();
    const onEditProvider = vi.fn().mockResolvedValue(undefined);
    render(
      <SettingsWorkspace
        providers={providers}
        activeProviderId="primary"
        onOpenMcpManager={onOpenMcpManager}
        onOpenSkillsManager={onOpenSkillsManager}
        onEditProvider={onEditProvider}
      />,
    );

    await user.click(screen.getByRole("button", { name: "管理 MCP" }));
    await user.click(screen.getByRole("button", { name: "管理技能" }));
    await user.click(screen.getByRole("button", { name: "编辑" }));

    const dialog = screen.getByRole("dialog");
    await user.clear(dialog.querySelector("#provider-name") as HTMLInputElement);
    await user.type(dialog.querySelector("#provider-name") as HTMLInputElement, "Primary Provider Updated");
    await user.clear(dialog.querySelector("#provider-main-model") as HTMLInputElement);
    await user.type(dialog.querySelector("#provider-main-model") as HTMLInputElement, "primary-chat-next");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    expect(onOpenMcpManager).toHaveBeenCalledTimes(1);
    expect(onOpenSkillsManager).toHaveBeenCalledTimes(1);
    expect(onEditProvider).toHaveBeenCalledWith(
      "primary",
      expect.objectContaining({
        name: "Primary Provider Updated",
        endpoint: "https://primary.example.com",
        mainModel: "primary-chat-next",
        modelMapping: "main=primary-chat-next\nhaiku=primary-chat\nsonnet=primary-chat\nopus=primary-chat",
      }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("filters provider list by search text and state", async () => {
    const user = userEvent.setup();
    render(
      <SettingsWorkspace
        providers={[
          {
            id: "primary",
            name: "Primary Provider",
            endpoint: "https://primary.example.com",
            models: ["primary-chat"],
            status: "ready",
          },
          {
            id: "backup",
            name: "Backup Provider",
            endpoint: "https://backup.example.com",
            models: ["backup-chat"],
            status: "standby",
          },
        ]}
        activeProviderId="primary"
      />,
    );

    await user.type(screen.getByRole("textbox", { name: "搜索供应商" }), "backup");
    const providerList = screen.getByLabelText("供应商列表");

    expect(within(providerList).queryByText("Primary Provider")).not.toBeInTheDocument();
    expect(within(providerList).getByText("Backup Provider")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "可用" }));

    expect(within(providerList).queryByText("Backup Provider")).not.toBeInTheDocument();
    expect(within(providerList).getByText("没有匹配的供应商")).toBeInTheDocument();
  });

  it("uses the permission mode prop and calls the permission change callback", async () => {
    const user = userEvent.setup();
    const onPermissionModeChange = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { container } = render(
      <SettingsWorkspace
        permissionMode="plan"
        onPermissionModeChange={onPermissionModeChange}
      />,
    );
    const navButtons = container.querySelectorAll(".settings-nav button");

    await user.click(navButtons[1] as HTMLElement);

    const modeRadios = container.querySelectorAll(
      ".settings-card-stack input[type='radio']",
    );
    const planRadio = modeRadios[2] as HTMLInputElement;
    const skipRadio = modeRadios[3] as HTMLInputElement;
    expect(planRadio).toBeChecked();

    await user.click(skipRadio);

    expect(confirm).toHaveBeenCalledWith("切换到自主执行会减少审批提示，仅建议在受控环境中使用。继续切换？");
    expect(onPermissionModeChange).toHaveBeenCalledWith("skip");
    expect(skipRadio).toBeChecked();
  });

  it("shows explicit permission rules and clears them from settings", async () => {
    const user = userEvent.setup();
    const onClearPermissionRule = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { container } = render(
      <SettingsWorkspace
        permissionRules={[
          {
            capability: "runCommand",
            label: "命令执行",
            mode: "allow",
            modeLabel: "始终允许",
            scope: "*",
            description: "来自审批的始终允许规则。",
          },
        ]}
        onClearPermissionRule={onClearPermissionRule}
      />,
    );

    await user.click(container.querySelectorAll(".settings-nav button")[1] as HTMLElement);

    const ruleList = container.querySelector(".settings-rule-list") as HTMLElement;
    expect(within(ruleList).getByText("命令执行")).toBeInTheDocument();
    expect(within(ruleList).getByText(/scope:/)).toHaveTextContent("始终允许");
    await user.click(within(ruleList).getByRole("button", { name: "恢复默认" }));

    expect(confirm).toHaveBeenCalledWith("恢复“命令执行”的默认权限规则？");
    expect(onClearPermissionRule).toHaveBeenCalledWith("runCommand");
  });

  it("filters explicit permission rules by text and mode", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <SettingsWorkspace
        permissionRules={[
          {
            capability: "runCommand",
            label: "命令执行",
            mode: "allow",
            modeLabel: "始终允许",
            scope: "*",
            description: "来自审批的始终允许规则。",
          },
          {
            capability: "networkAccess",
            label: "网络访问",
            mode: "deny",
            modeLabel: "拒绝",
            scope: "workspace",
            description: "敏感环境禁用网络。",
          },
        ]}
      />,
    );

    await user.click(container.querySelectorAll(".settings-nav button")[1] as HTMLElement);
    const ruleSection = screen.getByRole("heading", { name: "始终允许与显式规则" }).closest("section") as HTMLElement;

    await user.type(screen.getByRole("textbox", { name: "搜索权限规则" }), "网络");

    expect(within(ruleSection).queryByText("命令执行")).not.toBeInTheDocument();
    expect(within(ruleSection).getByText("网络访问")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "允许" }));

    expect(within(ruleSection).queryByText("网络访问")).not.toBeInTheDocument();
    expect(within(ruleSection).getByText("没有匹配的权限规则")).toBeInTheDocument();
  });

  it("uses controlled general props and emits full next values", async () => {
    const user = userEvent.setup();
    const onGeneralChange = vi.fn();
    const { container } = render(
      <SettingsWorkspace
        general={{
          theme: "light",
          density: "comfortable",
          radius: "md",
          motion: "subtle",
          accentColor: "cyan",
          transparency: 0.78,
          fontScale: 1,
          language: "zh",
          reasoningEffort: "medium",
          webFetchPreflight: true,
        }}
        onGeneralChange={onGeneralChange}
      />,
    );
    const navButtons = container.querySelectorAll(".settings-nav button");

    await user.click(navButtons[2] as HTMLElement);
    await user.click(container.querySelector('input[name="theme"][value="dark"]') as HTMLElement);

    expect(onGeneralChange).toHaveBeenCalledWith({
      theme: "dark",
      density: "comfortable",
      radius: "md",
      motion: "subtle",
      accentColor: "cyan",
      transparency: 0.78,
      fontScale: 1,
      language: "zh",
      reasoningEffort: "medium",
      webFetchPreflight: true,
    });

    await user.click(
      container.querySelector('input[type="checkbox"]') as HTMLInputElement,
    );

    expect(onGeneralChange).toHaveBeenCalledWith({
      theme: "dark",
      density: "comfortable",
      radius: "md",
      motion: "subtle",
      accentColor: "cyan",
      transparency: 0.78,
      fontScale: 1,
      language: "zh",
      reasoningEffort: "medium",
      webFetchPreflight: false,
    });

    await user.click(screen.getByRole("button", { name: "恢复默认" }));

    expect(onGeneralChange).toHaveBeenCalledWith({
      theme: "dark",
      density: "comfortable",
      radius: "md",
      motion: "subtle",
      accentColor: "cyan",
      transparency: 0.78,
      fontScale: 1,
      language: "auto",
      reasoningEffort: "max",
      webFetchPreflight: true,
    });
  });

  it("keeps secondary sections as connectable skeletons", async () => {
    const user = userEvent.setup();
    const onOpenSkillsFolder = vi.fn();
    const { container } = render(<SettingsWorkspace onOpenSkillsFolder={onOpenSkillsFolder} />);
    const navButtons = container.querySelectorAll(".settings-nav button");

    await openSettingsSection(container, "技能库");
    expect(container.querySelector(".settings-empty-state")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "打开目录" }));
    expect(onOpenSkillsFolder).toHaveBeenCalled();
  });

  it("renders installed skills as read-only runtime presets", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <SettingsWorkspace
        skills={[
          {
            id: "docs",
            name: "Docs",
            description: "Document workflows",
            path: "category:productivity",
            enabled: true,
          },
        ]}
      />,
    );
    await openSettingsSection(container, "技能库");

    expect(screen.getByText("Docs")).toBeInTheDocument();
    expect(screen.getByText("可用")).toBeInTheDocument();
    expect(container.querySelector('input[type="checkbox"]')).not.toBeInTheDocument();
  });

  it("marks unsupported utility actions as disabled with visible reasons", async () => {
    const user = userEvent.setup();
    const { container } = render(<SettingsWorkspace />);
    await openSettingsSection(container, "消息桥接");
    expect(screen.getByRole("button", { name: "测试 IM 连接" })).toBeDisabled();
    expect(screen.getByText("当前桌面版本尚未接入运行时消息桥接测试。")).toBeInTheDocument();

    await openSettingsSection(container, "智能体");
    expect(screen.getByRole("button", { name: "New profile" })).toBeDisabled();
    expect(screen.getByText("No agent profiles")).toBeInTheDocument();

    await openSettingsSection(container, "技能库");
    expect(screen.getByRole("button", { name: "打开目录" })).toBeDisabled();
    expect(screen.getByText("打开目录还在等待桌面 shell 桥接；刷新仍会使用运行时技能注册表。")).toBeInTheDocument();

    await openSettingsSection(container, "电脑操作");
    expect(screen.getByRole("button", { name: "重新检查" })).toBeDisabled();
    expect(screen.getByText("桌面权限重新检查尚未实现。")).toBeInTheDocument();
    expect(screen.getByLabelText("电脑操作能力状态")).toHaveTextContent("截图 action 已接入运行时");

    await openSettingsSection(container, "关于");
    expect(screen.getByRole("button", { name: "打开日志" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "打开数据目录" })).toBeDisabled();
    expect(screen.getByText("打开本地目录还在等待 Tauri shell 桥接；上方路径可用于手动检查。")).toBeInTheDocument();
  });

  it("surfaces checked computer-use capabilities", async () => {
    const user = userEvent.setup();
    const onRecheckComputerUse = vi.fn();
    const { container } = render(
      <SettingsWorkspace
        computerUse={{
          screenshot: true,
          browserAutomation: true,
          clipboardAccess: true,
          systemKeyCombos: false,
          sensitiveActionConfirm: true,
          status: "degraded",
          checkedAt: new Date("2026-05-31T03:04:05Z").getTime(),
          capabilities: [
            {
              id: "screen-observation",
              label: "屏幕观察",
              state: "ready",
              detail: "截图 action 已接入运行时，执行时会尝试 Pillow ImageGrab 并返回缩略预览。",
            },
            {
              id: "browser-dom",
              label: "浏览器 DOM 控制",
              state: "partial",
              detail: "Playwright page-like executor 协议已就绪，宿主还需要注入真实浏览器会话。",
            },
          ],
        }}
        onRecheckComputerUse={onRecheckComputerUse}
      />,
    );

    await openSettingsSection(container, "电脑操作");

    const capabilities = screen.getByLabelText("电脑操作能力状态");
    expect(capabilities).toHaveTextContent("屏幕观察");
    expect(capabilities).toHaveTextContent("已接入");
    expect(capabilities).toHaveTextContent("浏览器 DOM 控制");
    expect(capabilities).toHaveTextContent("部分接入");
    expect(screen.getByText(/状态：部分可用/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重新检查" }));
    expect(onRecheckComputerUse).toHaveBeenCalledTimes(1);
  });

  it("manages dynamic agent profiles from settings", async () => {
    const user = userEvent.setup();
    const onAddAgent = vi.fn();
    const onUpdateAgent = vi.fn();
    const onDeleteAgent = vi.fn();
    const onValidateAgent = vi.fn().mockResolvedValue({ valid: true, errors: [] });
    const onPreviewAgentTools = vi.fn().mockResolvedValue({
      allowedTools: ["read_file", "git_diff"],
      deniedTools: ["run_command"],
    });
    const { container } = render(
      <SettingsWorkspace
        agents={[
          {
            id: "reviewer",
            name: "Reviewer",
            description: "Review code changes",
            role: "reviewer",
            enabled: true,
            permissionMode: "ask",
            toolPolicy: {
              allowedTools: ["read_file"],
              deniedTools: ["run_command"],
            },
          },
        ]}
        onAddAgent={onAddAgent}
        onUpdateAgent={onUpdateAgent}
        onDeleteAgent={onDeleteAgent}
        onValidateAgent={onValidateAgent}
        onPreviewAgentTools={onPreviewAgentTools}
      />,
    );
    await openSettingsSection(container, "智能体");
    expect(screen.getAllByText("Reviewer").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Preview tools" }));
    expect(onPreviewAgentTools).toHaveBeenCalledWith(
      expect.objectContaining({
        role: "reviewer",
        permissionMode: "ask",
      }),
    );
    expect(await screen.findByText(/2 allowed/)).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "Senior Reviewer");
    await user.click(screen.getByRole("button", { name: "Save profile" }));
    expect(onUpdateAgent).toHaveBeenCalledWith(
      "reviewer",
      expect.objectContaining({
        name: "Senior Reviewer",
        role: "reviewer",
        toolPolicy: expect.objectContaining({
          allowedTools: ["read_file"],
          deniedTools: ["run_command"],
        }),
      }),
    );

    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDeleteAgent).toHaveBeenCalledWith("reviewer");

    await user.click(screen.getByRole("button", { name: "New profile" }));
    await user.type(screen.getByLabelText("Name"), "Builder");
    await user.selectOptions(screen.getByLabelText("Role"), "builder");
    await user.type(screen.getByLabelText("Allowed tools"), "apply_patch");
    await user.click(screen.getByRole("button", { name: "Create profile" }));
    expect(onAddAgent).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Builder",
        role: "builder",
        toolPolicy: expect.objectContaining({
          allowedTools: ["apply_patch"],
        }),
      }),
    );
  });

  it("manages runtime hooks from settings", async () => {
    const user = userEvent.setup();
    const onAddHook = vi.fn();
    const onUpdateHook = vi.fn();
    const onDeleteHook = vi.fn();
    const onRefreshHookExecutions = vi.fn();
    const { container } = render(
      <SettingsWorkspace
        hookWorkspaceId="workspace-1"
        hooks={[
          {
            id: "hook-1",
            name: "Audit provider",
            enabled: true,
            scope: "workspace",
            workspaceId: "workspace-1",
            event: "before_provider_turn",
            priority: 20,
            conditions: { taskStatus: "running" },
            action: { type: "audit_note", note: "provider turn" },
            authority: { policy: "allowed" },
            timeoutMs: 60000,
            retry: { maxAttempts: 0 },
            onFailure: "warn",
            createdAt: 1,
            updatedAt: 1,
          },
        ]}
        hookExecutions={[
          {
            id: "exec-1",
            hookId: "hook-1",
            event: "before_provider_turn",
            conditionResult: "matched",
            policyOutcome: "allowed",
            status: "completed",
            startedAt: 1,
            finishedAt: 2,
            durationMs: 1,
            createdAt: 1,
          },
        ]}
        onAddHook={onAddHook}
        onUpdateHook={onUpdateHook}
        onDeleteHook={onDeleteHook}
        onRefreshHookExecutions={onRefreshHookExecutions}
      />,
    );

    await openSettingsSection(container, "Hooks");
    expect(screen.getAllByText("Audit provider").length).toBeGreaterThan(0);
    expect(screen.getByText("completed")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Event"), "after_provider_turn");
    await user.click(screen.getByRole("button", { name: "Save hook" }));
    expect(onUpdateHook).toHaveBeenCalledWith(
      "hook-1",
      expect.objectContaining({
        event: "after_provider_turn",
        action: expect.objectContaining({ type: "audit_note" }),
      }),
    );

    await user.click(screen.getByRole("button", { name: "Load runs" }));
    expect(onRefreshHookExecutions).toHaveBeenCalledWith("hook-1");

    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDeleteHook).toHaveBeenCalledWith("hook-1");

    await user.click(screen.getByRole("button", { name: "New hook" }));
    await user.type(screen.getByLabelText("Name"), "Run verifier");
    await user.selectOptions(screen.getByLabelText("Action"), "run_command");
    await user.type(screen.getByLabelText("Command"), "npm test");
    await user.click(screen.getByRole("button", { name: "Create hook" }));
    expect(onAddHook).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-1",
        name: "Run verifier",
        action: expect.objectContaining({
          type: "run_command",
          command: "npm test",
        }),
      }),
    );

    await user.type(screen.getByLabelText("Name"), "Webhook sync");
    await user.selectOptions(screen.getByLabelText("Action"), "webhook");
    await user.type(screen.getByLabelText("Webhook URL"), "https://example.com/hook");
    await user.click(screen.getByRole("button", { name: "Create hook" }));
    expect(onAddHook).toHaveBeenCalledWith(
      expect.objectContaining({
        workspaceId: "workspace-1",
        name: "Webhook sync",
        action: expect.objectContaining({
          type: "webhook",
          url: "https://example.com/hook",
          method: "POST",
        }),
      }),
    );
  });

  it("shows project memory state and clears it from settings", async () => {
    const user = userEvent.setup();
    const onClearWorkspaceMemory = vi.fn();
    const { container } = render(
      <SettingsWorkspace
        workspaceMemorySummary="Project memory:\n- completed: roadmap aligned"
        onClearWorkspaceMemory={onClearWorkspaceMemory}
      />,
    );
    const navButtons = container.querySelectorAll(".settings-nav button");

    await openSettingsSection(container, "关于");
    expect(screen.getByText("项目记忆")).toBeInTheDocument();
    expect(screen.getByText(/roadmap aligned/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "清空项目记忆" }));

    expect(onClearWorkspaceMemory).toHaveBeenCalledTimes(1);
  });

  it("edits and clears project focus separately from project memory", async () => {
    const user = userEvent.setup();
    const onSaveWorkspaceFocus = vi.fn();
    const onClearWorkspaceMemory = vi.fn();
    const { container } = render(
      <SettingsWorkspace
        workspaceFocus="Build durable product iteration."
        workspaceMemorySummary="Project memory:\n- completed: roadmap aligned"
        onSaveWorkspaceFocus={onSaveWorkspaceFocus}
        onClearWorkspaceMemory={onClearWorkspaceMemory}
      />,
    );
    const navButtons = container.querySelectorAll(".settings-nav button");

    await openSettingsSection(container, "关于");
    const focusInput = screen.getByRole("textbox", { name: "固定焦点" }) as HTMLTextAreaElement;
    await user.clear(focusInput);
    await user.type(focusInput, "Keep context focused on large projects.");
    await user.click(screen.getByRole("button", { name: "保存项目焦点" }));

    expect(onSaveWorkspaceFocus).toHaveBeenCalledWith("Keep context focused on large projects.");
    expect(onClearWorkspaceMemory).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "清空项目焦点" }));

    expect(onSaveWorkspaceFocus).toHaveBeenLastCalledWith("");
  });
});
