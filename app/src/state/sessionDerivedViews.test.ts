import { describe, expect, it } from "vitest";
import type { AgentEventEnvelope, CommandLogRecord, TraceEventRecord } from "@shared";
import { buildSessionBackgroundJobs, buildSessionCollaboration, buildSessionContextPreview, mergeSessionBackgroundJobs } from "./sessionDerivedViews";

describe("buildSessionContextPreview", () => {
  it("keeps the active session workspace when newer context events belong to another session", () => {
    const preview = buildSessionContextPreview({
      events: [
        {
          eventId: "evt_other_context",
          sessionId: "sess_other",
          taskId: "task_other",
          type: "task.updated",
          ts: 3000,
          payload: {
            context: {
              workspaceRoot: "D:/py/yuanbao_agent",
              budgetStats: {
                estimatedInputTokens: 9999,
              },
            },
          },
        } as AgentEventEnvelope,
        {
          eventId: "evt_current_usage",
          sessionId: "sess_current",
          taskId: "task_current",
          type: "provider.response",
          ts: 2000,
          payload: {
            usage: {
              input_tokens: 1234,
              output_tokens: 56,
            },
          },
        } as AgentEventEnvelope,
      ],
      traceEvents: [],
      workspace: {
        id: "workspace",
        name: "yuanbao_agent",
        rootPath: "D:/py/yuanbao_agent",
        createdAt: 1000,
        updatedAt: 2000,
      },
      session: {
        id: "sess_current",
        workspaceId: "workspace_current",
        title: "New Session",
        workspaceRoot: "D:/py/test_pro",
        workspaceName: "test_pro",
        status: "active",
        createdAt: 1000,
        updatedAt: 2000,
      },
      activeTaskId: null,
      activeTask: null,
      maxContextTokens: 200000,
    });

    expect(preview?.workspaceRoot).toBe("D:/py/test_pro");
    expect(preview?.budgetStats?.inputTokens).toBe(1234);
    expect(preview?.budgetStats?.estimatedInputTokens).toBe(1234);
  });

  it("uses the latest session token usage instead of filtering only to the active task", () => {
    const preview = buildSessionContextPreview({
      events: [
        {
          eventId: "evt_usage_old",
          sessionId: "sess_1",
          taskId: "task_1",
          type: "provider.response",
          ts: 1000,
          payload: {
            usage: {
              input_tokens: 1200,
              output_tokens: 40,
              prompt_tokens_details: {
                cached_tokens: 200,
              },
            },
          },
        } as AgentEventEnvelope,
      ],
      traceEvents: [
        {
          id: "trace_usage_new",
          sessionId: "sess_1",
          taskId: "task_2",
          type: "provider.response",
          source: "provider",
          sequence: 2,
          createdAt: 2000,
          payload: {
            usage: {
              input_tokens: 3400,
              output_tokens: 80,
              prompt_tokens_details: {
                cached_tokens: 500,
              },
            },
          },
        } satisfies TraceEventRecord,
      ],
      workspace: null,
      session: null,
      activeTaskId: "task_2",
      activeTask: {
        id: "task_2",
        sessionId: "sess_1",
        type: "chat",
        status: "running",
        goal: "继续",
        createdAt: 1900,
        updatedAt: 2000,
      },
      maxContextTokens: 200000,
    });

    expect(preview?.budgetStats?.inputTokens).toBe(3400);
    expect(preview?.budgetStats?.cacheReadTokens).toBe(500);
    expect(preview?.budgetStats?.outputTokens).toBe(80);
    expect(preview?.budgetStats?.maxContextTokens).toBe(200000);
  });

  it("normalizes provider cache read usage from anthropic and responses shapes", () => {
    const preview = buildSessionContextPreview({
      events: [
        {
          eventId: "evt_usage_anthropic",
          sessionId: "sess_1",
          taskId: "task_1",
          type: "message_complete",
          ts: 3000,
          payload: {
            usage: {
              input_tokens: 2200,
              output_tokens: 90,
              cache_read_input_tokens: 1200,
              cache_creation_input_tokens: 64,
            },
          },
        } as AgentEventEnvelope,
      ],
      traceEvents: [
        {
          id: "trace_usage_responses",
          sessionId: "sess_1",
          taskId: "task_1",
          type: "provider.response",
          source: "provider",
          sequence: 1,
          createdAt: 2000,
          payload: {
            usage: {
              inputTokens: 1800,
              outputTokens: 70,
              input_tokens_details: {
                cached_tokens: 900,
              },
            },
          },
        } satisfies TraceEventRecord,
      ],
      workspace: null,
      session: null,
      activeTaskId: "task_1",
      activeTask: {
        id: "task_1",
        sessionId: "sess_1",
        type: "chat",
        status: "completed",
        goal: "cache",
        createdAt: 1000,
        updatedAt: 3000,
      },
      maxContextTokens: 200000,
    });

    expect(preview?.budgetStats?.inputTokens).toBe(2200);
    expect(preview?.budgetStats?.outputTokens).toBe(90);
    expect(preview?.budgetStats?.cacheReadTokens).toBe(1200);
  });

  it("keeps a context budget preview when only the session and model window are known", () => {
    const preview = buildSessionContextPreview({
      events: [],
      traceEvents: [],
      workspace: null,
      session: {
        id: "sess_current",
        workspaceId: "workspace_current",
        title: "New Session",
        workspaceRoot: "D:/py/test_pro",
        workspaceName: "test_pro",
        status: "active",
        createdAt: 1000,
        updatedAt: 2000,
      },
      activeTaskId: null,
      activeTask: null,
      maxContextTokens: 256000,
    });

    expect(preview?.workspaceRoot).toBe("D:/py/test_pro");
    expect(preview?.budgetStats?.estimatedTokens).toBe(0);
    expect(preview?.budgetStats?.estimatedInputTokens).toBe(0);
    expect(preview?.budgetStats?.maxContextTokens).toBe(256000);
    expect(preview?.budgetStats?.estimated).toBe(true);
  });

  it("reads a persisted context snapshot from session metadata when no live event is available", () => {
    const preview = buildSessionContextPreview({
      events: [],
      traceEvents: [],
      workspace: null,
      session: {
        id: "sess_snapshot",
        workspaceId: "workspace_snapshot",
        title: "Persisted context",
        workspaceRoot: "D:/py/test_pro",
        workspaceName: "test_pro",
        status: "active",
        createdAt: 1000,
        updatedAt: 2000,
        metadata: {
          contextPreview: {
            workspaceRoot: "D:/py/test_pro",
            toolCount: 7,
            budgetStats: {
              estimatedTokens: 6200,
              estimatedInputTokens: 6200,
              messageTokens: 5000,
              toolSchemaTokens: 1200,
              maxContextTokens: 256000,
              updatedAt: 12345,
              estimated: false,
              includedSections: ["system_prompt", "task_context"],
              promptLayers: [{ name: "role", tokenEstimate: 100 }],
            },
            taskFocus: {
              currentStep: "Review persisted context",
              acceptanceCriteriaCount: 2,
            },
          },
        },
      } as never,
      activeTaskId: null,
      activeTask: null,
      maxContextTokens: 256000,
    });

    expect(preview?.workspaceRoot).toBe("D:/py/test_pro");
    expect(preview?.toolCount).toBe(7);
    expect(preview?.budgetStats?.estimatedTokens).toBe(6200);
    expect(preview?.budgetStats?.estimatedInputTokens).toBe(6200);
    expect(preview?.budgetStats?.maxContextTokens).toBe(256000);
    expect(preview?.budgetStats?.updatedAt).toBe(12345);
    expect(preview?.budgetStats?.estimated).toBe(false);
    expect(preview?.taskFocus?.currentStep).toBe("Review persisted context");
  });
});

