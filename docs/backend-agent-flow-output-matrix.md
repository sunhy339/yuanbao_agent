# Backend Agent Flow And Output Matrix

This document is the working contract for Yuanbao backend flow, frontend presentation, and Yuanbao flat chat output. The key rule is simple: user-facing chat should be driven by Yuanbao flat `ServerMessage` frames, while raw runtime events remain structured panel or trace data. Legacy haha-cc names are compatibility aliases only, not product-facing output names.

## Output Layers

| Layer | Purpose | Event examples | UI target | Failure risk if wrong |
| --- | --- | --- | --- | --- |
| Chat protocol | User-visible transcript and streaming blocks | `content_start`, `content_delta`, `tool_use_complete`, `tool_result`, `permission_request`, provider `thinking`, `message_complete`, `error` | Main chat | Duplicate text, raw JSON, missing tool blocks, duplicate finalization |
| Message lifecycle | Durable user/assistant bubbles | `message.created`, `message.completed`, `message.failed` | Main chat message store | Lost messages, failed bubble not updated, wrong session replay |
| Task/panel state | Root task, approvals, plan, subtask state | `task.started`, `task.completed`, `task.failed`, `task.planning.*`, `approval.*`, `goal_event`, `memory_event` | Runtime panel / clean activity | Internal JSON appears in chat, approval cards look like raw data |
| Tool/command diagnostics | Raw tool arguments/results and command lifecycle | `tool.started`, `tool.completed`, `command.output`, `command.completed` | Trace plus folded runtime worklog | Tool output duplicates when both raw and compat render |
| Decision diagnostics | Advisor/router/provider/completion details | `agent.decision.*`, `task.routing.*`, `provider.*`, `runtime.error` | Trace only unless explicitly promoted | Completion review, recovery, and provider internals leak into chat |
| Collaboration | Team/child task lifecycle | `collab.*`, `task.planning.subtask.*` | Agent panel, plus flat `team_*` / `task_update` when needed | Child ids and handoff text appear as confusing chat messages |

## Mode Matrix

| Input or state | Backend route | Expected chat output | Expected panel/trace output | Common failure points |
| --- | --- | --- | --- | --- |
| Simple greeting or answer | `send_message` -> standard model-first ReAct | `message.created`, optional provider `thinking`, streamed `content_delta`, `message_complete` | `status`, `task.started/completed`, routing/provider trace | Over-planning tiny questions; provider transient failure creating many memory/goal errors |
| Read-only analysis | Standard ReAct with read tools selected by the model | Provider `thinking` if available; tool blocks for search/read; final text | Raw read/search tool events in trace; task state in panel | Tools not shown in realtime if compat bridge is absent; history replay differs if raw is hidden but compat is not persisted |
| Write/edit task | Standard ReAct with write and verification tools | Tool blocks, permission cards if policy requires, final text or failure bubble | Task changed files, command logs, completion evidence, approvals | Completion review asks for semantic evidence after work is enough; final failure duplicated by `message.failed` and `task.failed` |
| Plan mode entered by model | ReAct tool `enter_plan_mode` | Provider thinking/text only; no raw plan JSON in main text | Plan state and plan approval in structured panel | Plan approval body rendered as JSON instead of structured subtask cards |
| Exit plan mode approval | `exit_plan_mode` pauses task and stores pending ReAct state | `permission_request`; `status(permission_pending)` | `approval.requested`, `task.waiting_approval` panel | User answer treated as a new goal; approval can be clicked more than once; resumed answer shown as user-sent message |
| Strict plan approval | `_execute_with_planning` creates `plan` approval before subtasks | Permission card, then resumed progress | `task.planning.started/decomposed`, approval panel | After approval, no heartbeat while backend is resuming; plan details displayed as raw JSON |
| Swarm / supervisor multi-agent | Routing strategy `plan_swarm` or `plan_supervise` | Compact planning/thinking, then final synthesis | `task.planning.*`, `collab.*`, child results, team/task updates | Fixed generic subtasks, child names like ids, missing live child task cards, late history shows many child messages |
| Background or queued task | `send_message` mode queued/background | User message and high-level status | `task.queued`, background job/command logs | Queued task has no assistant placeholder; user sees silence after submit |
| ask_user_question | Tool pauses ReAct with required missing info | One question card; answer resolves the same card | `ask_user_question`, `task.supplement.consumed` | Low-risk style preference asks user unnecessarily; selected answer can be resubmitted and treated as new task |
| Supplement to active task | `send_message` internal or active-task supplement path | No extra visible user goal unless truly user-authored | `task.supplement.consumed`; pending ReAct state resumes | Supplement becomes a new chat request; completion review runs on the answer text |
| Provider/runtime failure | Provider/loop exception -> classifier -> `_fail_task` | One failed assistant message or retry/error status | Provider failure details in trace; no memory/goal/recovery-card spam for auth, rate-limit, timeout, network, server, invalid-response, or request-validation failures | Infrastructure failures are mistaken for task learnings/open issues and replay as many panels |
| Tool failure | Tool pipeline emits failed result and model can recover | Tool result block with `isError=true`; optional recovery text | Raw tool failure and recovery decision trace | Failure result hidden so model seems idle; recovery decision leaks as chat JSON |
| Completion review | `_complete_task` evidence gate asks for review/approval | If user approval is needed, show structured approval card | Completion evidence/audit in panel/trace | Review requested for read-only summary; review details are raw JSON; rejected review creates duplicate final failure |
| Stop/cancel | Task control cancels active work | Clear thinking/status; optional cancelled notice | `task.cancelled` panel; command cancel trace | Stopping reveals buffered internal messages; pending commands keep running silently |
| Session reload/reconnect | `message.list` + `events.after` + trace replay | Durable messages first, then persisted chat-compatible runtime blocks only | Trace and runtime panels rebuilt from trace/events | Reopened session shows more output than live run because raw events defaulted to chat |

