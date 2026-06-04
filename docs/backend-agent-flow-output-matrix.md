# Backend Agent Flow And Output Matrix

This document is the working contract for Yuanbao backend flow, frontend presentation, and haha-cc-compatible output. The key rule is simple: user-facing chat should be driven by haha-cc-style `ServerMessage` frames, while Yuanbao raw events remain structured panel or trace data.

## Output Layers

| Layer | Purpose | Event examples | UI target | Failure risk if wrong |
| --- | --- | --- | --- | --- |
| Chat protocol | User-visible transcript and streaming blocks | `content_start`, `content_delta`, `tool_use_complete`, `tool_result`, `permission_request`, `thinking`, `status`, `message_complete`, `error` | Main chat | Duplicate text, raw JSON, missing tool blocks, duplicate finalization |
| Message lifecycle | Durable user/assistant bubbles | `message.created`, `message.completed`, `message.failed` | Main chat message store | Lost messages, failed bubble not updated, wrong session replay |
| Task/panel state | Root task, approvals, plan, subtask state | `task.started`, `task.completed`, `task.failed`, `task.planning.*`, `approval.*`, `goal_event`, `memory_event` | Runtime panel / clean activity | Internal JSON appears in chat, approval cards look like raw data |
| Tool/command diagnostics | Raw tool arguments/results and command lifecycle | `tool.started`, `tool.completed`, `command.output`, `command.completed` | Trace plus folded runtime worklog | Tool output duplicates when both raw and compat render |
| Decision diagnostics | Advisor/router/provider/completion details | `agent.decision.*`, `task.routing.*`, `provider.*`, `runtime.error` | Trace only unless explicitly promoted | Completion review, recovery, and provider internals leak into chat |
| Collaboration | Team/child task lifecycle | `collab.*`, `task.planning.subtask.*` | Agent panel, plus flat `team_*` / `task_update` when needed | Child ids and handoff text appear as confusing chat messages |

## Mode Matrix

| Input or state | Backend route | Expected chat output | Expected panel/trace output | Common failure points |
| --- | --- | --- | --- | --- |
| Simple greeting or answer | `send_message` -> routing -> `react_fast` or standard ReAct | `message.created`, optional `thinking/status`, streamed `content_delta`, `message_complete` | `task.started/completed` panel; routing/provider trace | Over-planning tiny questions; provider transient failure creating many memory/goal errors |
| Read-only analysis | Standard ReAct with read tools | Progress `thinking/status`; tool blocks for search/read; final text | Raw read/search tool events in trace; task state in panel | Tools not shown in realtime if compat bridge is absent; history replay differs if raw is hidden but compat is not persisted |
| Write/edit task | Standard ReAct with write and verification tools | Tool blocks, permission cards if policy requires, final text or failure bubble | Task changed files, command logs, completion evidence, approvals | Completion review asks for semantic evidence after work is enough; final failure duplicated by `message.failed` and `task.failed` |
| Plan mode entered by model | ReAct tool `enter_plan_mode` | Planning/thinking progress; no raw plan JSON in main text | Plan state and plan approval in panel | Plan approval body rendered as JSON instead of structured subtask cards |
| Exit plan mode approval | `exit_plan_mode` pauses task and stores pending ReAct state | `permission_request`; `status(permission_pending)` | `approval.requested`, `task.waiting_approval` panel | User answer treated as a new goal; approval can be clicked more than once; resumed answer shown as user-sent message |
| Strict plan approval | `_execute_with_planning` creates `plan` approval before subtasks | Permission card, then resumed progress | `task.planning.started/decomposed`, approval panel | After approval, no heartbeat while backend is resuming; plan details displayed as raw JSON |
| Swarm / supervisor multi-agent | Routing strategy `plan_swarm` or `plan_supervise` | Compact planning/thinking, then final synthesis | `task.planning.*`, `collab.*`, child results, team/task updates | Fixed generic subtasks, child names like ids, missing live child task cards, late history shows many child messages |
| Background or queued task | `send_message` mode queued/background | User message and high-level status | `task.queued`, background job/command logs | Queued task has no assistant placeholder; user sees silence after submit |
| ask_user_question | Tool pauses ReAct with required missing info | One question card; answer resolves the same card | `ask_user_question`, `task.supplement.consumed` | Low-risk style preference asks user unnecessarily; selected answer can be resubmitted and treated as new task |
| Supplement to active task | `send_message` internal or active-task supplement path | No extra visible user goal unless truly user-authored | `task.supplement.consumed`; pending ReAct state resumes | Supplement becomes a new chat request; completion review runs on the answer text |
| Provider transient failure | Provider/loop exception -> classifier -> `_fail_task` | One friendly failed assistant message or retry status | Provider failure details in trace; no task/memory/goal spam for transient failures | Concurrency/rate-limit writes repeated memory/goal/task failed rows |
| Tool failure | Tool pipeline emits failed result and model can recover | Tool result block with `isError=true`; optional recovery text | Raw tool failure and recovery decision trace | Failure result hidden so model seems idle; recovery decision leaks as chat JSON |
| Completion review | `_complete_task` evidence gate asks for review/approval | If user approval is needed, show structured approval card | Completion evidence/audit in panel/trace | Review requested for read-only summary; review details are raw JSON; rejected review creates duplicate final failure |
| Stop/cancel | Task control cancels active work | Clear thinking/status; optional cancelled notice | `task.cancelled` panel; command cancel trace | Stopping reveals buffered internal messages; pending commands keep running silently |
| Session reload/reconnect | `message.list` + `events.after` + trace replay | Durable messages first, then only persisted chat-compatible runtime blocks | Trace and runtime panels rebuilt from trace/events | Reopened session shows more output than live run because raw events defaulted to chat |

