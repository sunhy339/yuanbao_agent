import { invoke } from "@tauri-apps/api/core";
import type { AgentEventEnvelope, AppConfig, TaskRecord, TraceEventRecord } from "@shared";
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
  flow: "provider-flow";
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

function isTerminalStatus(status: TaskRecord["status"]) {
  return status === "completed" || status === "failed" || status === "cancelled";
}

function buildProviderConfig(fixture: Required<TauriProviderFlowFixture>["provider"]): AppConfig["provider"] {
  const profile = {
    id: fixture.profileId,
    name: fixture.name,
    mode: "openai-compatible" as const,
    baseUrl: fixture.baseUrl,
    model: fixture.model,
    defaultModel: fixture.model,
    fallbackModel: fixture.model,
    apiKeyEnvVarName: fixture.apiKeyEnvVarName,
    temperature: 0.2,
    maxTokens: 4000,
    maxOutputTokens: 4000,
    maxContextTokens: 120000,
    timeout: fixture.timeout,
  };

  return {
    ...profile,
    activeProfileId: profile.id,
    profiles: [profile],
  };
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

  const baseResult = (): TauriProviderFlowResult => ({
    ok: false,
    flow: "provider-flow",
    phase,
    eventTypes: events.map((event) => event.type),
    traceTypes: [],
  });

  try {
    if (fixture.flow !== "provider-flow") {
      throw new Error(`Unsupported E2E flow: ${fixture.flow ?? "unknown"}`);
    }
    if (!fixture.provider || !fixture.workspacePath || !fixture.prompt) {
      throw new Error("E2E fixture is missing provider, workspacePath, or prompt.");
    }

    unsubscribe = await client.subscribeEvents((event) => {
      events.push(event);
    });

    phase = "save-provider-config";
    const providerConfig = buildProviderConfig(fixture.provider);
    const configResult = await client.updateConfig({
      config: {
        provider: providerConfig,
      },
    });
    const activeProfileId = configResult.config.provider.activeProfileId;
    if (activeProfileId !== fixture.provider.profileId) {
      throw new Error(`Expected active provider ${fixture.provider.profileId}, got ${activeProfileId ?? "none"}.`);
    }

    phase = "test-provider";
    const providerResult = await client.testProvider({ profileId: fixture.provider.profileId });
    if (!providerResult.ok) {
      throw new Error(providerResult.message || `Provider test failed with status ${providerResult.status}.`);
    }

    phase = "open-workspace";
    const workspace = (await client.openWorkspace(fixture.workspacePath)).workspace;

    phase = "create-session";
    const session = (await client.createSession({
      workspaceId: workspace.id,
      title: "Tauri E2E provider flow",
    })).session;

    phase = "send-message";
    const taskResult = await client.sendMessage({
      sessionId: session.id,
      content: fixture.prompt,
      attachments: [],
    });
    const finalTask = await pollTask(client, taskResult.task.id, taskResult.task);

    phase = "assert-timeline";
    if (finalTask.status !== "completed") {
      throw new Error(`Expected completed task, got ${finalTask.status}.`);
    }

    const traceEvents = (await client.listTrace({ taskId: finalTask.id, limit: 100 })).traceEvents;
    const traceTypes = traceTypesFrom(traceEvents);
    const missingTraceTypes = REQUIRED_TRACE_TYPES.filter((type) => !traceTypes.includes(type));
    if (missingTraceTypes.length > 0) {
      throw new Error(`Timeline is missing trace types: ${missingTraceTypes.join(", ")}.`);
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
      sessionId: session.id,
      taskId: finalTask.id,
      taskStatus: finalTask.status,
      taskSummary: finalTask.resultSummary,
      eventTypes: events.map((event) => event.type),
      traceTypes,
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