## Current Event Visibility Contract

Root events that may drive chat compatibility:

- Text and message lifecycle: `assistant.token`, `message.delta`, `message.created`, `message.completed`, `message.failed`.
- Tool/command/approval bridge sources: `tool.*`, `command.*`, `approval.requested`, `approval.resolved`.
- Task lifecycle bridge sources: `task.started`, `task.updated`, `task.completed`, `task.failed`, `task.cancelled`.
- Direct chat protocol frames: `content_*`, `tool_*`, `permission_request`, provider `thinking`, `message_complete`.

Root raw events that should not be direct chat:

- `agent.decision.*`, `task.routing.*`, `provider.*`, `runtime.error`, `mcp.*` -> trace.
- `status`, `assistant_progress`, `task.created`, `task.started`, `task.updated`, `task.completed`, `task.failed`, `task.cancelled`, `approval.*`, `goal_event`, `memory_event`, `task.planning.*` -> trace/panel, never flat chat.
- `tool.*` and `command.*` raw lifecycle -> trace; their derived `content_start/tool_use_complete/tool_result/content_delta` frames are chat.
- `goal_event` and `memory_event` are panel/state events only. They must not be projected as flat `system_notification` chat messages, because they are internal task bookkeeping rather than assistant output.
- `plan_update` is a structured panel event. It may be replayed by the clean UI as a plan/subtask panel, but it must not become flat `system_notification`.

Failure finalization rule:

- `message.failed` maps to flat `error` and updates the assistant bubble.
- `task.failed` maps to flat `task_update(status=failed)` and updates task state.
- The same failure must not be emitted as two final `error` messages.

## Open Risks To Close Next

1. Planning and swarm subtask generation can still look template-like. The decomposer should derive roles/titles from repository evidence and the user request, and fallback templates should be labeled as fallback.
2. Thinking placement still needs more real-provider coverage: provider thinking before tool choice, tool block during execution, thinking/progress after tool result, final text after the last tool block.
3. Completion evidence panel rendering is still too JSON-heavy. Approval details should use structured preview rows and hide advisor diagnostics in trace by default.
4. User/internal responses need a stronger ownership model so approval answers and `ask_user_question` answers cannot be submitted twice or become new goals.

## 2026-06-04 State-Machine Alignment Notes

The current backend must be treated as four coupled state machines, not as one
generic event log:

| Machine | Input | Internal states | User-visible output | Hard rule |
| --- | --- | --- | --- | --- |
| Turn loop | user message, supplement, resume | route -> provider turn -> optional tools -> synthesize/finalize | `thinking/status`, `content_start/delta`, `tool_*`, `message_complete` | Do not expose router/advisor/provider diagnostics as chat blocks. |
| Tool loop | provider `tool_calls` | `tool.started` -> progress/output -> completed/failed | Flat `content_start(tool_use)`, `content_delta(toolInput/toolOutput)`, `tool_result` | A visible tool_use must have exactly one visible tool_result or a cancelled/error result. |
| Approval loop | tool/policy/internal gate | requested -> waiting_approval/paused -> resolved -> resume/ignore | `permission_request` only for real user decisions | `completion_review` is internal; never create chat permission cards for it. |
| Task lifecycle | send/resume/cancel/error | queued/running/paused/waiting_approval/completed/failed/cancelled | `task_update`, compact panel state | Terminal states are absorbing: no later review/resume/tool events may re-enter chat. |

The reference backend rhythm, as seen in `docs/cc-haha-main*/src/query.ts`, is:

1. Stream assistant blocks from the model.
2. If a `tool_use` block appears, record the assistant block and execute tools.
3. Yield tool results back as user/tool-result messages for the next model turn.
4. On fallback or abort, discard orphaned streaming tool results and emit missing/cancelled tool results to keep the protocol complete.
5. If no tool use remains, finalize the assistant turn; recovery/compact/failure logic is not rendered as ordinary chat.

Yuanbao does not need to be byte-for-byte identical, but should preserve the same
observable contract:

- The chat transcript is a typed protocol, not a raw trace viewer.
- Internal recovery, advisor, completion review, and routing data live in trace/panel only.
- Live streaming and session replay must apply the same visibility rules.
- User answers to approvals/questions are supplements to the existing task, not new user goals.
- Tool availability should be route-specific. Simple cleanup should not open broad read/search/write tools.

Immediate corrections now in scope:

- Completion-review approval submit is ignored once a task is terminal, preventing cancelled tasks from being resumed or failed by a late review.
- `completion_review` approvals are no longer bridged into chat `permission_request` frames and are filtered from runtime cards.
- Trace replay suppresses later chat events for a cancelled task so re-entering a session does not reveal buffered internal tail events.
- Generated/local-only paths such as `%SystemDrive%`, Python caches, local memory files, IDE workspace state, and temp screenshots are blocked from `write_file` and `apply_patch`.
- Cleanup-oriented goals get a narrow `cleanup_noise` tool policy instead of the root `*` tool set.
- Explicit read-only user constraints narrow the provider-visible tools to read/context tools plus `ask_user_question`; explicit plan mode still keeps `enter_plan_mode`/`exit_plan_mode`, and explicit read-only multi-agent prompts may keep `agent`/`task` while withholding write-capable tools.
- Default mock/provider fallback no longer creates a fixed workspace probe. Ordinary natural-language prompts must finish as a provider turn unless the model emits tool calls; explicit `run command:`, `apply patch:`, `show git status`, `show git diff`, or opt-in `provider.deterministicFallback` may still enter the deterministic tool loop for diagnostics/tests.

Follow-up corrections still needed:

- Plan/swarm creation should keep emitting structured `task_update/team_update` and subtask cards; the clean frontend now preserves structured `plan_update` payloads and renders `plan/tasks/subtasks` as cards instead of raw JSON.
- Thinking must be segmented by turn phase: before tool use, after tool result, and before final text, never as duplicated markdown/source blocks. Live and trace replay now close transient/provider thinking at tool and command boundaries, including command completion/failure/cancel events.
- Child-agent names should be semantic and user-readable; internal task ids should stay in metadata.
- Completion evidence should be rendered as compact counts/preview rows in panel traces, not JSON blobs.
- Provider/runtime failures should collapse into one retry/error status instead of repeated memory/open-issue/recovery-card rows. This now includes auth/401 and unknown provider errors, not only transient 429/rate-limit failures.
- Provider/runtime failures must not create flat `goal_event` or `memory_event` notifications. A failure may still update task state and assistant error text, but recovery diagnostics stay in trace.

## 2026-06-05 Live/Replay Projection Update

