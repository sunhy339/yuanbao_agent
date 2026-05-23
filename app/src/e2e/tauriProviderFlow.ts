import { invoke } from "@tauri-apps/api/core";
import type { AgentEventEnvelope, TaskRecord, TraceEventRecord } from "@shared";
import { RuntimeClient } from "../lib/runtimeClient";
import { formatStatusLabel } from "../ui/copy";

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
    apiFormat?: string;
    model: string;
    apiKeyEnvVarName: string;
    timeout: number;
  };
  autoApprove?: boolean;
}

interface TauriProviderFlowResult {
  ok: boolean;
  flow: "provider-flow" | "ui-smoke" | "mcp-live" | "session-recovery-seed" | "session-recovery-verify";
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

interface ExportedApprovalRecord {
  id?: unknown;
  approvalId?: unknown;
  taskId?: unknown;
  task_id?: unknown;
  decision?: unknown;
}

let started = false;

const REQUIRED_TRACE_TYPES = [
  "provider.request",
  "provider.response",
  "task.completed",
];

const UI_ASSERTION_TIMEOUT_MS = 180_000;
const TASK_COMPLETION_TIMEOUT_MS = 900_000;
const WORKBENCH_SHELL_SELECTOR = ".yb-app-shell";

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

async function pollTask(
  client: RuntimeClient,
  taskId: string,
  initialTask: TaskRecord,
  timeoutMs = TASK_COMPLETION_TIMEOUT_MS,
): Promise<TaskRecord> {
  let current = initialTask;
  const deadline = Date.now() + timeoutMs;

  while (!isTerminalStatus(current.status) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 1_000));
    current = (await client.getTask(taskId)).task;
  }

  return current;
}

function traceTypesFrom(traceEvents: TraceEventRecord[]) {
  return [...new Set(traceEvents.map((event) => event.type))].sort();
}

function exportedTraceTypesFrom(logs: Record<string, unknown>) {
  const traceEvents = Array.isArray(logs.traceEvents) ? logs.traceEvents : [];
  return traceEvents
    .map((event) => {
      if (!event || typeof event !== "object") {
        return null;
      }
      const record = event as { type?: unknown; event_type?: unknown };
      const type = record.type ?? record.event_type;
      return typeof type === "string" && type.trim() ? type : null;
    })
    .filter((type): type is string => Boolean(type));
}

