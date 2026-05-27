import type {
  McpServerRecord,
  SessionRecord,
  SkillPresetRecord,
  TaskRecord,
} from "@shared";
import { RuntimeClient } from "../lib/runtimeClient";
import {
  appendAssistantPlaceholder,
  appendUserMessage,
  failAssistantMessage,
  reconcileBackendMessage,
  stopStreamingMessages,
  updatePendingMessageTask,
} from "../state/chatMessages";
import { formatRuntimeModeLabel, formatStatusLabel } from "../ui/copy";
import {
  DEFAULT_SESSION_TITLE,
} from "../state/constants";
import { dispatchSlashCommand, SLASH_COMMANDS } from "../state/slashCommands";
import { openSessionTab } from "../ui/workbench/tabModel";
import {
  isTaskControllable,
} from "../state/providerConfig";
import {
  upsertRecord,
  type QueuedPromptSubmission,
} from "../state/eventRecordViews";
import {
  messageRecordToChatMessageLocal,
} from "../state/chatTokenHelpers";
import { shouldPromoteTaskToActive } from "../state/sessionDerivedViews";
import type { HookDeps } from "./types";

const runtimeClient = new RuntimeClient();
const SUPPLEMENTABLE_TASK_STATUSES = new Set(["running", "planning", "verifying", "waiting_approval", "queued", "paused"]);

function canReceiveSupplement(status?: string | null) {
  return Boolean(status && SUPPLEMENTABLE_TASK_STATUSES.has(status));
}

export interface UseMessageActionsDeps extends HookDeps {
  // State
  prompt: string;
  setPrompt: React.Dispatch<React.SetStateAction<string>>;
  promptAttachments: string[];
  setPromptAttachments: React.Dispatch<React.SetStateAction<string[]>>;
  queuedPromptSubmissions: QueuedPromptSubmission[];
  setQueuedPromptSubmissions: React.Dispatch<React.SetStateAction<QueuedPromptSubmission[]>>;
  chatMessages: any[];
  setChatMessages: React.Dispatch<React.SetStateAction<any[]>>;
  messageBusy: boolean;
  setMessageBusy: React.Dispatch<React.SetStateAction<boolean>>;
  task: TaskRecord | null;
  setTask: (task: TaskRecord | null) => void;
  activeTaskId: string | null;
  session: SessionRecord | null;
  setSession: React.Dispatch<React.SetStateAction<SessionRecord | null>>;
  sessions: SessionRecord[];
  setSessions: React.Dispatch<React.SetStateAction<SessionRecord[]>>;
  openTabs: any[];
  setOpenTabs: React.Dispatch<React.SetStateAction<any[]>>;
  setActiveTabId: React.Dispatch<React.SetStateAction<any>>;
  hostStatus: any;
  config: any;
  providerSettings: any;
  setProviderSettings: React.Dispatch<React.SetStateAction<any>>;
  loading: boolean;

  // From hooks
  taskHistory: TaskRecord[];
  setTaskHistory: React.Dispatch<React.SetStateAction<TaskRecord[]>>;
  setActiveTaskForSession: (taskId: string | null, sessionId?: string | null) => void;
  setActiveTaskId: React.Dispatch<React.SetStateAction<string | null>>;
  setApprovalBusyId: React.Dispatch<React.SetStateAction<string | null>>;
  setTaskControlError: React.Dispatch<React.SetStateAction<string | null>>;
  setSessionBusy: React.Dispatch<React.SetStateAction<boolean>>;

  // Functions from other hooks
  persistSearchConfig: () => Promise<any>;
  ensureWorkspace: () => Promise<any>;
  clearPendingAssistantTokens: () => void;
  handleTaskControl: (action: any, taskId?: string) => Promise<void>;
  handleRefreshMcpTools: () => Promise<void>;
  refreshSkills: () => void;
  sessionTitle: string;