- Recoverable chat-compatible frames now persist with `_bridge.persistTraceMirror=true`: `thinking`, `content_start`, `tool_use_complete`, `tool_result`, `message_complete`, `status`, `permission_request`, `plan_update`, and tool `content_delta` frames that carry `toolInput` or `toolOutput`.
- Normal assistant text `content_delta` still does not persist as trace because durable assistant messages replay the text. This avoids duplicate final text after reload.
- Raw `tool.*` lifecycle stays `trace` and is not the primary chat replay source. Frontend replay restores tool rows from flat `content_start(tool_use)` -> `tool_use_complete` -> `tool_result` frames.
- Raw `message.completed` remains an internal message lifecycle event and suppresses realtime/replay flat projection. The visible flat finalization is `message_complete`, so external adapters do not receive duplicate finalize/flush events.
- `thinking` is finalized before visible tool/command rows in both realtime subscription and trace replay. A new provider thinking segment after a tool result stays after that tool instead of merging into the earlier segment. Backend `status` and `assistant_progress` are not rendered as thinking.
- Structured `plan_update` payloads are no longer filtered as low-signal startup noise when they contain `plan`, `tasks`, or `subtasks`. This keeps plan/swarm panels visible during live display and session recovery.
- Team/member snapshots with flat `members` are projected as agent task rows, hiding raw member ids as metadata and avoiding duplicated `currentTask` title/summary text.
- Added focused contracts for repeated replay idempotency: many thinking/flat-tool/progress cycles can be replayed multiple times, including out-of-order input, without duplicate visible ids or missing final text.

## 2026-06-06 Single Stream And Real LLM Probe

The current text-stream contract is now:

- `assistant.token` is a legacy/internal compatibility event. It may still be
  produced by the runtime so old paths can observe provider tokens, but its raw
  event visibility is `trace`.
- `message.delta` is the only chat-visible assistant text stream. The flat
  projection of a `message.delta` is `content_delta`.
- `content_start(text)` and `message_complete` are the visible start/end frames.
  Raw `message.completed` suppresses realtime and replay flat projection, so
  external adapters do not finalize twice.
- Live event envelopes use the persisted trace `id/sequence` as
  `eventId/seq`. `events.after` replay and realtime subscription therefore
  share the same cursor identity.
- Frontend live subscription and trace replay both consume `_chatCompat`
  `message.delta`; `_chatCompat` `assistant.token` stays ignored in chat to
  avoid a second text stream.

Real Responses streaming probe with `gpt-5.4-mini` covered five cases:

- Simple Chinese greeting and simple English reply: one provider turn, zero
  tool calls, flat output limited to `content_start(text) -> content_delta* ->
  message_complete`; runtime `status` stayed trace-only.
- README summary: model-selected `list_dir/search_files/read_file`, no forced
  git/verify, no `ask_user_question`.
- Read-only optimization plan: model-selected read tools only, no write or
  test commands, no fixed inspect/apply/verify/report scaffold.
- Tiny README edit: model-selected read tools, then `apply_patch`; runtime
  stopped at `permission_request` before write. No tests were forced because
  the user only requested a README sentence.
- Approval resume probe: first approval resumed the same task and completed;
  a second submit for the same approval returned ignored and emitted no new
  task output.

Observed trace-only internals:

- `agent.decision.completion` remains `visibility=trace`. It may contain audit
  JSON for debugging, but it is not a chat/panel frame and has no flat
  Yuanbao/haha-cc message projection.
- Provider request/response and preflight decisions remain trace diagnostics.

Remaining watch items:

- The tested provider did not emit true token-level reasoning events, so
  `thinking` remains provider-dependent. Production backend no longer emits
  synthetic `assistant_progress`; Yuanbao-only progress must use typed
  panel/status events and stay out of the main chat/replay stream.
- Long desktop plan/swarm/team sessions still need visual pressure testing
  after the single-stream fix, especially for duplicate panels and raw JSON in
  approval details.

## 2026-06-06 Flat Protocol Boundary Fix

After the real Responses probe, the `events.after` serialization path was
checked directly because the frontend consumes serialized envelopes, not only
raw `trace_events.payload_json`.

The current verified flat output is:

- Simple answers: `content_start`, `content_delta`, `message_complete`.
- Read-only tool use: `content_start(tool_use)`, `tool_use_complete`,
  `tool_result`, final text, `message_complete`.
- Write approval: same tool frames, then exactly one `permission_request`.
- No flat `status`.
- No flat `system_notification` derived from `plan_update`.
- No chat/panel raw leaks for `advisorRequestedEvidence`,
  `completionEvidence`, `providerRequest`, or policy diagnostics.

`plan_update` remains a structured chat-visible event for the clean frontend
panel. The adapter intentionally returns no Yuanbao/haha-cc flat message for
it, matching the reference principle that model/tool trajectory frames are not
mixed with backend panel state.