## Current Event Visibility Contract

Root events that may drive chat compatibility:

- Text and message lifecycle: `assistant.token`, `message.delta`, `message.created`, `message.completed`, `message.failed`.
- Tool/command/approval bridge sources: `tool.*`, `command.*`, `approval.requested`, `approval.resolved`.
- Task lifecycle bridge sources: `task.started`, `task.updated`, `task.completed`, `task.failed`, `task.cancelled`.
- Direct chat protocol frames: `content_*`, `tool_*`, `permission_request`, `thinking`, `status`, `message_complete`, `plan_update`.

Root raw events that should not be direct chat:

- `agent.decision.*`, `task.routing.*`, `provider.*`, `runtime.error`, `mcp.*` -> trace.
- `task.created`, `task.started`, `task.updated`, `task.completed`, `task.failed`, `task.cancelled`, `approval.*`, `goal_event`, `memory_event`, `task.planning.*` -> panel.
- `tool.*` and `command.*` raw lifecycle -> trace; their derived `content_start/tool_use_complete/tool_result/content_delta` frames are chat.

Failure finalization rule:

- `message.failed` maps to haha-cc `error` and updates the assistant bubble.
- `task.failed` maps to haha-cc `task_update(status=failed)` and updates task state.
- The same failure must not be emitted as two final `error` messages.

## Open Risks To Close Next

1. Persisted chat-compatible tool/progress frames are still thinner than live realtime because many bridge events intentionally skip trace mirroring. History replay currently reconstructs some blocks from raw trace, but raw visibility changes mean we need explicit persistence rules for the bridge frames that must survive reload.
2. Planning and swarm subtask generation can still look template-like. The decomposer should derive roles/titles from repository evidence and the user request, and fallback templates should be labeled as fallback.
3. Thinking placement still needs a turn-phase contract: thinking before provider/tool choice, tool block during execution, thinking/progress after tool result, final text after the last tool block.
4. Completion review is still too visible and JSON-heavy. Approval details should use structured preview rows and hide advisor diagnostics in trace by default.
5. User/internal responses need a stronger ownership model so approval answers and `ask_user_question` answers cannot be submitted twice or become new goals.
