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

## 2026-06-05 Code Confirmation

This pass rechecked the actual haha-cc code before making another design
change. The key files are:

- `docs/cc-haha-main/src/server/services/conversationService.ts`
- `docs/cc-haha-main/src/remote/sdkMessageAdapter.ts`
- `docs/cc-haha-main/src/query.ts`
- `docs/cc-haha-main/src/services/tools/StreamingToolExecutor.ts`
- `docs/cc-haha-main/src/tools/TeamCreateTool/TeamCreateTool.ts`
- `docs/cc-haha-main/src/tools/AgentTool/AgentTool.tsx`
- `docs/cc-haha-main/src/tools/TaskUpdateTool/TaskUpdateTool.ts`

The verified shape is:

```text
desktop sendMessage
  -> build user SDK message
  -> CLI/SDK query loop streams assistant messages
  -> assistant tool_use blocks are executed by the tool executor
  -> tool_result user messages are yielded back to the model
  -> loop continues until result/end turn
```

Important code-level conclusions:

- `ConversationService.sendMessage()` does not run a business intent router,
  planner, advisor, workspace-evidence gate, or completion-review gate before
  handing the user message to the CLI/SDK session.
- `ConversationService` tracks `control_request(can_use_tool)` as pending
  permission requests and sends `control_response` when the user decides. This
  is policy/control plumbing, not task routing.
- `query.ts` owns the ReAct loop. It yields provider assistant blocks, records
  tool_use blocks, streams tool execution results, and continues with
  tool_result messages.
- `StreamingToolExecutor` owns tool concurrency, progress, synthetic
  tool_result errors on abort/fallback, and ordered result emission.
- Thinking belongs to the provider assistant trajectory. haha-cc preserves
  thinking across a trajectory that may include tool_use, the corresponding
  tool_result, and the following assistant message. Backend progress is not
  treated as provider thinking.
- Team, agent, task, plan, and ask-user-question behavior is exposed as tools
  and tool prompts. The backend provides typed tools, permissions, task state,
  and rendering data; it does not pre-generate generic swarm subtasks for every
  multi-agent-looking prompt.
- The SDK message adapter displays known message kinds and ignores success
  result noise, SDK-only summaries, auth/rate-limit events, and unknown message
  types instead of leaking raw JSON into chat.

Yuanbao alignment decision from this evidence:

- The default product path should be model-first ReAct.
- `MetaRouter` may keep cheap intent hints for observability, but it must not
  be a default business router that selects fixed inspect/apply/verify/report
  plans.
- `DecisionAdvisor` and completion review are not default turn routers. They
  belong behind explicit policy/gate boundaries such as write verification,
  permissions, safety, compaction, or explicitly enabled experimental modes.
- `workspaceEvidenceRequired` must be an explicit contract, not an implicit
  default for code/doc/workspace prompts.
- Plan/swarm/team/agent panels should be projected from model tool calls or
  explicit mode state, not from raw planner JSON.
- Parity fixes must be source-driven, not filter-driven. Do not treat raw JSON,
  duplicate progress, misplaced thinking, or fixed workflows as frontend-only
  rendering bugs. First remove or opt-in the backend source that creates the
  wrong flow; then map only core trajectory outputs to the flat protocol:
  assistant text -> `content_start/content_delta/message_complete`, provider
  reasoning -> `thinking`, tool calls -> `tool_use_complete/tool_result`,
  permissions -> `permission_request`, and task/team/agent state -> structured
  panel events. Synthetic backend stage logs must not masquerade as chat or
  provider thinking.

## Code-Level Initial Turn Diff

### haha-cc Initial Turn

Reference code:

- `docs/cc-haha-main/src/QueryEngine.ts`
- `docs/cc-haha-main/src/query.ts`
- `docs/cc-haha-main/src/services/tools/toolOrchestration.ts`
- `docs/cc-haha-main/src/tools/EnterPlanModeTool/EnterPlanModeTool.ts`
- `docs/cc-haha-main/src/tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`

Observed flow:

```text
submitMessage()
  -> processUserInput()
  -> append user input to transcript / mutableMessages
  -> yield system init
  -> queryLoop()
      -> stream provider assistant content
      -> if provider emits tool_use blocks, runTools() executes those tools
      -> append tool_result messages
      -> repeat provider loop
      -> finish with result/message_complete
```

The important part is not the exact names. The important part is ownership:

- The model chooses whether to answer, read files, run commands, enter plan
  mode, create tasks, create teams, or spawn agents.
- Backend services execute tools, enforce permissions, compact context,
  persist transcript/state, and project typed events.