describe("buildSessionCollaboration", () => {
  it("surfaces structured child task attention signals", () => {
    const collaboration = buildSessionCollaboration(
      [],
      [
        {
          id: "trace_child_completed",
          sessionId: "sess_1",
          taskId: "child_1",
          type: "collab.task.completed",
          source: "runtime",
          sequence: 1,
          createdAt: 1778734169000,
          payload: {
            task: {
              id: "child_1",
              title: "Validate frontend",
              status: "completed",
              result: {
                summary: "Validation status: partial, with one blocker.",
                validationStatus: "partial",
              },
            },
          },
        } satisfies TraceEventRecord,
      ],
    );

    expect(collaboration.childTasks?.[0]?.status).toBe("completed");
    expect(collaboration.childTasks?.[0]?.attention).toContain("Validation status: partial");
  });

  it("keeps child tasks in creation order instead of latest-completed order", () => {
    const collaboration = buildSessionCollaboration(
      [],
      [
        {
          id: "trace_first",
          sessionId: "sess_1",
          taskId: "child_1",
          type: "collab.task.completed",
          source: "runtime",
          sequence: 1,
          createdAt: 1000,
          payload: {
            task: {
              id: "child_1",
              title: "Inspect README and source files",
              status: "completed",
              createdAt: 100,
              updatedAt: 400,
            },
          },
        } satisfies TraceEventRecord,
        {
          id: "trace_second",
          sessionId: "sess_1",
          taskId: "child_2",
          type: "collab.task.completed",
          source: "runtime",
          sequence: 2,
          createdAt: 1100,
          payload: {
            task: {
              id: "child_2",
              title: "Run the pytest suite",
              status: "completed",
              createdAt: 200,
              updatedAt: 300,
            },
          },
        } satisfies TraceEventRecord,
      ],
    );

    expect(collaboration.childTasks?.map((task) => task.title)).toEqual([
      "Inspect README and source files",
      "Run the pytest suite",
    ]);
  });

  it("surfaces planning subtask events as collaboration child tasks", () => {
    const collaboration = buildSessionCollaboration(
      [
        {
          eventId: "evt_planning_started",
          sessionId: "sess_1",
          taskId: "task_parent",
          type: "task.planning.subtask.started",
          ts: 1000,
          payload: {
            subtaskId: "subtask_frontend",
            subtaskTitle: "Audit session UI parity",
          },
        } as AgentEventEnvelope,
        {
          eventId: "evt_planning_completed",
          sessionId: "sess_1",
          taskId: "task_parent",
          type: "task.planning.subtask.completed",
          ts: 1500,
          payload: {
            subtaskId: "subtask_frontend",
            subtaskTitle: "Audit session UI parity",
            status: "completed",
            summary: "Found workspace and composer gaps.",
            duration_ms: 500,
          },
        } as AgentEventEnvelope,
      ],
      [],
    );

    expect(collaboration.childTasks).toHaveLength(1);
    expect(collaboration.childTasks?.[0]).toMatchObject({
      id: "subtask_frontend",
      title: "Audit session UI parity",
      status: "completed",
      summary: "Found workspace and composer gaps.",
      createdAt: 1000,
      completedAt: 1500,
      durationMs: 500,
    });
    expect(collaboration.results?.[0]?.summary).toBe("Found workspace and composer gaps.");
  });
});

