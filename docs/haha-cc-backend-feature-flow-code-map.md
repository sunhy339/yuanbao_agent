# haha-cc Backend Feature Flow Code Map

This document is the code-level comparison baseline for aligning Yuanbao's
backend flow and frontend output with haha-cc. It is not a request to copy
haha-cc byte-for-byte. The target principle is:

> The model decides the work flow through messages and tool calls. The backend
> enforces policy, executes tools, persists state, and projects typed events.
> The frontend renders typed events, not raw backend JSON.

## Why This Exists

Recent sessions showed several symptoms:

- Normal prompts were expanded into fixed inspect/apply/verify/report flows.
- The UI showed raw plan/approval/completion-review JSON.
- Tool and agent output differed between live streaming and session reload.
- Thinking appeared late, duplicated, or mixed with backend progress logs.
- Multi-agent cards used generic task names or internal IDs instead of clear
  user-facing task/agent names.
- Failure and completion-review states could emit repeated final/failure rows.

Those are not isolated UI defects. They come from flow and event-state
boundaries being too blurred.

## haha-cc Reference Shape

### 1. Transport And Messages

Reference code:

- `docs/cc-haha-main/src/server/ws/events.ts`
- `docs/cc-haha-main/src/server/ws/handler.ts`
- `docs/cc-haha-main/src/server/services/sessionService.ts`

Observed shape:

- WebSocket emits direct `ServerMessage` objects:
  `content_start`, `content_delta`, `tool_use_complete`, `tool_result`,
  `permission_request`, `thinking`, `status`, `message_complete`,
  `team_update`, `task_update`, etc.
- The handler is primarily a bridge around the CLI process and stream-json
  output. It does not invent a fixed task plan for every user turn.
- Session history is rebuilt from Claude JSONL transcript files, so replay is
  transcript-driven.

Yuanbao current code:

- `runtime/src/local_agent_runtime/models.py`
- `runtime/src/local_agent_runtime/event_bus.py`
- `runtime/src/local_agent_runtime/yuanbao_event_adapter.py`
- `runtime/src/local_agent_runtime/store/sqlite_store.py`
- `app/src/hooks/useEventSubscription.ts`
- `app/src/state/chatMessages.ts`

Gap:

- Yuanbao has a richer `AgentEventEnvelope` plus flat `yuanbao` / `hahaCc`
  projections. That is fine internally, but live and replay must use the same
  projection and visibility rules.
- Some runtime trace events can still become visible on reload even when they
  were hidden live.

Action:

- Keep `RuntimeEvent` / `AgentEventEnvelope` as source of truth for Yuanbao.
- Treat flat `YuanbaoServerMessage` as the chat protocol projection.
- Make live stream, `events.yuanbaoAfter`, trace replay, and message DB replay
  share one visibility contract.

### 2. Text Streaming And Finalization

Reference code:

- `docs/cc-haha-main/src/server/ws/events.ts`
- `docs/cc-haha-main/src/cli/transports/ccrClient.ts`
- `docs/cc-haha-main/src/components/messages/AssistantToolUseMessage.tsx`

haha-cc flow:

```text
assistant text block starts
  -> content_start(blockType=text)
  -> content_delta(text)
  -> message_complete(usage)
```

Yuanbao current code:

- `runtime/src/local_agent_runtime/orchestrator/provider_turn.py`
- `runtime/src/local_agent_runtime/orchestrator/task_lifecycle.py`
- `runtime/src/local_agent_runtime/yuanbao_event_adapter.py`
- `app/src/hooks/useEventSubscription.ts`
- `app/src/state/chatTokenHelpers.ts`

Gap:

- Realtime duplicate suppression for `message.delta` / `message.completed`
  exists, but the contract is still spread across adapter, event bus, and
  frontend token helpers.
- `message.created` is correctly envelope-only, but this should remain tested
  as part of the transcript contract.

Action:

- Lock one finalization rule: one assistant turn has at most one flat
  `message_complete`.
- Keep lifecycle events (`message.created`, DB message records) separate from
  flat text protocol events (`content_start`, `content_delta`,
  `message_complete`).

### 3. Thinking

Reference code:

- `docs/cc-haha-main/src/server/ws/events.ts`
- `docs/cc-haha-main/src/components/messages/AssistantThinkingMessage.tsx`

haha-cc shape:

- `thinking` is a model/provider thinking block.
- In normal view it can collapse to a small "Thinking" indicator.
- In verbose/transcript view it renders the thinking markdown.

Yuanbao current code:

- `runtime/src/local_agent_runtime/orchestrator/provider_turn.py`
- `runtime/src/local_agent_runtime/yuanbao_event_adapter.py`
- `shared/src/events.ts`
- `app/src/hooks/useEventSubscription.ts`
- `app/src/state/chatMessages.ts`

Gap:

- Yuanbao mixes real provider reasoning, synthetic progress, and backend phase
  logs under similar UI surfaces.
- Some providers cannot emit true token-level thinking, so backend-generated
  progress must not pretend to be provider thinking.

Action:

- `thinking`: only provider reasoning delta/summary or explicitly marked model
  thought summary.
- `assistant_progress` / `status`: backend phase/status heartbeat.
- Frontend should render them differently and avoid markdown-source-looking
  raw blocks for internal progress.

### 4. Tool Lifecycle

Reference code:

- `docs/cc-haha-main/src/components/messages/AssistantToolUseMessage.tsx`
- `docs/cc-haha-main/src/utils/groupToolUses.ts`
- Tool-specific UI files under `docs/cc-haha-main/src/tools/*/UI.*`

haha-cc flow:

```text
model emits tool_use
  -> content_start(blockType=tool_use, toolName, toolUseId)
  -> optional content_delta(toolInput)
  -> tool_use_complete(toolName, toolUseId, input)
backend executes tool
  -> tool_result(toolUseId, content, isError)
model receives tool result in next turn
```

Frontend behavior:

- Tools expose `userFacingName()`, `renderToolUseMessage()`,
  `renderToolUseTag()`, and optionally `renderGroupedToolUse()`.
- Read/search calls from the same assistant message can be grouped.
- Raw JSON is not the primary user-facing tool view.

Yuanbao current code:

- `runtime/src/local_agent_runtime/execution/tool_pipeline.py`
- `runtime/src/local_agent_runtime/orchestrator/react_runner.py`
- `runtime/src/local_agent_runtime/orchestrator/react_tool_helpers.py`
- `runtime/src/local_agent_runtime/orchestrator/publishing.py`
- `app/src/ui/workbench/workspaces/session/runtimeItemBuilder.ts`
- `app/src/state/viewComputations.ts`

Gap:

- Basic flat tool frames exist, but many rendered panels still depend on raw
  input/result JSON or generic summaries.
- Live and replay grouping can diverge because raw trace events and derived
  chat-compatible events are both available.
- Tool names and targets are sometimes formatted by heuristics rather than a
  stable tool presentation registry.

Action:

- Keep raw tool lifecycle in trace.
- Persist/replay only the chat-compatible tool frames that are intended for
  transcript display.
- Add a frontend/backend shared presentation contract per tool category:
  command, read, search, edit, patch, approval, agent, task, team.

### 5. Permissions And Approval

Reference code:

- `docs/cc-haha-main/src/server/ws/events.ts`
- `docs/cc-haha-main/src/cli/structuredIO.ts`
- `docs/cc-haha-main/src/components/messages/AssistantToolUseMessage.tsx`

haha-cc shape:

- Permission is tied to a tool use/request id.
- Duplicate permission responses are guarded.
- Permission cards are for real user decisions, not internal review gates.

Yuanbao current code:

- `runtime/src/local_agent_runtime/orchestrator/approval_flow.py`
- `runtime/src/local_agent_runtime/execution/tool_recovery.py`
- `runtime/src/local_agent_runtime/tools/ask_user_question.py`
- `app/src/hooks/useEventSubscription.ts`
- `app/src/state/viewComputations.ts`

Gap:

- `completion_review` has appeared as visible approval/detail JSON.
- `ask_user_question` and approval answers can look like user-authored new
  prompts if ownership/resolution is not strict.
- Plan approvals can still show JSON in detail.

Action:

- Permission cards only for real external decisions:
  write/edit, risky command, computer use, explicit plan approval.
- `completion_review` is an internal gate. If visible, it should be a compact
  panel audit, not a chat permission request.
- All approval/question responses are one-shot supplements to the existing
  task, never new user goals.

### 6. Plan Mode

Reference code:

