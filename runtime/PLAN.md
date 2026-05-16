# Yuanbao Agent Current Plan

Last updated: 2026-05-14

This file is a short working plan for the next implementation pass. The long-form source of truth remains `docs/YUANBAO_AGENT_DOCS_CONSOLIDATED.md`.

## Current Baseline

Large-file refactoring is no longer the active blocker.

Completed:

- `runtime/src/local_agent_runtime/orchestrator/service.py` has been reduced to a compatibility facade.
- `runtime/src/local_agent_runtime/store/sqlite_store.py` has been reduced to a compatibility facade.
- `app/src/App.tsx`, `SessionWorkspace.tsx`, `session.css`, `runtimeClient.ts`, and `SettingsWorkspace.tsx` have been split to manageable sizes.
- ToolPolicyResolver + Dynamic Agent Profile minimum loop is complete.
- Real provider path hardening is complete for the current OpenAI-compatible chat-completions path.
- Completion Evidence and the first five completion hard-gate layers are complete.
- Worktree auto-binding for write tasks is complete through merge approval and merge verification commands.
- Worktree Closure Phase 2 is complete: merge approvals now carry reviewer/user approval summaries, diff preview/full-diff metadata, and multi-agent worktree strategy context.
- Completion Audit Phase 2 is complete: completion evidence now records framework-matched verification requirements, completion review approvals/rejections persist review conclusions, and approval cards surface review resolution history.
- Hooks Lifecycle Completion is complete: hook executions now emit trace-visible runtime events, run_command hooks route through PermissionEngine, changed-file conditions accept structured file entries, and pause/resume/compaction/worktree lifecycle wiring is covered by tests.
- ToolPolicyResolver Phase 2 is complete: provider tool exposure now records phase, role, child allowlist, Skill policy, MCP policy, and PermissionEngine decisions in one resolver path; provider turns persist the decision snapshot for replay/audit.

Recent verification recorded in the consolidated docs includes:

- Runtime focused pytest suites for completion, worktree, routing, provider turns, and tool policy.
- App tests for `SessionWorkspace` and event/view computations.
- App typecheck.
- Tauri `cargo check`.

## Next Priority

### P1: Dynamic Agent Profile UI/RPC

Goal: let users manage profile definitions instead of relying only on runtime snapshots.

Next steps:

1. Add profile list/create/update/delete/validate RPC.
2. Add previewTools RPC using the same resolver path as provider turns.
3. Add Settings UI for profile editing and tool preview.

### P1: Provider API Format Expansion

Goal: expand provider support without letting users select unimplemented formats.

Status: completed in working tree; keep this plan file local/untracked unless the user asks otherwise.

Completed:

1. Implemented `openai-responses` request/response adaptation.
2. Added native `anthropic-messages` request/response adaptation.
3. Included effective `apiFormat`, request path, and failure reason in provider test records.

### P1: Real LLM Smoke Runner

Goal: keep real-provider smoke tests repeatable without leaking credentials or committing generated artifacts.

Next steps:

1. Add an optional smoke runner that reads keys only from environment variables.
2. Keep generated files under ignored temp/smoke directories.
3. Record structured smoke output without storing secrets.

## Repository Hygiene

Current known untracked artifacts:

- `runtime/PLAN.md`
- `runtime/smoke_runs/music_player_glm_after_chain_fix_1778722941_459760/app.js`
- `runtime/smoke_runs/music_player_glm_after_chain_fix_1778722941_459760/index.html`
- `runtime/smoke_runs/music_player_glm_after_chain_fix_1778722941_459760/styles.css`

Before the next commit, decide whether `runtime/PLAN.md` should be tracked as the short working plan. Smoke-generated files should usually stay untracked unless the task explicitly needs to preserve them as fixtures.
