import type { ConversationActivityItem, RuntimeTimelineItem, SessionWorkspaceMessage } from "../../workbench/workspaces/session/types";

export type CleanTranscriptKind =
  | "user_text"
  | "assistant_text"
  | "assistant_progress"
  | "thinking"
  | "tool_use"
  | "tool_result"
  | "tool_group"
  | "permission_request"
  | "computer_use_permission"
  | "ask_user_question"
  | "background_task"
  | "task_summary"
  | "plan_update"
  | "goal_event"
  | "memory_event"
  | "compact_summary"
  | "slash_command"
  | "api_retry"
  | "error"
  | "change_set"
  | "command"
  | "status"
  | "system";

export interface CleanTranscriptCapability {
  kind: CleanTranscriptKind;
  label: string;
  current: "mapped" | "partial" | "backend_needed";
  source: string;
}

export const CLEAN_TRANSCRIPT_CAPABILITIES: CleanTranscriptCapability[] = [
  { kind: "user_text", label: "用户消息", current: "mapped", source: "messages.role=user" },
  { kind: "assistant_text", label: "助手正文", current: "mapped", source: "messages.role=assistant" },
  { kind: "assistant_progress", label: "过程说明", current: "mapped", source: "assistant_progress / operational deltas" },
  { kind: "thinking", label: "模型思考", current: "partial", source: "thinking/status events; true token thinking needs backend deltas" },
  { kind: "tool_use", label: "工具调用", current: "mapped", source: "tool_use/tool_activity/toolCalls" },
  { kind: "tool_result", label: "工具结果", current: "mapped", source: "tool_result/tool_activity/toolCalls" },
  { kind: "tool_group", label: "工具分组/树", current: "partial", source: "worklog grouping; parentToolUseId plus toolGroupId/toolIndex/toolTotal/toolCategory/toolPhaseId/toolPhaseLabel and toolSemanticParentId/toolSemanticParentLabel semantic phases" },
  { kind: "permission_request", label: "权限/审批请求", current: "mapped", source: "permission_request/approvals" },
  { kind: "computer_use_permission", label: "Computer Use 权限", current: "partial", source: "computer_use approval events render and submit through approval.submit; dedicated desktop tool/modal still pending" },
  { kind: "ask_user_question", label: "向用户提问", current: "partial", source: "ask_user_question payload can render; answer submits as supplement/resume" },
  { kind: "background_task", label: "后台/子任务", current: "partial", source: "backgroundJobs/collaboration; child task stream needs backend" },
  { kind: "task_summary", label: "任务摘要", current: "partial", source: "legacy-only; runtime/task panel state, not main chat" },
  { kind: "plan_update", label: "计划更新", current: "partial", source: "runtime plan panel/replay state, not flat chat" },
  { kind: "goal_event", label: "Goal 事件", current: "backend_needed", source: "missing goal lifecycle chat event" },
  { kind: "memory_event", label: "记忆/上下文事件", current: "backend_needed", source: "missing memory lifecycle chat event" },
  { kind: "compact_summary", label: "上下文压缩", current: "backend_needed", source: "missing compact_summary chat event" },
  { kind: "slash_command", label: "Slash command", current: "mapped", source: "local slash command dispatcher" },
  { kind: "api_retry", label: "API 重试", current: "backend_needed", source: "missing api_retry chat event" },
  { kind: "error", label: "错误", current: "mapped", source: "failed messages/traces/runtime" },
  { kind: "change_set", label: "文件改动/diff", current: "mapped", source: "patches/changedFiles" },
  { kind: "command", label: "命令执行", current: "mapped", source: "commands/backgroundJobs/run_command" },
  { kind: "status", label: "运行状态", current: "mapped", source: "status/current step" },
  { kind: "system", label: "系统通知", current: "partial", source: "visible traces/system messages" },
];

export function transcriptKindForMessage(message: SessionWorkspaceMessage): CleanTranscriptKind {
  const metadataKind = typeof message.metadata?.kind === "string" ? message.metadata.kind : "";
  if (message.role === "user") return "user_text";
  if (metadataKind === "assistant_progress") return "assistant_progress";
  if (metadataKind === "assistant_thinking" || metadataKind === "thinking" || message.kind === "thinking" || message.kind === "status") {
    return "thinking";
  }
  if (metadataKind === "permission_request") return "permission_request";
  if (metadataKind === "tool_use" || metadataKind === "tool_activity") return "tool_use";
  if (metadataKind === "tool_result") return "tool_result";
  if (metadataKind === "computer_use_permission_request" || metadataKind === "computer_use_permission") return "computer_use_permission";
  if (metadataKind === "ask_user_question") return "ask_user_question";
  if (metadataKind === "background_task" || metadataKind === "agent_task_group") return "background_task";
  if (metadataKind === "task_summary") return "task_summary";
  if (metadataKind === "plan_update") return "plan_update";
  if (metadataKind === "goal_event") return "goal_event";
  if (metadataKind === "memory_event") return "memory_event";
  if (metadataKind === "compact_summary") return "compact_summary";
  if (metadataKind === "slash_command") return "slash_command";
  if (metadataKind === "api_retry") return "api_retry";
  if (metadataKind === "status") return "status";
  if (metadataKind === "system") return "system";
  if (message.kind === "failure" || message.status === "failed") return "error";
  if (message.role === "system") return "system";
  return "assistant_text";
}

export function transcriptKindForRuntime(item: RuntimeTimelineItem): CleanTranscriptKind {
  if (item.kind === "patch" || item.kind === "task") return "change_set";
  if (item.kind === "approval") return "permission_request";
  if (item.kind === "command") return "command";
  if (item.kind === "tool") return "tool_use";
  if (item.kind === "trace") {
    const status = item.status?.toLowerCase();
    return status && ["failed", "error"].includes(status) ? "error" : "system";
  }
  return "status";
}

export function transcriptKindForActivity(item: ConversationActivityItem): CleanTranscriptKind {
  if (item.kind === "message") return transcriptKindForMessage(item.message);
  if (item.kind === "worklog") return "tool_group";
  return transcriptKindForRuntime(item.runtime);
}