- Plan approval is a state transition from active plan mode. `ExitPlanMode`
  validates that the session is already in plan mode before producing the plan
  approval interaction.
- Completion is the model/tool loop finishing. There is no default visible
  completion-review task for ordinary chat or read-only answers.

### Yuanbao Before This Pass

Reference code:

- `runtime/src/local_agent_runtime/orchestrator/message_execution.py`
- `runtime/src/local_agent_runtime/orchestrator/react_runner.py`
- `runtime/src/local_agent_runtime/orchestrator/task_lifecycle.py`
- `runtime/src/local_agent_runtime/tools/plan_mode.py`
- `runtime/src/local_agent_runtime/router/defaults.py`

Observed flow:

```text
message.send
  -> router/meta-router picks scenario and strategy
  -> enable_planning may enter planner/supervisor/swarm before normal ReAct
  -> ReAct provider loop runs
  -> completion advisor/product advisor may run
  -> completion gate may request internal follow-up/review
```

This created the user-visible symptoms from the screenshots:

- A short greeting could still get a long backend context and internal
  completion follow-up.
- A model that called `exit_plan_mode` outside real plan mode could create a
  plan approval with raw JSON.
- Backend planner/swarm scaffolds could create fixed subtasks before the model
  had actually chosen an agent/task/team tool path.
- Thinking/progress felt out of order because backend phases were competing
  with provider thinking and tool lifecycle events.

### Required Direction

Yuanbao should keep its own envelope and runtime services, but the default
orchestration principle should match haha-cc:

- Default turn: model-first ReAct. Do not insert inspect/apply/verify/report
  scaffolds for normal chat, read-only, document, or code questions.
- Explicit contracts only: `workspaceEvidenceRequired` is honored when router
  or advisor explicitly attaches it, not as a broad default for all workspace
  prompts.
- Plan mode is stateful: `exit_plan_mode` can request approval only after
  `enter_plan_mode` or an explicit plan-mode session state.
- Completion advisor is a write/verification boundary. It should not take over
  pure chat/read-only answers.
- Task/team/agent cards should be generated from model tool calls or explicit
  user multi-agent requests, then rendered as typed panels rather than raw
  JSON.

### Current Code Changes From This Pass

- `runtime/src/local_agent_runtime/orchestrator/task_lifecycle.py`
  now checks explicit workspace evidence first, then completes read-only/chat
  tasks before consulting completion advisor.
- `runtime/src/local_agent_runtime/orchestrator/task_lifecycle.py`
  skips completion advisor for non-write/non-verification tasks.
- `runtime/src/local_agent_runtime/orchestrator/react_runner.py`
  blocks `exit_plan_mode` outside active plan mode and returns a model-visible
  tool error instead of creating a plan approval.
- `runtime/src/local_agent_runtime/orchestrator/react_runner.py`
  projects pre-execution blocked tools through the same `tool.started` /
  `tool.blocked` path as executed tools, so chat/replay still receive
  `content_start`, `tool_use_complete`, and `tool_result`.
- `runtime/src/local_agent_runtime/router/meta_router.py`
  routes explicit multi-agent requests to `plan_swarm` with
  `enable_planning=false` and `orchestrationMode=model_tools`, so the model
  receives agent/task capability without the backend pre-generating fixed
  swarm subtasks.
- `runtime/src/local_agent_runtime/orchestrator/message_routing.py`
  carries `orchestrationMode=model_tools` into the runtime routing dict so
  downstream planners and policy resolvers can distinguish model-tool
  orchestration from legacy fixed planner execution.
- `runtime/src/local_agent_runtime/planner/service.py`
  skips visible lifecycle plans for `model_tools` orchestration and narrows
  "explicit plan" detection so ordinary questions like `explain a plan` do not
  create generic `Confirm goal / Draft plan / Present plan` UI scaffolding.
- `runtime/src/local_agent_runtime/main.py` now builds the default server with
  no `DecisionAdvisor` and no provider-backed `MetaRouter` LLM pre-route.
  This matches the haha-cc shape: advisor remains an optional capability, not
  a default backend classifier that rewrites every turn before the model acts.
- `runtime/src/local_agent_runtime/orchestrator/service.py` uses a rule-only
  `MetaRouter` as its fallback when no router is injected. Tests can still
  explicitly construct advisor/provider-backed routing, but the product path no
  longer does so by accident.
- `runtime/src/local_agent_runtime/router/meta_router.py` no longer consults a
  high-confidence routing advisor by default. If this experimental path is
  needed, it must be explicitly enabled with
  `advisor.routingStrategyUseForHighConfidence=true`.