  // Derived values
  activeTab: any;
  activeSessionRecord: SessionRecord | null;
  runtimeReady: boolean;
  visibleChatMessages: any[];
  workspace: any;
  mcpServers: McpServerRecord[];
  skills: SkillPresetRecord[];
}

export function useMessageActions(deps: UseMessageActionsDeps) {
  const {
    addToast, toastError, setError,
    prompt, setPrompt,
    promptAttachments, setPromptAttachments,
    queuedPromptSubmissions, setQueuedPromptSubmissions,
    setChatMessages,
    messageBusy, setMessageBusy,
    task, setTask,
    activeTaskId,
    session, setSession,
    sessions, setSessions,
    openTabs, setOpenTabs, setActiveTabId,
    hostStatus, config, providerSettings, setProviderSettings,
    loading,
    taskHistory, setTaskHistory,
    setActiveTaskForSession, setActiveTaskId,
    setApprovalBusyId, setTaskControlError, setSessionBusy,
    persistSearchConfig, ensureWorkspace,
    clearPendingAssistantTokens, handleTaskControl,
    handleRefreshMcpTools, refreshSkills,
    sessionTitle,
    activeTab, activeSessionRecord,
    runtimeReady,
    visibleChatMessages,
    workspace,
    mcpServers, skills,
  } = deps;

  function getErrorMessage(reason: unknown): string {
    return reason instanceof Error ? reason.message : String(reason);
  }

  async function ensureSessionForSend(): Promise<SessionRecord> {
    const nextWorkspace = await ensureWorkspace();
    const result = await runtimeClient.createSession({
      workspaceId: nextWorkspace.id,
      title: sessionTitle.trim() || DEFAULT_SESSION_TITLE,
    });
    setSessions((current) => upsertRecord(current, result.session));
    setSession(result.session);
    setOpenTabs((current) => {
      const resultTabs = openSessionTab(current, result.session);
      setActiveTabId(resultTabs.activeTabId);
      return resultTabs.tabs;
    });
    return result.session;
  }

  async function sendMessageContent(
    messageContentInput: string,
    messageAttachmentsInput: string[],
    options: { clearComposer?: boolean; mode?: "new" | "supplement" | "queued" } = {},
  ) {
    if (!messageContentInput.trim() && messageAttachmentsInput.length === 0) {
      setError("Enter a task description before sending.");
      return;
    }

    setMessageBusy(true);
    setError(null);
    let pendingAssistantMessageIdForCatch: string | null = null;
    let pendingSessionIdForCatch: string | null = null;

    try {
      await persistSearchConfig();
      const activeSession =
        activeTab.kind === "session"
          ? activeSessionRecord ?? (await ensureSessionForSend())
          : await ensureSessionForSend();
      pendingSessionIdForCatch = activeSession.id;
      const messageContent = messageContentInput.trim() || "Please review the attached file.";
      const messageAttachments = messageAttachmentsInput;
      const messageCreatedAt = Date.now();
      const clientMessageId = `client_${messageCreatedAt}`;
      const pendingUserMessageId = `user_${messageCreatedAt}`;
      const pendingAssistantMessageId = `assistant_pending_${messageCreatedAt}`;
      const taskCanReceiveSupplement = canReceiveSupplement(task?.status) && Boolean(task?.id);
      const requestedMode = options.mode === "supplement" && !taskCanReceiveSupplement ? "new" : options.mode;
      const shouldCreateAssistantPlaceholder = requestedMode !== "supplement";
      pendingAssistantMessageIdForCatch = shouldCreateAssistantPlaceholder ? pendingAssistantMessageId : null;
      const currentTaskIdBeforeSend = task?.id ?? null;
      if (shouldCreateAssistantPlaceholder) {
        clearPendingAssistantTokens();
      }
      if (options.clearComposer ?? true) {
        setPrompt("");
        setPromptAttachments([]);
      }
      setChatMessages((current) =>
        shouldCreateAssistantPlaceholder
          ? appendAssistantPlaceholder(
              appendUserMessage(current, {
                id: pendingUserMessageId,
                sessionId: activeSession.id,
                content: messageContent,
                now: messageCreatedAt,
                clientMessageId,
              }),
              {
                id: pendingAssistantMessageId,
                sessionId: activeSession.id,
                content: "思考中...",
                now: messageCreatedAt + 1,
              },
            )
          : appendUserMessage(current, {
              id: pendingUserMessageId,
              sessionId: activeSession.id,
              content: messageContent,
              now: messageCreatedAt,
              clientMessageId,
            }),
      );
      setApprovalBusyId(null);

      const result = await runtimeClient.sendMessage({
        sessionId: activeSession.id,
        content: messageContent,
        attachments: messageAttachments,
        mode: requestedMode,
        taskId: requestedMode === "supplement" ? task?.id ?? activeTaskId ?? undefined : undefined,
        newTask: requestedMode === "new" ? true : undefined,
        clientMessageId,
      });

      if (result.userMessage) {
        const chatMsg = messageRecordToChatMessageLocal(result.userMessage);
        if (chatMsg) {
          setChatMessages((current) => reconcileBackendMessage(current, chatMsg));
        }
      } else {
        setChatMessages((current) => updatePendingMessageTask(current, pendingUserMessageId, result.task.id));
      }
      if (shouldCreateAssistantPlaceholder && result.assistantMessage) {
        const chatMsg = messageRecordToChatMessageLocal(result.assistantMessage);
        if (chatMsg) {
          setChatMessages((current) => reconcileBackendMessage(current, chatMsg));
        }
      } else if (shouldCreateAssistantPlaceholder) {
        setChatMessages((current) => updatePendingMessageTask(current, pendingAssistantMessageId, result.task.id));
      }
      setTaskHistory((current) => upsertRecord(current, result.task));
      if (shouldPromoteTaskToActive(result.task, currentTaskIdBeforeSend)) {
        setTask(result.task);
        setActiveTaskForSession(result.task.id, activeSession.id);
      }
      const touchedSession = { ...activeSession, updatedAt: result.task.updatedAt };
      setSessions((current) => upsertRecord(current, touchedSession));
      setSession(touchedSession);
      setOpenTabs((current) => {
        const resultTabs = openSessionTab(current, touchedSession);
        setActiveTabId(resultTabs.activeTabId);
        return resultTabs.tabs;
      });
    } catch (reason) {
      if (pendingAssistantMessageIdForCatch || options.mode === "supplement") {
        const errorSummary = getErrorMessage(reason);
        setChatMessages((current) =>
          failAssistantMessage(current, {
            messageId: pendingAssistantMessageIdForCatch,
            sessionId: pendingSessionIdForCatch ?? activeSessionRecord?.id ?? session?.id ?? "pending",
            taskId: task?.id ?? activeTaskId ?? undefined,
            content: `发送失败：${errorSummary}`,
            now: Date.now(),
            appendOnly: options.mode === "supplement" && !pendingAssistantMessageIdForCatch,
          }),
        );
      }
      toastError(reason);
    } finally {
      setMessageBusy(false);
    }
  }

  const composerCanStop = isTaskControllable(task?.status);
  const composerHasStreamingMessage = visibleChatMessages.some((message) => message.streaming);
  const composerSending = messageBusy || composerCanStop || (composerHasStreamingMessage && composerCanStop);

  async function handleSendMessage() {
    if (!prompt.trim() && promptAttachments.length === 0) {
      setError("Enter a task description before sending.");
      return;
    }

    const slashResult = dispatchSlashCommand(prompt);
    if (slashResult) {
      setPrompt("");
      handleSlashCommand(slashResult);
      return;
    }

    await sendMessageContent(prompt, promptAttachments, {
      clearComposer: true,
      mode: canReceiveSupplement(task?.status) ? "supplement" : "new",
    });
  }

  function handleQueuePrompt() {
    if (!prompt.trim() && promptAttachments.length === 0) {
      setError("Enter a task description before queueing.");
      return;
    }

    const slashResult = dispatchSlashCommand(prompt);
    if (slashResult) {
      setError("Slash commands cannot be queued.");
      return;
    }

    const queued: QueuedPromptSubmission = {
      id: `queued_${Date.now()}`,
      content: prompt.trim() || "Please review the attached file.",
      attachments: promptAttachments,
    };
    setQueuedPromptSubmissions((current) => [...current, queued]);
    setPrompt("");
    setPromptAttachments([]);
    setError(null);
  }

  function formatMcpSummary(servers: McpServerRecord[]): string {
    if (servers.length === 0) {
      return "暂无 MCP 服务器。可以在侧边栏的 **MCP** 页签中添加。";
    }
    const lines = servers.map((s) => {
      const status = s.enabled ? "已启用" : "已停用";
      const transport = s.transport ?? "stdio";
      const detail = s.url ?? s.command ?? "";
      return `- **${s.name}** (${status}) — ${transport}${detail ? `: ${detail}` : ""}`;
    });
    const enabled = servers.filter((s) => s.enabled).length;
    return `**MCP 服务器**（${enabled}/${servers.length} 已启用）\n\n${lines.join("\n")}\n\n_使用 \`/mcp refresh\` 重新发现工具。_`;
  }

  function formatSkillsSummary(skillList: SkillPresetRecord[]): string {
    if (skillList.length === 0) {
      return "暂无技能预设。可以在侧边栏的 **技能** 页签中创建。";
    }
    const lines = skillList.map((s) => {
      const builtin = s.isBuiltin || s.is_builtin ? " [内置]" : "";
      const cat = s.category ? ` (${s.category})` : "";
      return `- **${s.name}**${cat}${builtin} — ${(s.description || "").slice(0, 80)}`;
    });
    return `**技能**（${skillList.length} 个预设）\n\n${lines.join("\n")}`;
  }

  function addSystemMessage(markdown: string) {
    const now = Date.now();
    const systemMessageId = `system_${now}`;
    setChatMessages((current) => [
      ...current,
      {
        id: systemMessageId,
        sessionId: session?.id ?? "",
        taskId: "system",
        role: "assistant" as const,
        content: markdown,
        createdAt: now,
        updatedAt: now,
      },
    ]);
  }

  function handleStopPrompt() {
    clearPendingAssistantTokens();
    setChatMessages((current) => stopStreamingMessages(current, session?.id));
    setMessageBusy(false);
    setSessionBusy(false);
    setError(null);
    setTaskControlError(null);
    if (task && isTaskControllable(task.status)) {
      void handleTaskControl("cancel", task.id);
    }
  }

  async function handleSlashCommand(cmd: ReturnType<typeof dispatchSlashCommand>) {
    if (!cmd) return;

    // Compute status text for /status command
    const hostStatusText = hostStatus?.runtimeRunning ? "本地运行时已连接" : "浏览器预览模式";

    switch (cmd.kind) {
      case "init": {
        const activeWorkspace = workspace ?? (await ensureWorkspace());
        try {
          const result = await runtimeClient.initWorkspaceMemory({ workspaceId: activeWorkspace.id });
          const created = result.createdFiles ?? [];
          const existing = result.existingFiles ?? [];
          const lines: string[] = ["**Workspace memory initialized**"];
          if (created.length > 0) {
            lines.push("", `Created: ${created.map((name) => `\`${name}\``).join(", ")}`);
          }
          if (existing.length > 0) {
            lines.push("", `Already existed: ${existing.map((name) => `\`${name}\``).join(", ")}`);
          }
          if (created.length === 0 && existing.length === 0) {
            lines.push("", "No memory files were created.");
          }
          addSystemMessage(lines.join("\n"));
          addToast("success", "Initialized workspace memory files.");
        } catch (err: unknown) {
          addSystemMessage(`Initialization failed: ${err instanceof Error ? err.message : String(err)}`);
        }
        break;
      }
      case "help": {
        const lines = SLASH_COMMANDS.map(
          (c) => `**${c.name}**${c.argsHint ? ` ${c.argsHint}` : ""} - ${c.description}`,
        );
        const helpText = `**可用命令：**\n\n${lines.join("\n")}`;
        addSystemMessage(helpText);
        break;
      }
      case "clear":
        setChatMessages([]);
        addToast("success", "聊天已清空");
        break;
      case "compact": {
        if (!session) {
          addSystemMessage("没有活跃会话，无法压缩上下文。");
          break;
        }
        try {
          const result = await runtimeClient.compactSession({ sessionId: session.id });
          if (result.strategy === "none" || result.tokensBefore === 0) {
            addSystemMessage("会话消息为空，无需压缩。");
          } else {
            const saved = result.tokensBefore - result.tokensAfter;
            addSystemMessage(
              `**上下文压缩完成**\n\n` +
              `- 策略：${result.strategy}\n` +
              `- 压缩前：${result.tokensBefore} tokens\n` +
              `- 压缩后：${result.tokensAfter} tokens\n` +
              `- 节省：${saved > 0 ? saved : 0} tokens` +
              (result.summary ? `\n\n**摘要：**\n${result.summary.slice(0, 500)}` : ""),
            );
          }
        } catch (err: unknown) {
          addSystemMessage(`压缩失败：${err instanceof Error ? err.message : String(err)}`);
        }
        break;
      }
      case "status": {
        const statusLines: string[] = [];
        statusLines.push(`**运行时：** ${hostStatusText}`);
        if (hostStatus?.runtimeRunning) {
          statusLines.push(`**传输：** ${hostStatus.runtimeTransport}`);
        }
        statusLines.push(`**模型：** ${providerSettings.model || providerSettings.name || "未配置模型"}`);
        statusLines.push(`**会话：** ${session ? session.title : "无"}`);
        if (task) {
          statusLines.push(`**任务：** ${task.id} - ${formatStatusLabel(task.status)}`);
        }
        addSystemMessage(statusLines.join("\n"));
        break;
      }
      case "model":
        if (cmd.args) {
          setProviderSettings((current: any) => ({ ...current, model: cmd.args }));
          addToast("success", `模型已切换为：${cmd.args}`);
        } else {
          addSystemMessage(
            `**当前模型：** ${providerSettings.model || providerSettings.name || "未配置模型"}`,
          );
        }
        break;
      case "config": {
        const configLines: string[] = [
          `**模式：** ${formatRuntimeModeLabel(providerSettings.mode)}`,
          `**基础 URL：** ${providerSettings.baseUrl || "默认"}`,
          `**模型：** ${providerSettings.model || "(默认)"}`,
          `**温度：** ${providerSettings.temperature}`,
          `**最大输出令牌：** ${providerSettings.maxTokens}`,
          `**最大上下文：** ${providerSettings.maxContextTokens}`,
          `**超时：** ${providerSettings.timeout}s`,
        ];
        addSystemMessage(configLines.join("\n"));
        break;
      }
      case "mcp": {
        if (cmd.args === "refresh") {
          addSystemMessage("正在刷新 MCP 工具...");
          handleRefreshMcpTools().then(() => {
            addSystemMessage(formatMcpSummary(mcpServers));
          });
        } else {
          addSystemMessage(formatMcpSummary(mcpServers));
        }
        break;
      }
      case "skills": {
        if (cmd.args === "refresh") {
          addSystemMessage("正在刷新技能...");
          refreshSkills();
        } else {
          addSystemMessage(formatSkillsSummary(skills));
        }
        break;
      }
    }
  }

  return {
    sendMessageContent,
    handleSendMessage,
    handleQueuePrompt,
    handleSlashCommand,
    handleStopPrompt,
    addSystemMessage,
    ensureSessionForSend,
    composerCanStop,
    composerHasStreamingMessage,
    composerSending,
    formatMcpSummary,
    formatSkillsSummary,
  };
}
