import { invoke } from "@tauri-apps/api/core";
import type { AgentEventEnvelope, TaskRecord, TraceEventRecord } from "@shared";
import { RuntimeClient } from "../lib/runtimeClient";

interface TauriProviderFlowFixture {
  enabled: boolean;
  flow?: string;
  workspacePath?: string;
  prompt?: string;
  sessionTitle?: string;
  provider?: {
    profileId: string;
    name: string;
    baseUrl: string;
    model: string;
    apiKeyEnvVarName: string;
    timeout: number;
  };
}

interface TauriProviderFlowResult {
  ok: boolean;
  flow: "provider-flow" | "ui-smoke" | "session-recovery-seed" | "session-recovery-verify";
  phase: string;
  provider?: {
    ok?: boolean;
    status?: string;
    model?: string;
    baseUrl?: string;
    checkedEnvVarName?: string;
  };
  sessionId?: string;
  taskId?: string;
  taskStatus?: TaskRecord["status"];
  taskSummary?: string | null;
  persistedMessageRoles?: string[];
  persistedMessageCount?: number;
  eventTypes: string[];
  traceTypes: string[];
  missingTraceTypes?: string[];
  uiAssertions?: string[];
  error?: string;
}

let started = false;

const REQUIRED_TRACE_TYPES = [
  "provider.request",
  "provider.response",
  "tool.started",
  "tool.completed",
  "task.completed",
];

const UI_ASSERTION_TIMEOUT_MS = 180_000;

function isTerminalStatus(status: TaskRecord["status"]) {
  return status === "completed" || status === "failed" || status === "cancelled";
}

async function getFixture(): Promise<TauriProviderFlowFixture | null> {
  const deadline = Date.now() + 15_000;

  while (Date.now() < deadline) {
    try {
      return await invoke<TauriProviderFlowFixture>("e2e_fixture");
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }

  return null;
}

async function finish(result: TauriProviderFlowResult) {
  await invoke("e2e_finish", { payload: result });
}

async function pollTask(client: RuntimeClient, taskId: string, initialTask: TaskRecord): Promise<TaskRecord> {
  let current = initialTask;
  const deadline = Date.now() + 180_000;

  while (!isTerminalStatus(current.status) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 1_000));
    current = (await client.getTask(taskId)).task;
  }

  return current;
}

function traceTypesFrom(traceEvents: TraceEventRecord[]) {
  return [...new Set(traceEvents.map((event) => event.type))].sort();
}