- `docs/cc-haha-main/src/tools/EnterPlanModeTool/EnterPlanModeTool.ts`
- `docs/cc-haha-main/src/tools/ExitPlanModeTool/*`
- `docs/cc-haha-main/src/components/messages/PlanApprovalMessage.tsx`

haha-cc shape:

- Plan mode is entered through a model-visible tool or explicit user mode.
- In plan mode, write tools are restricted.
- Exiting plan mode produces a plan approval interaction.
- Backend does not create a fixed plan for every code task.

Yuanbao current code:

- `runtime/src/local_agent_runtime/tools/plan_mode.py`
- `runtime/src/local_agent_runtime/planner/service.py`
- `runtime/src/local_agent_runtime/orchestrator/message_execution.py`
- `runtime/src/local_agent_runtime/orchestrator/approval_flow.py`
- `app/src/state/viewComputations.ts`

Gap:

- Existing planner has been reduced, but the architecture still contains
  fallback scaffolding and plan approvals that can expose raw JSON.
- Some plan/swarm flows still use generated generic subtasks.

Action:

- Explicit plan requests and true model/tool `enter_plan_mode` may create plan
  UI.
- Normal ReAct turns should not receive root inspect/apply/verify scaffolds.
- Plan approval UI should render subtask cards/sections, not raw JSON.

### 7. Tasks / Todo

Reference code:

- `docs/cc-haha-main/src/tools/TaskCreateTool/TaskCreateTool.ts`
- `docs/cc-haha-main/src/tools/TaskUpdateTool/TaskUpdateTool.ts`
- `docs/cc-haha-main/src/server/services/taskService.ts`
- `docs/cc-haha-main/src/components/TaskListV2.tsx`

haha-cc shape:

- Task creation/update is model-driven through `TaskCreate` / `TaskUpdate`.
- Task status is `pending`, `in_progress`, `completed`.
- Task files are persisted separately from chat messages.
- Task tools do not primarily render raw tool-use JSON.

Yuanbao current code:

- `runtime/src/local_agent_runtime/tools/task.py`
- `runtime/src/local_agent_runtime/planner/decomposer.py`
- `runtime/src/local_agent_runtime/planner/dag_executor.py`
- `runtime/src/local_agent_runtime/services/collaboration_service.py`
- `runtime/src/local_agent_runtime/services/worker_runner.py`
- `runtime/src/local_agent_runtime/yuanbao_event_adapter.py`

Gap:

- Yuanbao has task and collaboration records, but user-facing task cards can be
  created by backend planning/decomposition rather than model-decided task
  tools.
- Internal IDs like `ctask_*` can leak into visible panels.
- Generic subtasks can still appear when decomposition falls back or when the
  router selects orchestration too eagerly.

Action:

- Treat `task_create` / `task_update` style events as UI lifecycle outputs.
- Expose internal IDs only in metadata/tooltips, never as primary titles.
- Only decompose when the model explicitly calls `agent`/`task`/plan tooling or
  the user asks for multi-agent/decomposition.

### 8. Agent / Subagent

Reference code:

- `docs/cc-haha-main/src/tools/AgentTool/AgentTool.tsx`
- `docs/cc-haha-main/src/tools/AgentTool/agentToolUtils.ts`
- `docs/cc-haha-main/src/tasks/LocalAgentTask/LocalAgentTask.tsx`
- `docs/cc-haha-main/src/tools/TaskOutputTool/TaskOutputTool.tsx`

haha-cc shape:

- `AgentTool` is a model-callable tool.
- Inputs include `description`, `prompt`, optional `subagent_type`, model,
  background mode, team name, name, cwd, and isolation.
- Outputs distinguish sync completion, async launch, teammate spawn, and remote
  launch.
- Agent progress tracks recent tool activity and token counts.
- Subagent final output uses a clean result, not raw full transcript.

Yuanbao current code:

- `runtime/src/local_agent_runtime/tools/task.py`
- `runtime/src/local_agent_runtime/services/subagent_service.py`
- `runtime/src/local_agent_runtime/services/worker_runner.py`
- `runtime/src/local_agent_runtime/orchestrator/child_task.py`
- `runtime/src/local_agent_runtime/orchestration/swarm.py`
- `runtime/src/local_agent_runtime/orchestration/supervisor.py`

Gap:

- Agent capability exists, but some visible flows still come from backend
  planner templates.