- `runtime/src/local_agent_runtime/orchestrator/message_routing.py` no longer
  auto-binds `doc_write` to a worktree. Real writes still go through the normal
  write tool and approval gates; read-only docs stay read-only.
- `runtime/src/local_agent_runtime/orchestrator/publishing.py`
  only derives flat `plan_update` from `task.updated` when the update carries
  an actual non-empty plan. Plain task status/current-step updates continue as
  task updates instead of pretending to be plan panels.
- `runtime/tests/test_orchestrator_react_loop.py` locks the
  `exit_plan_mode` outside plan-mode regression, blocked tool visibility, and
  explicit multi-agent model-tool entrypoint.
- `runtime/tests/test_runtime_flows.py` updates the old swarm approval contract:
  a plain `swarm` keyword no longer means fixed plan approval before the model
  acts.
- `runtime/tests/test_planner_service.py` locks both sides of the planner
  boundary: explicit fixed-planner routes can still show lifecycle plans, but
  model-tool swarm routes and explanatory plan questions do not.
- `runtime/tests/test_backend_flow_contracts.py` now locks the default server
  contract: the main product runtime has no backend advisor or provider-backed
  pre-route unless a caller wires one explicitly.

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
- Runtime trace events must not become visible on reload when they were hidden
  live. Reload should use persisted chat-compatible frames, not raw lifecycle
  events, for chat reconstruction.

Action:

- Keep `RuntimeEvent` / `AgentEventEnvelope` as source of truth for Yuanbao.
- Treat flat `YuanbaoServerMessage` as the chat protocol projection.
- Make live stream, `events.yuanbaoAfter`, trace replay, and message DB replay
  share one visibility contract.
- Persist recoverable flat frames (`thinking`, `content_start`,
  `tool_use_complete`, `tool_result`, tool `content_delta`, and
  `message_complete`) with explicit trace mirrors; suppress raw
  `message.completed` flat projection to avoid duplicate finalization.

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

2026-06-06 update:

- Yuanbao now treats `message.delta` as the canonical assistant text stream.
  It projects to the haha-cc/Yuanbao flat `content_delta` frame.
- Raw `assistant.token` is kept only as legacy/internal trace data. It still
  triggers `content_start(text)` and `message.delta`, but its own event
  visibility is `trace`, so session reload cannot show a second text stream
  that realtime chat did not show.
- `EventBus.publish()` backfills the runtime envelope with the persisted trace
  `id/sequence`, making realtime `eventId/seq` match `events.after` replay.
- Frontend live subscription and trace replay both consume `_chatCompat`
  `message.delta`; `_chatCompat` `assistant.token` remains ignored by chat.
- `agent.decision.completion` and provider/router diagnostics remain
  `visibility=trace` and are not projected as flat ServerMessage frames.

Real Responses streaming probe (`gpt-5.4-mini`) confirmed the observable
contract:

```text
simple question
  -> provider turn, no tools
  -> status/content_start/message.delta*/message_complete/status

read-only summary or plan
  -> model-selected read/search tools when needed
  -> no forced git/verify/write path
  -> final text stream

write request
  -> model-selected read tools
  -> write tool reaches permission_request
  -> approval resumes same task; duplicate approval submit is ignored
```

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

- Yuanbao previously mixed real provider reasoning, synthetic progress, and
  backend phase logs under similar UI surfaces.
- Some providers cannot emit true token-level thinking, so backend-generated
  progress must not pretend to be provider thinking.

Action:

- `thinking`: only provider reasoning delta/summary or explicitly marked model
  thought summary.
- Production backend must not emit synthetic `assistant_progress` chat frames.
- Backend budget/planning state uses typed panel events such as
  `task.budget.progress` and `task.planning.progress`.
- Frontend keeps legacy `assistant_progress` out of live/replay chat so old
  traces do not rehydrate as new transcript noise.

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

- Basic flat tool frames exist, but many rendered panels still need better
  per-tool presentation labels/previews.
- New live and replay grouping should use derived chat-compatible events as the
  main path; raw lifecycle remains for trace/panel diagnostics and legacy
  compatibility only.
- Tool names and targets are sometimes formatted by heuristics rather than a
  stable tool presentation registry.

Action:

- Keep raw tool lifecycle in trace.
- Persist/replay only the chat-compatible tool frames that are intended for
  transcript display.