async function waitFor<T>(
  description: string,
  read: () => T | null | undefined | false,
  timeoutMs = UI_ASSERTION_TIMEOUT_MS,
): Promise<T> {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const result = read();
    if (result) {
      return result;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  throw new Error(`Timed out waiting for ${description}.`);
}

function query<T extends Element>(selector: string): T | null {
  return document.querySelector(selector) as T | null;
}

function assertText(text: string) {
  if (!document.body.textContent?.includes(text)) {
    throw new Error(`Expected UI text not found: ${text}`);
  }
}

function assertElement(selector: string, description: string) {
  if (!query(selector)) {
    throw new Error(`Expected ${description} not found: ${selector}`);
  }
}

function click(selector: string, description: string) {
  const target = query<HTMLElement>(selector);
  if (!target) {
    throw new Error(`Cannot click ${description}; selector not found: ${selector}`);
  }
  target.click();
}

function setFieldValue(selector: string, value: string) {
  const field = query<HTMLInputElement | HTMLTextAreaElement>(selector);
  if (!field) {
    throw new Error(`Input not found: ${selector}`);
  }

  const prototype = field instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
  descriptor?.set?.call(field, value);
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.dispatchEvent(new Event("change", { bubbles: true }));
}

async function configureProviderThroughUi(fixture: Required<TauriProviderFlowFixture>["provider"]) {
  await waitFor("workbench shell", () => query(".workbench-shell"));
  await waitFor("composer ready", () => {
    const composer = query<HTMLTextAreaElement>('textarea[aria-label="Task prompt"]');
    return composer && !composer.disabled ? composer : null;
  });

  click('button[aria-label="Settings"]', "Settings navigation");
  await waitFor("settings provider panel", () => query(".settings-panel-providers"));
  click(".settings-panel-header .settings-primary-action", "Add Provider");
  await waitFor("provider dialog", () => query('[role="dialog"].settings-modal'));

  setFieldValue("#provider-name", fixture.name);
  setFieldValue("#provider-endpoint", fixture.baseUrl);
  setFieldValue("#provider-api-key", fixture.apiKeyEnvVarName);
  setFieldValue("#provider-main-model", fixture.model);
  setFieldValue("#provider-haiku-model", fixture.model);
  setFieldValue("#provider-sonnet-model", fixture.model);
  setFieldValue("#provider-opus-model", fixture.model);
  setFieldValue(
    "#provider-json",
    JSON.stringify(
      {
        apiKeyEnvVarName: fixture.apiKeyEnvVarName,
        timeout: fixture.timeout,
      },
      null,
      2,
    ),
  );

  click(".settings-modal-footer .settings-secondary-action:nth-of-type(2)", "Test connection");
  await waitFor("provider test success", () =>
    document.body.textContent?.includes("Last test: ok") ? true : null,
  );
  click('.settings-modal-footer button[type="submit"]', "Save provider");
  await waitFor("provider save confirmation", () =>
    document.body.textContent?.includes("Saved and activated") &&
    document.body.textContent?.includes("Active provider") &&
    document.body.textContent?.includes(fixture.model)
      ? true
      : null,
  );
}

async function sendPromptThroughUi(prompt: string) {
  click('button[aria-label="New Session"]', "New Session navigation");
  await waitFor("task prompt composer", () => query<HTMLTextAreaElement>('textarea[aria-label="Task prompt"]'));
  setFieldValue('textarea[aria-label="Task prompt"]', prompt);
  await waitFor("composer run enabled", () => {
    const button = query<HTMLButtonElement>(".composer-run");
    return button && !button.disabled ? button : null;
  });
  click(".composer-run", "composer run");
  await waitFor("session workspace", () => query(".session-workspace:not(.session-workspace-empty)"));
}

async function runUiSmokeFlow(workspacePath?: string) {
  const assertions: string[] = [];

  await waitFor("workbench shell", () => query(".workbench-shell"));
  assertElement(".new-session-workspace", "new session workspace");
  await waitFor("command composer", () => query('textarea[aria-label="Task prompt"]'));
  assertText("New Session");
  assertions.push("new session workspace renders");

  click('button[aria-label="Settings"]', "Settings navigation");
  await waitFor("settings workspace", () => query(".settings-workspace"));
  assertElement(".settings-panel-providers", "settings providers panel");
  assertText("服务商");
  assertions.push("settings providers page renders");

  click('button[aria-label="Scheduled"]', "Scheduled navigation");
  await waitFor("scheduled workspace", () => query(".scheduled-workspace"));
  assertElement(".scheduled-empty", "scheduled empty state");
  assertText("暂无调度任务");
  assertions.push("scheduled empty state renders without demo data");

  click('button[aria-label="New Session"]', "New Session navigation");
  await waitFor("new session workspace", () => query(".new-session-workspace"));
  await waitFor("command composer after returning", () => query('textarea[aria-label="Task prompt"]'));
  assertions.push("top-level navigation returns to new session");

  await finish({
    ok: true,
    flow: "ui-smoke",
    phase: "complete",
    eventTypes: [],
    traceTypes: [],
    uiAssertions: workspacePath
      ? [...assertions, `workspace path fixture received: ${workspacePath}`]
      : assertions,
  });
}

function flowFromFixture(fixture: TauriProviderFlowFixture): TauriProviderFlowResult["flow"] {
  if (
    fixture.flow === "ui-smoke" ||
    fixture.flow === "session-recovery-seed" ||
    fixture.flow === "session-recovery-verify"
  ) {
    return fixture.flow;
  }
  return "provider-flow";
}

async function runSessionRecoverySeedFlow(client: RuntimeClient, fixture: TauriProviderFlowFixture) {
  const workspacePath = fixture.workspacePath;
  const prompt = fixture.prompt;
  const sessionTitle = fixture.sessionTitle || "E2E Recovery Session";
  if (!workspacePath || !prompt) {
    throw new Error("Session recovery seed fixture is missing workspacePath or prompt.");
  }

  const events: AgentEventEnvelope[] = [];
  const unsubscribe = await client.subscribeEvents((event) => {
    events.push(event);
  });

  try {
    await waitFor("workbench shell", () => query(".workbench-shell"));
    const workspaceResult = await client.openWorkspace(workspacePath);
    const sessionResult = await client.createSession({
      workspaceId: workspaceResult.workspace.id,
      title: sessionTitle,
    });
    const sendResult = await client.sendMessage({
      sessionId: sessionResult.session.id,
      content: prompt,
      attachments: [],
    });
    const finalTask = await pollTask(
      client,
      sendResult.task.id,
      (await client.getTask(sendResult.task.id)).task,
    );
    if (finalTask.status !== "completed") {
      throw new Error(`Expected seeded task to complete, got ${finalTask.status}.`);
    }

    const persistedMessages = (await client.listMessages({ sessionId: sessionResult.session.id, limit: 20 })).messages;
    const persistedRoles = persistedMessages.map((message) => message.role);
    if (!persistedRoles.includes("user") || !persistedRoles.includes("assistant")) {
      throw new Error(`Expected seeded user and assistant messages, got: ${persistedRoles.join(", ") || "none"}.`);
    }
    if (!persistedMessages.some((message) => message.role === "user" && message.content.includes(prompt))) {
      throw new Error("Seeded messages do not include the recovery prompt.");
    }

    await finish({
      ok: true,
      flow: "session-recovery-seed",
      phase: "complete",
      sessionId: sessionResult.session.id,
      taskId: finalTask.id,
      taskStatus: finalTask.status,
      taskSummary: finalTask.resultSummary,
      persistedMessageRoles: persistedRoles,
      persistedMessageCount: persistedMessages.length,
      eventTypes: events.map((event) => event.type),
      traceTypes: [],
      uiAssertions: [
        "seeded session through runtime API",
        "seeded task completed",
        "seeded messages persisted to runtime API",
      ],
    });
  } finally {
    unsubscribe();
  }
}

async function runSessionRecoveryVerifyFlow(client: RuntimeClient, fixture: TauriProviderFlowFixture) {
  const prompt = fixture.prompt;
  const sessionTitle = fixture.sessionTitle || "E2E Recovery Session";
  if (!prompt) {
    throw new Error("Session recovery verify fixture is missing prompt.");
  }

  await waitFor("workbench shell", () => query(".workbench-shell"));
  const sessions = (await client.listSessions()).sessions;
  const recoveredSession = sessions.find((session) => session.title === sessionTitle);
  if (!recoveredSession) {
    throw new Error(`Recovered session not found after restart: ${sessionTitle}.`);
  }

  const persistedMessages = (await client.listMessages({ sessionId: recoveredSession.id, limit: 20 })).messages;
  const persistedRoles = persistedMessages.map((message) => message.role);
  const userMessage = persistedMessages.find((message) => message.role === "user" && message.content.includes(prompt));
  const assistantMessage = persistedMessages.find((message) => message.role === "assistant" && message.content.trim());
  if (!userMessage || !assistantMessage) {
    throw new Error(`Recovered messages are incomplete: ${persistedRoles.join(", ") || "none"}.`);
  }

  await waitFor("recovered session in sidebar", () => {
    const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>(".session-rail-item"));
    return buttons.find((button) => button.textContent?.includes(sessionTitle));
  });
  const sessionButton = Array.from(document.querySelectorAll<HTMLButtonElement>(".session-rail-item"))
    .find((button) => button.textContent?.includes(sessionTitle));
  if (!sessionButton) {
    throw new Error(`Recovered session button disappeared: ${sessionTitle}.`);
  }
  sessionButton.click();
  await waitFor("recovered user message visible", () =>
    document.body.textContent?.includes(userMessage.content) ? true : null,
  );
  assertText(assistantMessage.content);

  await finish({
    ok: true,
    flow: "session-recovery-verify",
    phase: "complete",
    sessionId: recoveredSession.id,
    persistedMessageRoles: persistedRoles,
    persistedMessageCount: persistedMessages.length,
    eventTypes: [],
    traceTypes: [],
    uiAssertions: [
      "recovered session listed after desktop restart",
      "recovered session opens from sidebar",
      "persisted user message visible after restart",
      "persisted assistant message visible after restart",
    ],
  });
}