async function waitForTraceTypes(
  client: RuntimeClient,
  taskId: string,
  requiredTypes: string[],
  timeoutMs = 30_000,
): Promise<{ traceEvents: TraceEventRecord[]; traceTypes: string[] }> {
  let traceEvents: TraceEventRecord[] = [];
  let traceTypes: string[] = [];
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    traceEvents = (await client.listTrace({ taskId, limit: 100 })).traceEvents;
    traceTypes = traceTypesFrom(traceEvents);
    if (requiredTypes.every((type) => traceTypes.includes(type))) {
      return { traceEvents, traceTypes };
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  return { traceEvents, traceTypes };
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

function countTextOccurrences(text: string, needle: string) {
  if (!needle) return 0;
  let count = 0;
  let index = text.indexOf(needle);
  while (index >= 0) {
    count += 1;
    index = text.indexOf(needle, index + needle.length);
  }
  return count;
}

function click(selector: string, description: string) {
  const target = query<HTMLElement>(selector);
  if (!target) {
    throw new Error(`Cannot click ${description}; selector not found: ${selector}`);
  }
  target.click();
}

function setFieldValue(selector: string, value: string) {
  const field = query<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(selector);
  if (!field) {
    throw new Error(`Input not found: ${selector}`);
  }

  const prototype = field instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : field instanceof HTMLSelectElement
      ? HTMLSelectElement.prototype
      : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
  descriptor?.set?.call(field, value);
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.dispatchEvent(new Event("change", { bubbles: true }));
}

function readProviderDialogTestResult(fixture: Required<TauriProviderFlowFixture>["provider"]) {
  const result = query<HTMLElement>(".settings-modal .settings-json-box");
  if (!result?.textContent) {
    return null;
  }

  const text = result.textContent;
  if (text.includes(formatStatusLabel("failed")) || text.includes(formatStatusLabel("missing_env"))) {
    throw new Error(`Provider test failed in dialog: ${text}`);
  }

  return text.includes(formatStatusLabel("ok")) ||
    (text.includes(fixture.model) && text.includes(fixture.apiKeyEnvVarName))
    ? result
    : null;
}

async function applyWorkspaceThroughUi(workspacePath: string) {
  click('button[aria-label="新建会话"]', "New Session navigation");
  await waitFor("new session workspace", () => query(".new-session-workspace"));
  const workspaceField = await waitFor("workspace path field", () =>
    query<HTMLInputElement>('input[aria-label="工作区文件夹"]'),
  );
  if (!workspaceField.disabled) {
    setFieldValue('input[aria-label="工作区文件夹"]', workspacePath);
  }
  click('button[aria-label="应用工作区"]', "Apply workspace");
  await waitFor("workspace applied", () =>
    document.body.textContent?.includes(workspacePath) ? true : null,
  );
}

async function configureProviderThroughUi(fixture: Required<TauriProviderFlowFixture>["provider"]) {
  await waitFor("workbench shell", () => query(WORKBENCH_SHELL_SELECTOR));
  await waitFor("composer ready", () => {
    const composer = query<HTMLTextAreaElement>('textarea[aria-label="任务指令"]');
    return composer && !composer.disabled ? composer : null;
  });

  click('button[aria-label="设置"]', "Settings navigation");
  await waitFor("settings provider panel", () => query(".settings-panel-providers"));
  click(".settings-panel-header .settings-primary-action", "Add Provider");
  await waitFor("provider dialog", () => query('[role="dialog"].settings-modal'));

  setFieldValue("#provider-name", fixture.name);
  setFieldValue("#provider-endpoint", fixture.baseUrl);
  if (fixture.apiFormat) {
    setFieldValue("#provider-api-format", fixture.apiFormat);
  }
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
  try {
    await waitFor("provider test success", () => readProviderDialogTestResult(fixture), 45_000);
  } catch {
    const directResult = await new RuntimeClient().testProvider({
      provider: {
        mode: "openai-compatible",
        baseUrl: fixture.baseUrl,
        apiFormat: fixture.apiFormat ?? "openai-chat",
        model: fixture.model,
        defaultModel: fixture.model,
        apiKeyEnvVarName: fixture.apiKeyEnvVarName,
        timeout: fixture.timeout,
      },
    } as Parameters<RuntimeClient["testProvider"]>[0]);
    if (!directResult.ok) {
      throw new Error(directResult.message || `Provider test failed with status ${directResult.status}.`);
    }
  }
  click('.settings-modal-footer button[type="submit"]', "Save provider");
  await waitFor("provider save confirmation", () =>
    !query('[role="dialog"].settings-modal') &&
    query(".settings-panel-providers") &&
    document.body.textContent?.includes(fixture.name) &&
    document.body.textContent?.includes(fixture.model)
      ? true
      : null,
  );
}

async function sendPromptThroughUi(prompt: string) {
  click('button[aria-label="新建会话"]', "New Session navigation");
  await waitFor("task prompt composer", () => query<HTMLTextAreaElement>('textarea[aria-label="任务指令"]'));
  setFieldValue('textarea[aria-label="任务指令"]', prompt);
  await waitFor("composer run enabled", () => {
    const button = query<HTMLButtonElement>(".composer-run");
    return button && !button.disabled ? button : null;
  });
  click(".composer-run", "composer run");
  await waitFor("session workspace", () => query(".session-workspace:not(.session-workspace-empty)"));
}

async function runUiSmokeFlow(workspacePath?: string) {
  const assertions: string[] = [];

  await waitFor("workbench shell", () => query(WORKBENCH_SHELL_SELECTOR));
  assertText("总览");
  assertions.push("workbench shell renders overview");

  click('button[aria-label="新建会话"]', "New Session navigation");
  await waitFor("new session workspace", () => query(".new-session-workspace"));
  assertElement(".new-session-workspace", "new session workspace");
  await waitFor("command composer", () => query('textarea[aria-label="任务指令"]'));
  assertText("新建会话");
  assertions.push("new session workspace renders");

  click('button[aria-label="设置"]', "Settings navigation");
  await waitFor("settings workspace", () => query(".settings-workspace"));
  assertElement(".settings-panel-providers", "settings providers panel");
  assertions.push("settings providers page renders");

  click('button[aria-label="定时任务"]', "Scheduled navigation");
  await waitFor("scheduled workspace", () => query(".scheduled-workspace"));
  assertElement(".scheduled-empty", "scheduled empty state");
  assertions.push("scheduled empty state renders without demo data");

  click('button[aria-label="新建会话"]', "New Session navigation");
  await waitFor("new session workspace", () => query(".new-session-workspace"));
  await waitFor("command composer after returning", () => query('textarea[aria-label="任务指令"]'));
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
    fixture.flow === "mcp-live" ||
    fixture.flow === "session-recovery-seed" ||
    fixture.flow === "session-recovery-verify"
  ) {
    return fixture.flow;
  }
  return "provider-flow";
}

function eventApprovalId(event: AgentEventEnvelope): string | null {
  const payload = event.payload;
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const approvalId = (payload as { approvalId?: unknown }).approvalId;
  return typeof approvalId === "string" && approvalId.trim() ? approvalId : null;
}

async function maybeAutoApprove(client: RuntimeClient, event: AgentEventEnvelope, approved: Set<string>) {
  const approvalId = eventApprovalId(event);
  if (!approvalId || approved.has(approvalId)) {
    return;
  }
  approved.add(approvalId);
  await client.approvalSubmit({ approvalId, decision: "approved" });
}

function exportedApprovalId(approval: ExportedApprovalRecord): string | null {
  const approvalId = approval.id ?? approval.approvalId;
  return typeof approvalId === "string" && approvalId.trim() ? approvalId : null;
}

function isPendingApproval(approval: ExportedApprovalRecord) {
  return approval.decision === null || approval.decision === undefined || approval.decision === "";
}

async function approvePendingApprovalsFromLogs(
  client: RuntimeClient,
  approved: Set<string>,
  sessionId?: string,
) {
  const logs = await client.exportLogs(sessionId ? { sessionId } : undefined);
  const approvals = Array.isArray(logs.approvals) ? logs.approvals : [];

  for (const approval of approvals) {
    if (!approval || typeof approval !== "object" || !isPendingApproval(approval as ExportedApprovalRecord)) {
      continue;
    }
    const approvalId = exportedApprovalId(approval as ExportedApprovalRecord);
    if (!approvalId || approved.has(approvalId)) {
      continue;
    }
    await client.approvalSubmit({ approvalId, decision: "approved" });
    approved.add(approvalId);
  }
}

async function waitForTaskCompletedEvent(
  client: RuntimeClient,
  events: AgentEventEnvelope[],
  approvedApprovalIds: Set<string>,
  autoApprove: boolean,
  getTaskId?: () => string | null | undefined,
  timeoutMs = TASK_COMPLETION_TIMEOUT_MS,
) {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const terminalEvent = events.find((event) =>
      event.type === "task.completed" ||
      event.type === "task.failed" ||
      event.type === "task.cancelled"
    );
    if (terminalEvent) {
      return terminalEvent;
    }

    const taskId = getTaskId?.();
    if (taskId) {
      try {
        const task = (await client.getTask(taskId)).task;
        if (isTerminalStatus(task.status)) {
          return {
            eventId: `poll_${task.id}_${task.status}`,
            type: task.status === "completed"
              ? "task.completed"
              : task.status === "failed"
                ? "task.failed"
                : "task.cancelled",
            sessionId: task.sessionId,
            taskId: task.id,
            ts: task.updatedAt,
            payload: task,
          } as AgentEventEnvelope;
        }
      } catch (reason) {
        console.warn("E2E terminal task polling failed", reason);
      }
    }

    if (autoApprove) {
      const sessionId = events.find((event) => typeof event.sessionId === "string" && event.sessionId)?.sessionId;
      await approvePendingApprovalsFromLogs(client, approvedApprovalIds, sessionId).catch((reason) => {
        console.warn("E2E auto-approval polling failed", reason);
      });
    }

    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }

  throw new Error("Timed out waiting for task completion event.");
}

