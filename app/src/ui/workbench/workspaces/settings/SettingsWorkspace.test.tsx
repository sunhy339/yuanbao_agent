import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsWorkspace } from "./SettingsWorkspace";

afterEach(() => {
  cleanup();
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

    expect(within(dialog).getByText("Detected env var: OPENAI_API_KEY")).toBeInTheDocument();

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

    expect(screen.getAllByText("Last test: missing_env").length).toBeGreaterThan(0);
    expect(screen.getByText("Failure reason")).toBeInTheDocument();
    expect(screen.getAllByText("OPENAI_API_KEY is not set.").length).toBeGreaterThan(0);
    expect(screen.getByText("Last test: ok")).toBeInTheDocument();
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

    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    expect(screen.getByText("Active provider")).toBeInTheDocument();
    expect(screen.getByText("Current model")).toBeInTheDocument();
    expect(screen.getByText("Test passed")).toBeInTheDocument();
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

  it("uses the permission mode prop and calls the permission change callback", async () => {
    const user = userEvent.setup();
    const onPermissionModeChange = vi.fn();
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

    expect(onPermissionModeChange).toHaveBeenCalledWith("skip");
    expect(skipRadio).toBeChecked();
  });

  it("uses controlled general props and emits full next values", async () => {
    const user = userEvent.setup();
    const onGeneralChange = vi.fn();
    const { container } = render(
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
    const navButtons = container.querySelectorAll(".settings-nav button");

    await user.click(navButtons[2] as HTMLElement);
    await user.click(container.querySelector('input[name="theme"][value="dark"]') as HTMLElement);

    expect(onGeneralChange).toHaveBeenCalledWith({
      theme: "dark",
      language: "zh",
      reasoningEffort: "medium",
      webFetchPreflight: true,
    });

    await user.click(
      container.querySelector('input[type="checkbox"]') as HTMLInputElement,
    );

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
    const { container } = render(<SettingsWorkspace onOpenSkillsFolder={onOpenSkillsFolder} />);
    const navButtons = container.querySelectorAll(".settings-nav button");

    await user.click(navButtons[5] as HTMLElement);
    expect(container.querySelector(".settings-empty-state")).toBeInTheDocument();

    await user.click(container.querySelector(".settings-secondary-action") as HTMLElement);
    expect(onOpenSkillsFolder).toHaveBeenCalled();
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

    await user.click(navButtons[7] as HTMLElement);
    expect(screen.getByText("Project memory")).toBeInTheDocument();
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

    await user.click(navButtons[7] as HTMLElement);
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