export async function maybeRunTauriProviderFlowE2e() {
  if (started) {
    return;
  }
  started = true;

  const fixture = await getFixture();
  if (!fixture?.enabled) {
    return;
  }

  const client = new RuntimeClient();
  const events: AgentEventEnvelope[] = [];
  let unsubscribe: (() => void) | null = null;
  let phase = "start";
  const flow = flowFromFixture(fixture);

  const baseResult = (): TauriProviderFlowResult => ({
    ok: false,
    flow,
    phase,
    eventTypes: events.map((event) => event.type),
    traceTypes: [],
  });

  try {
    if (fixture.flow === "ui-smoke") {
      phase = "ui-smoke";
      await runUiSmokeFlow(fixture.workspacePath);
      return;
    }

    if (fixture.flow === "session-recovery-seed") {
      phase = "session-recovery-seed";
      await runSessionRecoverySeedFlow(client, fixture);
      return;
    }

    if (fixture.flow === "session-recovery-verify") {
      phase = "session-recovery-verify";
      await runSessionRecoveryVerifyFlow(client, fixture);
      return;
    }

    if (fixture.flow !== "provider-flow") {
      throw new Error(`Unsupported E2E flow: ${fixture.flow ?? "unknown"}`);
    }
    if (!fixture.provider || !fixture.workspacePath || !fixture.prompt) {
      throw new Error("E2E fixture is missing provider, workspacePath, or prompt.");
    }
    const prompt = fixture.prompt;

    unsubscribe = await client.subscribeEvents((event) => {
      events.push(event);
    });

    phase = "configure-provider-ui";
    await configureProviderThroughUi(fixture.provider);
    const configResult = await client.getConfig();
    const activeProfileId = configResult.config.provider.activeProfileId;
    const activeProfile = configResult.config.provider.profiles?.find((profile) => profile.id === activeProfileId);
    if (!activeProfile) {
      throw new Error("Provider saved through UI, but runtime config has no active profile.");
    }
    if (activeProfile.model !== fixture.provider.model) {
      throw new Error(`Expected active model ${fixture.provider.model}, got ${activeProfile.model ?? "none"}.`);
    }

    phase = "test-provider";
    const providerResult = await client.testProvider({ profileId: activeProfileId });
    if (!providerResult.ok) {
      throw new Error(providerResult.message || `Provider test failed with status ${providerResult.status}.`);
    }

    phase = "open-workspace";
    await client.openWorkspace(fixture.workspacePath);

    phase = "send-message-ui";
    await sendPromptThroughUi(prompt);
    const completedEvent = await waitFor("task completed event", () =>
      events.find((event) => event.type === "task.completed"),
    );
    const startedEvent = events.find((event) => event.taskId === completedEvent.taskId && event.type === "task.started");
    const finalTask = await pollTask(
      client,
      completedEvent.taskId,
      (await client.getTask(completedEvent.taskId)).task,
    );
    const sessionId = completedEvent.sessionId || startedEvent?.sessionId || finalTask.sessionId;

    phase = "assert-timeline";
    if (finalTask.status !== "completed") {
      throw new Error(`Expected completed task, got ${finalTask.status}.`);
    }
    assertElement('.conversation-activity[aria-label="Conversation activity"]', "conversation activity stream");
    assertText(finalTask.id);
    assertText("completed");

    const traceEvents = (await client.listTrace({ taskId: finalTask.id, limit: 100 })).traceEvents;
    const traceTypes = traceTypesFrom(traceEvents);
    const missingTraceTypes = REQUIRED_TRACE_TYPES.filter((type) => !traceTypes.includes(type));
    if (missingTraceTypes.length > 0) {
      throw new Error(`Timeline is missing trace types: ${missingTraceTypes.join(", ")}.`);
    }
    await waitFor("provider request trace card in UI", () => query('.runtime-event-card[data-kind="trace"]'));

    phase = "assert-message-persistence";
    if (!sessionId) {
      throw new Error("Completed task did not expose a session id for message persistence checks.");
    }
    const persistedMessages = (await client.listMessages({ sessionId, limit: 20 })).messages;
    const persistedRoles = persistedMessages.map((message) => message.role);
    if (!persistedRoles.includes("user") || !persistedRoles.includes("assistant")) {
      throw new Error(`Expected persisted user and assistant messages, got: ${persistedRoles.join(", ") || "none"}.`);
    }
    if (!persistedMessages.some((message) => message.role === "user" && message.content.includes(prompt))) {
      throw new Error("Persisted messages do not include the submitted prompt.");
    }
    const assistantMessage = persistedMessages.find((message) => message.role === "assistant");
    if (!assistantMessage?.content?.trim()) {
      throw new Error("Persisted assistant message is empty.");
    }

    phase = "complete";
    await finish({
      ok: true,
      flow: "provider-flow",
      phase,
      provider: {
        ok: providerResult.ok,
        status: providerResult.status,
        model: providerResult.model,
        baseUrl: providerResult.baseUrl,
        checkedEnvVarName: providerResult.checkedEnvVarName,
      },
      sessionId,
      taskId: finalTask.id,
      taskStatus: finalTask.status,
      taskSummary: finalTask.resultSummary,
      persistedMessageRoles: persistedRoles,
      persistedMessageCount: persistedMessages.length,
      eventTypes: events.map((event) => event.type),
      traceTypes,
      uiAssertions: [
        "provider settings saved through UI",
        "provider test result visible in UI",
        "composer submitted through UI",
        "session task completion visible in UI",
        "runtime timeline rendered in UI",
        "message persistence verified through runtime API",
      ],
    });
  } catch (reason) {
    await finish({
      ...baseResult(),
      error: reason instanceof Error ? reason.message : String(reason),
    });
  } finally {
    unsubscribe?.();
  }
}