- Agent names/roles can be generic or ID-like.
- Worktree binding failures can dominate the visible panel.
- Child outputs can include handoff/debug text instead of a clean assistant
  synthesis.

Action:

- The `agent` tool should be the primary user-visible spawn source.
- Agent cards should use model-provided description/name/role, with internal
  task id hidden in metadata.
- Child task output should publish a clean final result plus compact progress,
  not the raw child transcript.

### 9. Team / Swarm

Reference code:

- `docs/cc-haha-main/src/tools/TeamCreateTool/TeamCreateTool.ts`
- `docs/cc-haha-main/src/tools/shared/spawnMultiAgent.ts`
- `docs/cc-haha-main/src/server/services/teamService.ts`

haha-cc shape:

- Team creation is a tool.
- Team config and members are persisted under a team namespace.
- Team members have readable names, roles/types, model, status, cwd, session id.
- `team_created`, `team_update`, and `team_deleted` are explicit output types.

Yuanbao current code:

- `runtime/src/local_agent_runtime/services/collaboration_service.py`
- `runtime/src/local_agent_runtime/services/worker_runner.py`
- `runtime/src/local_agent_runtime/yuanbao_event_adapter.py`
- `shared/src/events.ts`

Gap:

- Yuanbao emits `team_*` flat messages from collaboration events, but the
  source flow can still be backend-generated orchestration rather than a
  model/tool decision.
- Team panels can show duplicated "sent/created/completed" snapshots and raw
  child ids.

Action:

- Keep team lifecycle explicit and typed.
- Render roster/member cards from `team_update`.
- Only create swarm/team when user intent or model tool call asks for it.

### 10. Completion Review / Evidence Gate

Reference behavior:

- haha-cc has tool/task hooks and verification nudges, but internal recovery or
  evidence checks do not become ordinary user prompts.

Yuanbao current code:

- `runtime/src/local_agent_runtime/orchestrator/task_lifecycle.py`
- `runtime/src/local_agent_runtime/orchestrator/approval_flow.py`
- `runtime/src/local_agent_runtime/policy/decision_advisor.py`
- `app/src/state/viewComputations.ts`

Gap:

- Completion review can look like a visible approval task, expose JSON, and
  interfere with finalization.
- The evidence gate can demand workspace evidence for read-only or summary
  tasks where it should not.

Action:

- Completion review is internal by default.
- If an explicit user approval is needed, show compact semantic evidence rows.
- Workspace evidence is required only when router/advisor explicitly sets a
  task contract, not by default for all code/doc tasks.

### 11. Failure, Retry, Stop, Resume

Reference code:

- `docs/cc-haha-main/src/server/ws/events.ts`
- `docs/cc-haha-main/src/server/ws/handler.ts`
- `docs/cc-haha-main/src/cli/structuredIO.ts`

haha-cc shape:

- `api_retry`, `error`, `status`, and cancelled/missing tool results keep the
  user informed without multiplying final messages.
- Stop/cancel should not reveal a buffered internal tail as chat.

Yuanbao current code:

- `runtime/src/local_agent_runtime/provider/failure_recovery.py`
- `runtime/src/local_agent_runtime/orchestrator/provider_turn.py`
- `runtime/src/local_agent_runtime/orchestrator/task_lifecycle.py`
- `app/src/hooks/useEventSubscription.ts`

Gap:

- Provider rate/concurrency failures can produce multiple memory/goal/task
  failure rows.
- Stop/cancel/reload can surface events that happened after the user thought the
  task had stopped.

Action:

- Collapse transient provider failures into one retry/error chat status.
- Terminal task states are absorbing for chat: after cancel/failed/completed,
  later internal events remain trace-only unless they are explicit recovery
  initiated by the user.

## Yuanbao Current Strengths

Yuanbao already has useful infrastructure that haha-cc does not need in the
same form:

- Durable SQLite session/task/message/event/trace storage.
- Rich `RuntimeEvent` envelope for trace/panel/debug.
- Yuanbao/haha-compatible flat adapter.
- Command logs, patch records, worktree records, approval records.
- Tool result slimming and trace/artifact separation.
- Frontend runtime panels and derived views.

These should be preserved. The problem is not "too much backend"; the problem
is that internal state sometimes becomes primary chat output, and backend
fallback orchestration sometimes acts before the model has chosen a path.

## Gap Matrix