describe("buildSessionBackgroundJobs", () => {
  it("preserves command tool metadata from command logs when trace events are unavailable", () => {
    const jobs = mergeSessionBackgroundJobs([], [
      {
        id: "cmd_1",
        taskId: "task_1",
        toolUseId: "call_command",
        parentToolUseId: "call_parent",
        toolGroupId: "tgrp_1",
        toolIndex: 1,
        toolTotal: 2,
        toolOperationId: "run_command",
        toolOperationLabel: "运行命令",
        toolCategory: "verification",
        toolPhaseId: "verification",
        toolPhaseLabel: "验证",
        toolSemanticParentId: "group:tgrp_1:phase:verification",
        toolSemanticParentLabel: "验证",
        target: "npm test",
        inputSummary: "npm test",
        command: "npm test",
        cwd: "app",
        shell: "powershell",
        background: true,
        status: "completed",
        exitCode: 0,
        startedAt: 1000,
        finishedAt: 1100,
        durationMs: 100,
      } satisfies CommandLogRecord,
    ]);

    expect(jobs[0]).toMatchObject({
      id: "cmd_1",
      toolUseId: "call_command",
      parentToolUseId: "call_parent",
      toolGroupId: "tgrp_1",
      toolIndex: 1,
      toolTotal: 2,
      toolOperationId: "run_command",
      toolOperationLabel: "运行命令",
      toolCategory: "verification",
      toolPhaseId: "verification",
      toolPhaseLabel: "验证",
      toolSemanticParentId: "group:tgrp_1:phase:verification",
      toolSemanticParentLabel: "验证",
      target: "npm test",
      inputSummary: "npm test",
      isBackground: true,
    });
  });

  it("merges command logs from multiple tasks into the same session history", () => {
    const jobs = mergeSessionBackgroundJobs([], [
      {
        id: "cmd_old",
        taskId: "task_1",
        command: "npm run typecheck",
        cwd: "app",
        status: "completed",
        exitCode: 0,
        startedAt: 1000,
        finishedAt: 1100,
      } satisfies CommandLogRecord,
      {
        id: "cmd_new",
        taskId: "task_2",
        command: "npm test",
        cwd: "app",
        status: "completed",
        exitCode: 0,
        startedAt: 2000,
        finishedAt: 2100,
      } satisfies CommandLogRecord,
    ]);

    expect(jobs.map((job) => job.id)).toEqual(["cmd_new", "cmd_old"]);
    expect(jobs.map((job) => job.command)).toEqual(["npm test", "npm run typecheck"]);
  });

  it("preserves command tool metadata from trace events for refresh recovery", () => {
    const jobs = buildSessionBackgroundJobs(
      [],
      [
        {
          id: "trace_command_started",
          sessionId: "sess_1",
          taskId: "task_1",
          type: "command.started",
          source: "command",
          sequence: 1,
          createdAt: 1000,
          payload: {
            commandId: "cmd_1",
            toolUseId: "call_command",
            toolName: "run_command",
            toolGroupId: "tgrp_1",
            toolIndex: 1,
            toolTotal: 2,
            toolCategory: "verification",
            toolPhaseId: "verification",
            toolPhaseLabel: "验证",
            toolSemanticParentId: "group:tgrp_1:phase:verification",
            toolSemanticParentLabel: "验证",
            target: "npm test",
            inputSummary: "npm test",
            command: "npm test",
            cwd: "app",
            shell: "powershell",
            status: "running",
          },
        } satisfies TraceEventRecord,
        {
          id: "trace_command_output",
          sessionId: "sess_1",
          taskId: "task_1",
          type: "command.output",
          source: "command",
          sequence: 2,
          createdAt: 1010,
          payload: {
            commandId: "cmd_1",
            toolUseId: "call_command",
            target: "npm test",
            inputSummary: "npm test",
            stream: "stdout",
            chunk: "3 passed\n",
          },
        } satisfies TraceEventRecord,
        {
          id: "trace_command_completed",
          sessionId: "sess_1",
          taskId: "task_1",
          type: "command.completed",
          source: "command",
          sequence: 3,
          createdAt: 1020,
          payload: {
            commandId: "cmd_1",
            toolUseId: "call_command",
            toolGroupId: "tgrp_1",
            toolIndex: 1,
            toolTotal: 2,
            toolCategory: "verification",
            toolPhaseId: "verification",
            toolPhaseLabel: "验证",
            toolSemanticParentId: "group:tgrp_1:phase:verification",
            toolSemanticParentLabel: "验证",
            target: "npm test",
            inputSummary: "npm test",
            command: "npm test",
            status: "completed",
            exitCode: 0,
          },
        } satisfies TraceEventRecord,
      ],
    );

    expect(jobs[0]).toMatchObject({
      id: "cmd_1",
      toolUseId: "call_command",
      toolGroupId: "tgrp_1",
      toolIndex: 1,
      toolTotal: 2,
      toolCategory: "verification",
      toolPhaseId: "verification",
      toolPhaseLabel: "验证",
      toolSemanticParentId: "group:tgrp_1:phase:verification",
      toolSemanticParentLabel: "验证",
      target: "npm test",
      inputSummary: "npm test",
      stdout: "3 passed\n",
      status: "completed",
      exitCode: 0,
    });
  });
});
