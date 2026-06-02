import type {
  McpServerRecord,
  SessionRecord,
  SkillPresetRecord,
  TaskRecord,
} from "@shared";
import { RuntimeClient } from "../lib/runtimeClient";
import {
  appendAssistantPlaceholder,
  appendSpecialEventMessage,
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
const PROMPT_FILE_REFERENCE_PATTERN = /(^|\s)@([^\s@]+)/g;
const PROMPT_FILE_REFERENCE_TRAILING = /[),.;:!?，。；：！？）]+$/u;
const PROMPT_FILE_REFERENCE_TERMINATOR = /[，。；！？]/u;

function canReceiveSupplement(status?: string | null) {
  return Boolean(status && SUPPLEMENTABLE_TASK_STATUSES.has(status));
}

function normalizePromptFileReference(value: string) {
  return value
    .trim()
    .replace(/^["'`]+|["'`]+$/g, "")
    .split(PROMPT_FILE_REFERENCE_TERMINATOR)[0]
    .replace(PROMPT_FILE_REFERENCE_TRAILING, "")
    .replace(/\\/g, "/")
    .trim();
}

export function extractPromptFileReferences(content: string): string[] {
  const references: string[] = [];
  const seen = new Set<string>();
  for (const match of content.matchAll(PROMPT_FILE_REFERENCE_PATTERN)) {
    const reference = normalizePromptFileReference(match[2] ?? "");
    if (!reference || seen.has(reference)) continue;
    seen.add(reference);
    references.push(reference);
  }
  return references;
}

export function buildPromptAttachmentsWithReferences(content: string, attachments: string[]) {
  const fileReferences = extractPromptFileReferences(content);
  const nextAttachments: string[] = [];
  const seen = new Set<string>();
  [...attachments, ...fileReferences].forEach((item) => {
    const normalized = item.replace(/\\/g, "/").trim();
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    nextAttachments.push(normalized);
  });
  return { attachments: nextAttachments, fileReferences };
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
      const messageReferences = buildPromptAttachmentsWithReferences(messageContent, messageAttachmentsInput);
      const messageAttachments = messageReferences.attachments;
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
                metadata: messageAttachments.length ? { attachments: messageAttachments } : undefined,
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
              metadata: messageAttachments.length ? { attachments: messageAttachments } : undefined,
            }),
      );
      setApprovalBusyId(null);

      const result = await runtimeClient.sendMessage({
        sessionId: activeSession.id,
        content: messageContent,
        attachments: messageAttachments,
        fileReferences: messageReferences.fileReferences,
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

  function handleQueuePrompt(mode: "queued" | "supplement" = "queued") {
    if (!prompt.trim() && promptAttachments.length === 0) {
      setError("Enter a task description before queueing.");
      return;
    }

    const slashResult = dispatchSlashCommand(prompt);
    if (slashResult) {
      setError("Slash commands cannot be queued.");
      return;
    }

    const queuedReferences = buildPromptAttachmentsWithReferences(prompt.trim(), promptAttachments);
    const queued: QueuedPromptSubmission = {
      id: `queued_${Date.now()}`,
      content: prompt.trim() || "Please review the attached file.",
      attachments: queuedReferences.attachments,
      mode,
      createdAt: Date.now(),
    };
    setQueuedPromptSubmissions((current) => [...current, queued]);
    setPrompt("");
    setPromptAttachments([]);
    setError(null);
  }

  function handleQuoteMessage(text: string) {
    const quote = text.trim();
    if (!quote) return;
    setPrompt((current) => {
      const prefix = current.trim() ? `${current.trimEnd()}\n\n` : "";
      return `${prefix}${quote}\n\n`;
    });
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

  function addSlashCommandMessage(
    command: string,
    markdown: string,
    options: {
      args?: string;
      eventId?: string;
      status?: "completed" | "running" | "failed" | "warning";
      summary?: string;
      title?: string;
    } = {},
  ) {
    const now = Date.now();
    const normalizedCommand = command.startsWith("/") ? command : `/${command}`;
    const safeEventId = options.eventId ??
      `${normalizedCommand.replace(/[^a-z0-9]+/gi, "_").replace(/^_+|_+$/g, "") || "command"}_${now}`;
    setChatMessages((current) =>
      appendSpecialEventMessage(current, {
        kind: "slash_command",
        sessionId: session?.id ?? "",
        taskId: task?.id ?? "system",
        content: markdown,
        title: options.title ?? `${normalizedCommand} result`,
        summary: options.summary,
        status: options.status ?? "completed",
        eventId: safeEventId,
        metadata: {
          command: normalizedCommand,
          args: options.args ?? "",
        },
        now,
      }),
    );
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
          addSlashCommandMessage("/init", lines.join("\n"), {
            args: cmd.args,
            summary: created.length > 0
              ? `Created ${created.length} workspace memory file${created.length === 1 ? "" : "s"}`
              : "Workspace memory files already exist",
          });
          addToast("success", "Initialized workspace memory files.");
        } catch (err: unknown) {
          addSlashCommandMessage("/init", `Initialization failed: ${err instanceof Error ? err.message : String(err)}`, {
            args: cmd.args,
            status: "failed",
            summary: "Initialization failed",
          });
        }
        break;
      }
      case "help": {
        const lines = SLASH_COMMANDS.map(
          (c) => `**${c.name}**${c.argsHint ? ` ${c.argsHint}` : ""} - ${c.description}`,
        );
        const helpText = `**可用命令：**\n\n${lines.join("\n")}`;
        addSlashCommandMessage("/help", helpText, {
          args: cmd.args,
          summary: `${SLASH_COMMANDS.length} commands available`,
        });
        break;
      }
      case "clear":
        setChatMessages([]);
        addToast("success", "聊天已清空");
        break;
      case "compact": {
        if (!session) {
          addSlashCommandMessage("/compact", "没有活跃会话，无法压缩上下文。", {
            args: cmd.args,
            status: "warning",
            summary: "No active session",
          });
          break;
        }
        try {
          const result = await runtimeClient.compactSession({ sessionId: session.id });
          if (result.strategy === "none" || result.tokensBefore === 0) {
            addSlashCommandMessage("/compact", "会话消息为空，无需压缩。", {
              args: cmd.args,
              summary: "No messages to compact",
            });
          } else {
            const saved = result.tokensBefore - result.tokensAfter;
            addSlashCommandMessage(
              "/compact",
              `**上下文压缩完成**\n\n` +
              `- 策略：${result.strategy}\n` +
              `- 压缩前：${result.tokensBefore} tokens\n` +
              `- 压缩后：${result.tokensAfter} tokens\n` +
              `- 节省：${saved > 0 ? saved : 0} tokens` +
              (result.summary ? `\n\n**摘要：**\n${result.summary.slice(0, 500)}` : ""),
              {
                args: cmd.args,
                summary: `Saved ${saved > 0 ? saved : 0} tokens`,
              },
            );
          }
        } catch (err: unknown) {
          addSlashCommandMessage("/compact", `压缩失败：${err instanceof Error ? err.message : String(err)}`, {
            args: cmd.args,
            status: "failed",
            summary: "Compaction failed",
          });
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
        addSlashCommandMessage("/status", statusLines.join("\n"), {
          args: cmd.args,
          summary: hostStatusText,
        });
        break;
      }
      case "model":
        if (cmd.args) {
          setProviderSettings((current: any) => ({ ...current, model: cmd.args }));
          addSlashCommandMessage("/model", `**模型已切换：** ${cmd.args}`, {
            args: cmd.args,
            summary: cmd.args,
          });
          addToast("success", `模型已切换为：${cmd.args}`);
        } else {
          const modelName = providerSettings.model || providerSettings.name || "未配置模型";
          addSlashCommandMessage("/model", `**当前模型：** ${modelName}`, {
            args: cmd.args,
            summary: modelName,
          });
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
        addSlashCommandMessage("/config", configLines.join("\n"), {
          args: cmd.args,
          summary: `${formatRuntimeModeLabel(providerSettings.mode)} · ${providerSettings.model || "(默认)"}`,
        });
        break;
      }
      case "mcp": {
        if (cmd.args === "refresh") {
          const eventId = `mcp_refresh_${Date.now()}`;
          addSlashCommandMessage("/mcp", "正在刷新 MCP 工具...", {
            args: cmd.args,
            eventId,
            status: "running",
            summary: "Refreshing MCP tools",
          });
          handleRefreshMcpTools()
            .then(() => {
              addSlashCommandMessage("/mcp", "MCP 工具刷新请求已完成。", {
                args: cmd.args,
                eventId,
                summary: "Refresh completed",
              });
            })
            .catch((err: unknown) => {
              addSlashCommandMessage("/mcp", `MCP 工具刷新失败：${err instanceof Error ? err.message : String(err)}`, {
                args: cmd.args,
                eventId,
                status: "failed",
                summary: "Refresh failed",
              });
            });
        } else {
          const enabled = mcpServers.filter((server) => server.enabled).length;
          addSlashCommandMessage("/mcp", formatMcpSummary(mcpServers), {
            args: cmd.args,
            summary: `${enabled}/${mcpServers.length} enabled`,
          });
        }
        break;
      }
      case "skills": {
        if (cmd.args === "refresh") {
          const eventId = `skills_refresh_${Date.now()}`;
          addSlashCommandMessage("/skills", "正在刷新技能...", {
            args: cmd.args,
            eventId,
            status: "running",
            summary: "Refreshing skills",
          });
          try {
            refreshSkills();
            addSlashCommandMessage("/skills", "技能刷新请求已完成。", {
              args: cmd.args,
              eventId,
              summary: "Refresh completed",
            });
          } catch (err: unknown) {
            addSlashCommandMessage("/skills", `技能刷新失败：${err instanceof Error ? err.message : String(err)}`, {
              args: cmd.args,
              eventId,
              status: "failed",
              summary: "Refresh failed",
            });
          }
        } else {
          addSlashCommandMessage("/skills", formatSkillsSummary(skills), {
            args: cmd.args,
            summary: `${skills.length} skill presets`,
          });
        }
        break;
      }
    }
  }

  return {
    sendMessageContent,
    handleSendMessage,
    handleQueuePrompt,
    handleQuoteMessage,
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