| Area | Gap Type | Severity | Desired Direction |
| --- | --- | --- | --- |
| Default planner | Flow is too fixed | P0 | Normal turns are ReAct/model-first; visible plans only for explicit plan/orchestration. |
| Workspace evidence | Gate too broad | P0 | Only explicit router/advisor contract requires it. |
| Session replay | Live/reload mismatch | P0 | One projection/visibility contract for live and replay. |
| Completion review | Internal gate leaks | P0 | Trace/panel audit only unless real user decision is required. |
| Thinking | Provider reasoning mixed with progress | P0 | Separate `thinking` from `assistant_progress/status`. |
| Tool panels | Raw JSON and duplicate groups | P0 | Tool-specific presentation and persisted chat-compatible frames. |
| Plan approval | Raw plan JSON | P0 | Structured subtask/section cards. |
| Agent/subagent | Generic names / internal ids | P1 | Model-provided readable names, IDs in metadata only. |
| Team/swarm | Source flow too backend-driven | P1 | Created by explicit user intent or model tool calls. |
| Failure/retry | Multiple final failure rows | P1 | Single retry/error/failure surface, trace contains details. |
| Computer use | Incomplete parity | P2 | Defer; keep schema hooks but do not block current alignment. |

## Flow Rules To Enforce

### Normal Chat

```text
user message
  -> route intent
  -> provider turn
  -> optional provider tool calls
  -> tool execution and tool_result
  -> provider continues or final text
  -> message_complete
```

Rules:

- No root inspect/apply/verify plan unless model/user explicitly asks.
- No default broad workspace evidence gate.
- No raw router/advisor/provider diagnostics in chat.

### Explicit Plan

```text
user/model enters plan mode
  -> read-only exploration
  -> plan proposal
  -> plan approval
  -> resume with approved plan or revise
```

Rules:

- Plan details render as sections/cards.
- User approval response resolves the same approval once.
- Rejected plan does not create a new user goal unless the user sends one.

### Tool Call

```text
content_start(tool_use)
  -> tool_use_complete
  -> tool_result
```

Rules:

- Exactly one visible result for each visible tool use.
- Raw input/output stays trace-only when a structured presentation exists.

### Agent / Team

```text
model calls agent/team/task tool
  -> agent/team/task created/update events
  -> progress snapshots
  -> clean result summary
```

Rules:

- Names are user-facing titles, not IDs.
- Subagent final output is clean synthesized content.
- Backend may enforce policy/worktree/safety, but should not invent generic
  subtask templates as the default path.

## Immediate Implementation Checklist

1. Route/planner:
   - Keep normal ReAct turns planless.
   - Only explicit planning/delegation signals can select swarm/supervisor.
   - Provider preflight split only for real runtime need such as context
     pressure or prior provider failure.

2. Evidence/completion:
   - `workspaceEvidenceRequired` only from explicit metadata/advisor contract.
   - Completion review is internal; no flat `permission_request`.
   - Terminal states block late review/resume/chat events.

3. Event projection/replay:
   - Make chat-compatible derived frames persisted or reconstructable in one
     place.
   - Do not replay raw trace events into chat if live flow hid them.
   - Ensure one `message_complete` per assistant turn.

4. Frontend rendering:
   - Replace raw plan/approval JSON with preview sections/cards.
   - Add a stable tool presentation registry for common categories.
   - Hide internal IDs from primary labels.

5. Thinking/progress:
   - Provider reasoning -> `thinking`.
   - Backend heartbeat -> `status` / `assistant_progress`.
   - Render both in phase order around tool calls.

## What We Should Cut

- Default root inspect/apply/verify/report plans for ordinary prompts.
- Default workspace evidence gate for all code/doc/read-only tasks.
- Raw completion-review JSON in chat/runtime cards.
- Backend-created generic multi-agent tasks when the user did not ask for
  multi-agent/decomposition and the model did not call an agent/task tool.
- Visible internal identifiers as primary names.

## What We Should Add

- A single event visibility/projection contract used by live stream and replay.
- Structured plan/subtask approval rendering.
- Stable tool/agent/team presentation metadata.
- Tests for:
  - live vs reload parity,
  - no default plan for simple/code prompts,
  - no default workspace evidence,
  - one-shot approval/question resolution,
  - provider thinking separate from backend progress,
  - terminal-state absorbing behavior.

