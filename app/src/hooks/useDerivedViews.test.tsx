import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useDerivedViews, type UseDerivedViewsDeps } from "./useDerivedViews";

afterEach(() => {
  cleanup();
});

function makeDeps(overrides: Partial<UseDerivedViewsDeps> = {}): UseDerivedViewsDeps {
  return {
    config: {
      provider: { mode: "mock" },
      policy: { approvalMode: "ask" },
      workspace: { rootPath: "D:/py/test_pro", writableRoots: ["D:/py/test_pro"] },
    } as any,
    hostStatus: { runtimeRunning: false } as any,
    providerSettings: {
      mode: "mock",
      name: "Mock",
      model: "gpt-5",
      apiKey: "",
      apiKeyEnvVarName: "",
      baseUrl: "",
      apiFormat: "openai",
      maxTokens: 0,
      maxContextTokens: 0,
      timeout: 0,
      temperature: 0,
      profiles: [],
    } as any,
    providerTestResult: null,
    activeProviderProfileId: "mock",
    activeProviderProfile: null,
    activeTab: { id: "system:new-session", kind: "new-session", title: "新建会话", closable: true } as any,
    activeTabId: "system:new-session",
    session: null,
    sessions: [],
    task: null,
    activeTaskId: null,
    taskHistory: [],
    events: [],
    traceEvents: [],
    commandLogCacheById: {},
    patchCacheById: {},
    chatMessages: [],
    workspace: {
      id: "ws_test",
      name: "test_pro",
      rootPath: "D:/py/test_pro",
      createdAt: 1,
      updatedAt: 1,
    } as any,
    workspacePath: "D:/py/snake_game",
    scheduledRecords: [],
    scheduledLogs: [],
    skills: [],
    mcpServers: [],
    agentProfiles: [],
    loading: false,
    error: null,
    ...overrides,
  };
}

describe("useDerivedViews", () => {
  it("uses the launch workspace path on the new-session tab", () => {
    const { result } = renderHook(() => useDerivedViews(makeDeps()));

    expect(result.current.workspaceName).toBe("snake_game");
    expect(result.current.cwdLabel).toBe("D:/py/snake_game");
  });

  it("does not carry an existing session context preview into the new-session tab", () => {
    const { result } = renderHook(() => useDerivedViews(makeDeps({
      session: {
        id: "sess_existing",
        workspaceId: "ws_test",
        title: "Existing",
        status: "active",
        workspaceRoot: "D:/py/yuanbao_agent",
        createdAt: 1,
        updatedAt: 2,
        metadata: {
          contextPreview: {
            workspaceRoot: "D:/py/yuanbao_agent",
            budgetStats: {
              estimatedInputTokens: 75_720,
              maxContextTokens: 256_000,
              updatedAt: 12345,
              estimated: false,
            },
          },
        },
      } as any,
      task: {
        id: "task_existing",
        sessionId: "sess_existing",
        type: "chat",
        status: "running",
        goal: "existing task",
        currentStep: "旧会话步骤",
        createdAt: 1,
        updatedAt: 2,
      } as any,
      activeTaskId: "task_existing",
      events: [
        {
          eventId: "evt_existing_context",
          sessionId: "sess_existing",
          taskId: "task_existing",
          type: "task.updated",
          ts: 2000,
          payload: {
            context: {
              workspaceRoot: "D:/py/yuanbao_agent",
              budgetStats: {
                estimatedInputTokens: 75_720,
                maxContextTokens: 256_000,
              },
            },
          },
        },
      ],
    })));

    expect(result.current.sessionContextPreview).toBeUndefined();
    expect(result.current.contextStatusLabel).toBe("-- 上下文");
  });
});