- Restore frontend tool rows from `content_start(tool_use)` ->
  `tool_use_complete` -> `tool_result`, matching the haha-cc tool trajectory.
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
- New task completion no longer creates `completion_review` approvals. The
  completion state machine now separates clear failures, internal audit gaps,
  and same-task continuation for advisor/workspace evidence. Legacy
  `completion_review` approvals are still accepted for old stored sessions.
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
| Thinking | Provider reasoning mixed with backend progress | P0 | `thinking` is provider trajectory only; backend progress is typed panel/status, not chat text. |
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
   - Completion review is internal; no flat `permission_request`, no new
     user-visible approval, and no new user goal after approval/resume.
   - Failed verification, failed acceptance, and unresolved failed tool
     evidence terminate as task failures with structured completion-gate
     metadata. Missing but non-failing evidence is recorded as an internal
     audit, or continues the same task when advisor/workspace evidence is
     explicitly required.
   - Terminal states block late review/resume/chat events.

3. Event projection/replay:
   - Chat-compatible derived frames are the replay source for tool/thinking/
     completion surfaces.
   - Do not replay raw trace events into chat if live flow hid them.
   - Ensure one `message_complete` per assistant turn.

4. Frontend rendering:
   - Replace raw plan/approval JSON with preview sections/cards.
   - Add a stable tool presentation registry for common categories.
   - Hide internal IDs from primary labels.

5. Thinking/progress:
   - Provider reasoning -> `thinking`.
   - Backend budget/planning state -> typed panel events (`task.budget.progress`,
     `task.planning.progress`) or `status`.
   - Do not synthesize chat/thinking/progress text from backend stage logs.
   - Render tool order from `content_start` / `tool_use_complete` /
     `tool_result`, not from guessed text deltas.

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

## 2026-06-05 Routing Boundary Decision

The default Yuanbao turn flow must not use `MetaRouter` as a business-intent
orchestration router. This was the upstream cause of several repeated
symptoms:

- ordinary prompts with words like `README`, `document`, `debug`, `test`,
  `optimize`, or Chinese equivalents were classified into fixed scenarios or
  skills before the model had acted;
- those scenarios then affected worktree binding, workspace evidence,
  planner/swarm mode, skill filtering, validation, and completion review;
- the UI therefore showed repeated Git/read/verify activity, generic plan or
  agent panels, and raw approval/review JSON even when the model had not asked
  for those flows.

The haha-cc reference shape is different:

```text
process user input / slash commands / attachments
  -> build system init and tool context
  -> provider streams assistant blocks
  -> provider emits tool_use blocks when it chooses tools
  -> backend executes tools, enforces permissions, persists transcript
  -> backend yields typed ServerMessage frames
  -> provider continues or finalizes
```

There is no default keyword classifier that rewrites a normal prompt into
`doc_writer`, `debugger`, `test_writer`, fixed planner, or fixed swarm
execution. Keyword handling exists for special input surfaces such as slash
commands, ultraplan triggers, tool search, and UI highlighting, but it does not
own the main task flow.

Yuanbao decision:

- Default `message.send` should be model-first ReAct.
- The routing layer may only provide a thin turn policy:
  - greeting/empty fast path;
  - explicit user/session modes such as queued, background, supplement,
    approval resume, or plan mode;
  - explicit multi-agent/tool availability hints, without pre-generating
    subtasks;
  - permission, budget, and context-pressure boundaries.
- Keyword-derived classifications may be kept only as
  `metadata.intentHints`/trace diagnostics. They must not set default
  `skill_id`, `enable_planning`, `workspaceEvidenceRequired`, worktree binding,
  completion review, or verification requirements.
- Specialized skills remain available only through explicit user selection,
  explicit command/profile/session configuration, or model-visible tool/skill
  selection. A phrase like "generate a document" must not automatically filter
  the tool set to `doc_writer`.
- Explicit fixed planners can still exist for tests, developer tools, or
  deliberate product modes, but they are opt-in execution modes rather than
  default natural-language routing.

Implementation target:

- keep `MetaRouter` for compatibility, but make its default output
  `free_form/react_standard` for normal prompts;
- preserve old keyword result as `metadata.intentHints.ruleCandidate`;
- preserve `simple_query/react_fast` only for greeting-only or clearly
  informational prompts where it does not remove needed tools;
- preserve explicit multi-agent as `swarm_task/plan_swarm` with
  `enable_planning=false` and `orchestrationMode=model_tools`;
- update tests so `debug this`, `write a test`, `generate a document`,
  `update README`, `implement a game`, and `optimize this project` no longer
  force backend skills/plans/worktrees before the provider turn.

## 2026-06-05 Worktree, Approval, And Failure Boundary

This pass also tightened the runtime boundaries that previously made the
backend feel rigid or caused live/replay divergence.

haha-cc reference principle:

- Permissions are surfaced when a tool/control boundary actually needs them.
  For example, `can_use_tool`/plan approval pauses the current loop and resumes
  the same task after a `control_response`.
- Tool failures and permission denials remain part of the assistant/tool
  trajectory. They are not turned into a new user prompt or a separate final
  answer.
- Repository/worktree isolation is an execution policy, not a keyword-routing
  default. The model can decide to edit; the backend then applies the relevant
  write/permission policy at the tool boundary.

Yuanbao decisions:

- `worktreeBindingRequired` is now explicit. Session launch metadata such as
  `repository.worktree=true`, policy configuration, or an already explicit task
  route can require worktree binding. Ordinary natural-language phrases like
  "fix code", "update README", or "generate a document" must not bind a
  worktree before the model/tool loop acts.
- When a write tool runs and explicit worktree binding is required, the binding
  check happens at the write-tool boundary. If binding fails, the result is a
  structured task/tool failure with `WORKTREE_BINDING_FAILED`, not a hidden
  retry loop.
- If explicit worktree binding fails during task setup, the failure is returned
  as a normal task result with `task.failed` and structured metadata. The JSON-
  RPC request should not fail in a way that leaves the session without
  replayable messages/events.
- Plan, command, and Computer Use approvals intentionally leave the task in
  `waiting_approval` on the first response. After approval/rejection, the same
  task resumes and completes or fails. Tests should not expect the first
  approval response to already be `completed`.
- Completion review is not a default visible approval. Read-only answers,
  ordinary tool failures that the model can recover from, and simple chat
  completions should finish through the normal ReAct path. Completion gates
  remain only for explicit write/verification/safety boundaries.

## 2026-06-06 Provider Failure And Replay Boundary

This pass rechecked the actual haha-cc code before changing the failure path:

- `docs/cc-haha-main/src/remote/sdkMessageAdapter.ts`
- `docs/cc-haha-main/src/query.ts`
- `docs/cc-haha-main/src/services/tools/StreamingToolExecutor.ts`

Confirmed reference behavior:

- SDK `result` success is ignored as display noise; only error results become
  user-visible system messages.
- `auth_status` and SDK-only tool summaries are ignored by the display adapter.
- Provider/runtime query errors surface as terminal errors. The loop may create
  synthetic `tool_result` blocks only to keep an already-visible assistant
  `tool_use` protocol-complete; it does not turn provider failures into visible
  memory, open-issue, task-learning, or recovery cards.
- Streaming fallback discards orphan tool results and emits synthetic error
  `tool_result` frames for affected tool uses, preserving one tool result per
  visible tool use.

Yuanbao corrections:

- `runtime/src/local_agent_runtime/orchestrator/memory_flow.py` now skips
  session/workspace memory writes for provider/runtime failures identified by
  `structuredResult.failureRecovery` categories such as auth, rate_limit,
  timeout, network, server_error, request_validation, invalid_response, or by
  loop-level error codes carrying provider failure recovery metadata.
- Normal task failures can still become useful recovery memory. The skip is
  only for infrastructure/provider failures, matching haha-cc's terminal
  error/status treatment.
- `runtime/src/local_agent_runtime/policy/tool_policy_resolver.py` now treats
  explicit user read-only constraints as a provider tool-visibility boundary:
  read-only analysis can still use read/context tools, explicit plan mode can
  still expose plan-mode tools, and explicit read-only multi-agent prompts can
  expose `agent`/`task`, but write-capable tools are withheld. This mirrors the
  haha-cc split between model-first ReAct and permission/tool context.
- `runtime/src/local_agent_runtime/rpc/server.py` pages through trace events in
  `events.yuanbaoAfter` until it finds chat-compatible flat messages or reaches
  a bounded page limit. This fixes long sessions where live output included a
  final answer after many trace events but replay stopped before that final
  flat text.
- Assistant text deltas are not trace-mirrored for replay; durable assistant
  messages own final text replay. Trace replay keeps recoverable protocol/tool
  frames such as thinking, tool starts/results, tool output deltas, plan
  updates, and `message_complete`. This prevents reload or stop/reopen from
  duplicating the final answer after the message DB already restored it.
- `runtime/src/local_agent_runtime/yuanbao_event_adapter.py` no longer projects
  `goal_event` or `memory_event` into flat `system_notification` messages.
  Those events remain available as envelope/panel state, matching haha-cc's
  adapter habit of ignoring SDK-only status/memory noise instead of rendering
  it as assistant chat.
- Backend contracts now cover 429/concurrency and 401/auth provider failures
  with a real `MemoryManager`, and long replay with more than one trace page
  before the final flat message.
