# haha-cc Parity Parallel Workplan

This document splits the remaining parity work into non-overlapping tracks so large changes can run in parallel without clobbering each other.

## Current Principle

- Main chat should be a typed model/tool transcript: thinking, text, tool use, tool result, permission, completion.
- Runtime, task, provider, recovery, completion evidence, and raw JSON belong in panel/trace, not chat.
- Tool trees are structural through `toolUseId` and `parentToolUseId`. Display grouping fields are secondary.
- Completion review is an internal gate. It must not become a user-facing chat approval or a new user goal.
- Live and replay must consume the same Yuanbao chat-compatible frames.

## Track A: Backend Lifecycle And Projection

Owner: main agent.

Write scope:

- `runtime/src/local_agent_runtime/**`
- `runtime/tests/**`
- shared protocol files only when backend frame shape requires it.

Tasks:

1. Close pending visible tools on terminal paths.
   - Rejection, cancellation, and task failure must emit one terminal `tool_result isError=true` for the pending `toolUseId`.
   - Waiting approval may remain unresolved while the task is actually waiting.

2. Tighten chat projection.
   - `YuanbaoServerMessage` history should come from canonical `_chatCompat` frames.
   - Raw `tool.*`, `command.*`, `task.*`, provider diagnostics, and internal completion events stay in trace/panel.

3. Normalize tool metadata at source.
   - Preserve `parentToolUseId` as the only structural tree edge.
   - Keep `toolSemanticParent*`, `toolGroup*`, `toolPhase*`, and display fields consistent across started/completed/failed/blocked/cached/defaulted paths.

4. Slim public completion evidence.
   - Public task/result surfaces should carry only status, summary, counts, and concise issues.
   - Full evidence/audit stays in trace or diagnostics.

Tests:

- `runtime/tests/test_yuanbao_event_adapter.py`
- `runtime/tests/test_backend_flow_contracts.py`
- `runtime/tests/test_orchestrator_react_loop.py`
- A live-vs-replay backend parity test comparing emitted `event["yuanbao"]` with `events.yuanbaoAfter`.

## Track B: Frontend Transcript And Panels

Owner: frontend worker agent.

Write scope:

- `app/src/**`
- `app` tests only.

Do not edit:

- `runtime/src/**`
- `runtime/tests/**`

Tasks:

1. Hide internal completion review from main chat.
   - `completion_review` / `advisor_tool` approval records should not render as chat approval cards.
   - Runtime/panel view may show a slim summary, not raw evidence JSON.

2. Improve tool block rendering from existing source fields.
   - Prefer `displayTitle`, `displaySummary`, `displayTarget`, `resultSummary`, and `resultPreview`.
   - Successful tool details should not open with raw input/result JSON as visible content.
   - Failed tools may show compact reason/recovery details.

3. Align tool tree behavior with haha-cc.
   - Pair `tool_use` and `tool_result` by id.
   - Nest children by `parentToolUseId`.
   - Avoid duplicate standalone child/result rows when they are already in a group.

4. Clean Agent/Team/Plan cards.
   - Prefer role, current task, title, summary, and status.
   - Do not expose `ctask_*`, `task_*`, handoff ids, or raw metadata as primary titles.
   - Plan JSON should render as panel rows/cards when it is intentionally visible.

5. Strengthen replay tests.
   - Durable assistant text plus trace mirror must not duplicate final text.
   - Raw task/provider/tool progress JSON must not appear after reload.

Tests:

- `app/src/ui/haha-clean/conversation/CleanConversation.test.tsx`
- `app/src/ui/haha-clean/conversation/CleanSessionWorkspace.test.tsx`
- `app/src/state/chatTraceReplay.test.ts`
- `app/src/hooks/useEventSubscription.test.tsx`
- `npm run typecheck` when feasible.

## Merge Order

1. Merge Track A backend lifecycle/projection fixes first.
2. Rebase or review Track B against the new canonical frame behavior.
3. Run backend tests.
4. Run frontend tests and typecheck.
5. Run one Tauri live/replay smoke with `npm run tauri:dev`.

## Current Known Risks

- Tightening projection can break tests that expected raw events to map to flat messages.
- Frontend may still need defensive filters for historical sessions, but new live/replay data should be source-clean.
- Completion evidence exists in useful diagnostics paths; do not delete diagnostics, only remove it from chat-facing surfaces.

## Current Execution Status

Track A backend work is in progress in this thread.

Completed in this pass:

- Pending visible tools now close with exactly one terminal `tool_result` on approval rejection, cancellation, and task failure.
- Yuanbao / haha-cc flat output has one canonical field: `yuanbao`. The old parallel `hahaCc` flat field is no longer expected.
- Official typed chat frames (`content_start`, `content_delta`, `tool_use_complete`, `tool_result`, `thinking`, `status`, `message_complete`) can project directly.
- Raw provider/runtime aliases (`assistant.token`, `message.delta`, `message.completed`, `tool.progress`, `tool.output`, `command.output`) do not become chat unless they are intentionally bridged.
- Cached tool results now preserve display, phase, semantic, group, and parent metadata.
- Live/replay contract is covered so raw runtime events do not appear only after refresh or stop.

Backend tests currently passing:

- `python -m pytest runtime/tests/test_backend_flow_contracts.py runtime/tests/test_haha_cc_compat.py runtime/tests/test_yuanbao_event_adapter.py -q`
- `python -m pytest runtime/tests/test_tool_schemas_trace.py runtime/tests/test_provider_turns.py -q`
- `python -m pytest runtime/tests/test_orchestrator_react_loop.py -q -k "cache or metadata or semantic"`

Still open on Track A:

- Update or retire old P9 tests that still encode the pre-parity contract, such as root `task.started` as chat, duplicated `hahaCc`, and hidden tool group metadata.
- Decide whether tool result chat content should keep the current concise `{status, summary, target, preview}` shape or re-add a separate compact `truncated` flag for very large outputs.
- Continue trimming completion evidence from user-facing task surfaces without deleting diagnostic trace data.

Track B frontend work is delegated to Mendel.

Mendel owns only `app/src/**` and frontend tests. It should not touch backend runtime files. Its goal is to align the chat transcript, tool cards, agent/team/plan panels, and replay behavior with the backend canonical frames above.