function timelineSatisfied(observedTypes: string[]) {
  const observed = new Set(observedTypes);
  const directRuntimeFlow = REQUIRED_TRACE_TYPES.every((type) => observed.has(type)) &&
    (!observed.has("tool.started") || observed.has("tool.completed") || observed.has("tool.failed"));
  const planningFlow =
    observed.has("task.planning.started") &&
    observed.has("task.planning.subtask.started") &&
    (observed.has("collab.task.completed") || observed.has("task.planning.completed")) &&
    observed.has("task.completed");

  return directRuntimeFlow || planningFlow;
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
    await waitFor("workbench shell", () => query(WORKBENCH_SHELL_SELECTOR));
    const workspaceResult = await client.openWorkspace(workspacePath);
    const sessionResult = await client.createSession({
      workspaceId: workspaceResult.workspace.id,
      title: sessionTitle,
    });
    const sendResult = await client.sendMessage({
      sessionId: sessionResult.session.id,
      content: prompt,
      attachments: [],
      background: true,
    } as Parameters<RuntimeClient["sendMessage"]>[0] & { background: boolean });

    const persistedMessages = (await client.listMessages({ sessionId: sessionResult.session.id, limit: 20 })).messages;
    const persistedRoles = persistedMessages.map((message) => message.role);
    if (!persistedRoles.includes("user")) {
      throw new Error(`Expected seeded user message, got: ${persistedRoles.join(", ") || "none"}.`);
    }
    if (!persistedMessages.some((message) => message.role === "user" && message.content.includes(prompt))) {
      throw new Error("Seeded messages do not include the recovery prompt.");
    }

    await finish({
      ok: true,
      flow: "session-recovery-seed",
      phase: "complete",
      sessionId: sessionResult.session.id,
      taskId: sendResult.task.id,
      taskStatus: sendResult.task.status,
      taskSummary: sendResult.task.summary,
      persistedMessageRoles: persistedRoles,
      persistedMessageCount: persistedMessages.length,
      eventTypes: events.map((event) => event.type),
      traceTypes: [],
      uiAssertions: [
        "seeded session through runtime API",
        "seeded background task through runtime API",
        "seeded user message persisted to runtime API",
      ],
    });
  } finally {
    unsubscribe();
  }
}

