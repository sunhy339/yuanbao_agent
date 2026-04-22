import type {
  AppConfig,
  PolicyConfig,
  ProviderConfig,
  SearchConfig,
  ToolRuntimeConfig,
  UiConfig,
  WorkspaceConfig,
} from "./config";
import type {
  ApprovalRecord,
  CommandLogRecord,
  Identifier,
  PatchRecord,
  SessionRecord,
  TaskRecord,
  WorkspaceRef,
} from "./domain";

export interface JsonRpcRequest<TParams = unknown> {
  jsonrpc: "2.0";
  id: Identifier;
  method: RpcMethod;
  params: TParams;
}

export interface JsonRpcResponse<TResult = unknown> {
  jsonrpc: "2.0";
  id: Identifier;
  result?: TResult;
  error?: RpcError;
}

export interface RpcError {
  code:
    | "INVALID_ARGUMENT"
    | "NOT_FOUND"
    | "PATH_OUT_OF_SCOPE"
    | "PERMISSION_DENIED"
    | "APPROVAL_REQUIRED"
    | "TOOL_EXECUTION_FAILED"
    | "COMMAND_TIMEOUT"
    | "PATCH_APPLY_FAILED"
    | "MODEL_PROVIDER_ERROR"
    | "TOKEN_BUDGET_EXCEEDED"
    | "TASK_CANCELLED"
    | "DB_ERROR"
    | "INTERNAL_ERROR";
  message: string;
  details?: Record<string, unknown>;
  retryable: boolean;
}

export type RpcMethod =
  | "workspace.open"
  | "session.create"
  | "session.get"
  | "session.list"
  | "message.send"
  | "task.get"
  | "task.cancel"
  | "approval.submit"
  | "config.get"
  | "config.update"
  | "diff.get"
  | "command_log.get";

export interface WorkspaceOpenParams {
  path: string;
}

export interface SessionCreateParams {
  workspaceId: Identifier;
  title: string;
}

export interface MessageSendParams {
  sessionId: Identifier;
  content: string;
  attachments: string[];
}

export interface TaskGetParams {
  taskId: Identifier;
}

export interface TaskCancelParams {
  taskId: Identifier;
}

export interface ApprovalSubmitParams {
  approvalId: Identifier;
  decision: "approved" | "rejected";
}

export interface DiffGetParams {
  patchId: Identifier;
}

export interface CommandLogGetParams {
  commandId: Identifier;
}

export interface WorkspaceOpenResult {
  workspace: WorkspaceRef;
}

export interface SessionCreateResult {
  session: SessionRecord;
}

export interface SessionGetResult {
  session: SessionRecord;
}

export interface SessionListResult {
  sessions: SessionRecord[];
}

export interface MessageSendResult {
  task: TaskRecord;
}

export interface TaskGetResult {
  task: TaskRecord;
}

export interface ApprovalSubmitResult {
  approval: ApprovalRecord;
}

export interface ConfigGetResult {
  config: AppConfig;
}

export interface ConfigUpdateResult {
  config: AppConfig;
}

export interface ConfigUpdateParams {
  provider?: Partial<ProviderConfig>;
  workspace?: Partial<WorkspaceConfig>;
  search?: Partial<SearchConfig>;
  policy?: Partial<PolicyConfig>;
  tools?: {
    runCommand?: Partial<ToolRuntimeConfig>;
  };
  ui?: Partial<UiConfig>;
}

export interface DiffGetResult {
  patch: PatchRecord;
}

export interface CommandLogGetResult {
  commandLog: CommandLogRecord;
}
