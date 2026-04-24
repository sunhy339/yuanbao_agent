import { invoke } from "@tauri-apps/api/core";
import type { AgentEventEnvelope, TaskRecord, TraceEventRecord } from "@shared";
import { RuntimeClient } from "../lib/runtimeClient";

interface TauriProviderFlowFixture {
  enabled: boolean;
  flow?: string;
  workspacePath?: string;
  prompt?: string;
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
  flow: "provider-flow" | "ui-smoke";
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
  const flow = fixture.flow === "ui-smoke" ? "ui-smoke" : "provider-flow";

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

    if (fixture.flow !== "provider-flow") {
      throw new Error(`Unsupported E2E flow: ${fixture.flow ?? "unknown"}`);
    }
    if (!fixture.provider || !fixture.workspacePath || !fixture.prompt) {
      throw new Error("E2E fixture is missing provider, workspacePath, or prompt.");
    }

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
    await sendPromptThroughUi(fixture.prompt);
    const completedEvent = await waitFor("task completed event", () =>
      events.find((event) => event.type === "task.completed"),
    );
    const startedEvent = events.find((event) => event.taskId === completedEvent.taskId && event.type === "task.started");
    const finalTask = await pollTask(
      client,
      completedEvent.taskId,
      (await client.getTask(completedEvent.taskId)).task,
    );

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
      sessionId: completedEvent.sessionId || startedEvent?.sessionId,
      taskId: finalTask.id,
      taskStatus: finalTask.status,
      taskSummary: finalTask.resultSummary,
      eventTypes: events.map((event) => event.type),
      traceTypes,
      uiAssertions: [
        "provider settings saved through UI",
        "provider test result visible in UI",
        "composer submitted through UI",
        "session task completion visible in UI",
        "runtime timeline rendered in UI",
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