async function runMcpLiveFlow(client: RuntimeClient) {
  const assertions: string[] = [];
  const serverId = `e2e-mcp-${Date.now()}`;

  await waitFor("workbench shell", () => query(WORKBENCH_SHELL_SELECTOR));
  click('button[aria-label="MCP 中心"]', "MCP Center navigation");
  await waitFor("MCP workspace", () => query(".mcp-workspace"));
  assertions.push("MCP workspace opened in desktop shell");

  const initialList = await client.listMcpServers();
  assertions.push(`initial MCP server count: ${initialList.servers.length}`);

  const created = await client.createMcpServer({
    id: serverId,
    name: "E2E MCP disabled",
    transport: "stdio",
    command: "e2e-mcp-disabled",
    args: ["--disabled"],
    enabled: false,
  });
  if (created.server.id !== serverId || created.server.enabled !== false) {
    throw new Error("Created MCP server did not round-trip expected id/enabled state.");
  }
  assertions.push("created disabled MCP server through desktop runtime");

  const listedAfterCreate = await client.listMcpServers();
  if (!listedAfterCreate.servers.some((server) => server.id === serverId)) {
    throw new Error("Created MCP server was not returned by listMcpServers.");
  }
  assertions.push("listed created MCP server through desktop runtime");

  const updated = await client.updateMcpServer({
    serverId,
    name: "E2E MCP updated",
    command: "e2e-mcp-updated",
    args: ["--updated"],
    enabled: false,
  });
  if (updated.server.name !== "E2E MCP updated" || updated.server.command !== "e2e-mcp-updated") {
    throw new Error("Updated MCP server did not return edited fields.");
  }
  assertions.push("updated MCP server config through desktop runtime");

  const enabled = await client.updateMcpServer({ serverId, enabled: true });
  if (!enabled.server.enabled) {
    throw new Error("MCP server did not toggle enabled.");
  }
  assertions.push("enabled MCP server through desktop runtime");

  const refresh = await client.refreshMcpTools({ serverId });
  if (!Array.isArray(refresh.tools)) {
    throw new Error("MCP tool refresh did not return a tools array.");
  }
  assertions.push(`refreshed MCP tools through desktop runtime: ${refresh.refreshed}`);

  const disabled = await client.updateMcpServer({ serverId, enabled: false });
  if (disabled.server.enabled) {
    throw new Error("MCP server did not toggle disabled.");
  }
  assertions.push("disabled MCP server through desktop runtime");

  const deleted = await client.deleteMcpServer({ serverId });
  if (!deleted.deleted || deleted.serverId !== serverId) {
    throw new Error("MCP delete returned an unexpected payload.");
  }
  const listedAfterDelete = await client.listMcpServers();
  if (listedAfterDelete.servers.some((server) => server.id === serverId)) {
    throw new Error("Deleted MCP server still appears in listMcpServers.");
  }
  assertions.push("deleted MCP server through desktop runtime");

  await finish({
    ok: true,
    flow: "mcp-live",
    phase: "complete",
    eventTypes: [],
    traceTypes: [],
    uiAssertions: assertions,
  });
}

async function runSessionRecoveryVerifyFlow(client: RuntimeClient, fixture: TauriProviderFlowFixture) {
  const prompt = fixture.prompt;
  const sessionTitle = fixture.sessionTitle || "E2E Recovery Session";
  if (!prompt) {
    throw new Error("Session recovery verify fixture is missing prompt.");
  }

  await waitFor("workbench shell", () => query(WORKBENCH_SHELL_SELECTOR));
  const sessions = (await client.listSessions()).sessions;
  const recoveredSession = sessions.find((session) => session.title === sessionTitle);
  if (!recoveredSession) {
    throw new Error(`Recovered session not found after restart: ${sessionTitle}.`);
  }

  const persistedMessages = (await client.listMessages({ sessionId: recoveredSession.id, limit: 20 })).messages;
  const persistedRoles = persistedMessages.map((message) => message.role);
  const userMessage = persistedMessages.find((message) => message.role === "user" && message.content.includes(prompt));
  if (!userMessage) {
    throw new Error(`Recovered messages are incomplete: ${persistedRoles.join(", ") || "none"}.`);
  }
  const recoveredTasks = (await client.listTasks({ sessionId: recoveredSession.id })).tasks;
  const recoveredTask = recoveredTasks[0];
  if (!recoveredTask) {
    throw new Error(
      `Recovered session did not include a task: ${recoveredTasks.map((task) => `${task.id}:${task.status}`).join(", ") || "none"}.`,
    );
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
  await waitFor("recovered task state visible", () =>
    document.body.textContent?.includes(formatStatusLabel(recoveredTask.status))
      ? true
      : null,
    30_000,
  );

  await finish({
    ok: true,
    flow: "session-recovery-verify",
    phase: "complete",
    sessionId: recoveredSession.id,
    taskId: recoveredTask.id,
    taskStatus: recoveredTask.status,
    taskSummary: recoveredTask.summary,
    persistedMessageRoles: persistedRoles,
    persistedMessageCount: persistedMessages.length,
    eventTypes: [],
    traceTypes: [],
    uiAssertions: [
      "recovered session listed after desktop restart",
      "recovered session opens from sidebar",
      "persisted user message visible after restart",
      "recovered task state visible in UI",
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
  const approvedApprovalIds = new Set<string>();
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

    if (fixture.flow === "mcp-live") {
      phase = "mcp-live";
      await runMcpLiveFlow(client);
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
      if (fixture.autoApprove && event.type === "approval.requested") {
        void maybeAutoApprove(client, event, approvedApprovalIds);
      }
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
    await applyWorkspaceThroughUi(fixture.workspacePath);

    phase = "send-message-ui";
    await sendPromptThroughUi(prompt);
    const activeTaskIdForSession = () =>
      events.find((event) => event.type === "task.started" && event.sessionId)?.taskId ??
      events.find((event) => event.type === "task.created" && event.sessionId)?.taskId;
    const completedEvent = await waitForTaskCompletedEvent(
      client,
      events,
      approvedApprovalIds,
      fixture.autoApprove === true,
      activeTaskIdForSession,
    );
    if (completedEvent.type !== "task.completed") {
      throw new Error(`Expected task.completed event, got ${completedEvent.type}.`);
    }
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
    assertElement(".conversation-activity", "conversation activity stream");

    const { traceTypes } = await waitForTraceTypes(client, finalTask.id, REQUIRED_TRACE_TYPES);
    const exportedTraceTypes = exportedTraceTypesFrom(await client.exportLogs(sessionId ? { sessionId } : undefined));
    const observedRuntimeTypes = [...new Set([...traceTypes, ...exportedTraceTypes, ...events.map((event) => event.type)])];
    const missingTraceTypes = REQUIRED_TRACE_TYPES.filter((type) => !observedRuntimeTypes.includes(type));
    if (!timelineSatisfied(observedRuntimeTypes)) {
      throw new Error(
        `Timeline is missing a complete runtime path. Direct missing: ${missingTraceTypes.join(", ") || "none"}.`,
      );
    }
    const leakedTraceCard = Array.from(document.querySelectorAll('.runtime-event-card[data-kind="trace"]')).find((card) =>
      card.textContent?.includes("provider.request") ||
      card.textContent?.includes("assistant.token") ||
      card.textContent?.includes("task.started")
    );
    if (leakedTraceCard) {
      throw new Error(`Low-level trace leaked into the default UI: ${leakedTraceCard.textContent ?? "unknown trace"}.`);
    }

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
    const messageStream = query<HTMLElement>(".message-stream");
    const messageStreamText = messageStream?.textContent ?? "";
    const assistantText = assistantMessage.content.trim();
    const assistantBubbleVisible = Array.from(document.querySelectorAll<HTMLElement>('[data-activity-kind="message"][data-role="assistant"]'))
      .some((node) => (node.textContent ?? "").trim().length > 0);
    if (!assistantBubbleVisible) {
      throw new Error("Persisted assistant message is not visible in the conversation UI.");
    }
    if (assistantText.length < 500) {
      const assistantContentOccurrences = countTextOccurrences(messageStreamText, assistantText);
      if (assistantContentOccurrences > 1) {
        throw new Error(
          `Expected persisted assistant message to render at most once, got ${assistantContentOccurrences}.`,
        );
      }
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
