# Frontend V2 Follow-up Plan

Generated: 2026-05-02

## Goal

Push the committed Frontend V2 workbench from "broadly implemented" to "interaction-complete and release-ready" for the desktop client.

This plan is ordered. Work should proceed from top to bottom unless a blocker requires temporarily switching to the next item. Each item should end with tests or a clear note explaining why it could not be verified.

## Current Baseline

- Latest frontend commit: `1068d40 feat: add frontend workbench v2`.
- Verified before that commit:
  - `npm.cmd test`: 11 files, 78 tests passed.
  - `npm.cmd run build`: passed.
- Committed frontend scope:
  - V2 shell, overview, new session, session workspace, scheduled tasks, MCP, skills, appearance, settings, component playground.
  - Shared config/domain/RPC additions for V2 UI, skills, and MCP.
  - Tauri command bridge additions for skill and MCP calls.
- Known uncommitted non-frontend work remains in `runtime/`, `.pytest-tmp-*`, and miscellaneous local files. Keep those out of frontend-only commits unless a task explicitly requires runtime changes.

## Status Definitions

- Real: action reaches runtime or persisted app state and has visible success/error feedback.
- Local: action updates frontend-only draft state or current React state but does not reach runtime.
- Placeholder: action only shows a message, disabled control, or mock/fallback behavior.
- Read-only: intentionally non-interactive until backend semantics are defined.
- Unknown: needs manual inspection or desktop QA.

## Phase 1: Interaction Audit

Objective: create a full map of visible actions and classify whether each action is Real, Local, Placeholder, Read-only, or Unknown.

- [x] Audit global shell actions:
  - Sidebar navigation.
  - Tab open/activate/close/close-others.
  - Session rename/delete.
  - Topbar shortcuts.
  - Composer submit.
- [x] Audit Overview actions:
  - Open new session.
  - Open existing session.
  - Open scheduled workspace.
  - Open MCP workspace.
  - Open settings.
- [x] Audit New Session actions:
  - Workspace path edit.
  - Folder picker/open workspace.
  - Session title edit.
  - Model selection.
  - Template selection.
  - Create session.
- [x] Audit Session Workbench actions:
  - Send prompt.
  - Refresh task.
  - Stop task.
  - Approve/reject approval.
  - Load patch diff.
  - Copy patch path.
  - Refresh/stop command job.
  - Refresh trace.
  - Tool/trace/detail expansion and copy controls.
- [x] Audit Scheduled Tasks actions:
  - Create task.
  - Select task.
  - Run now.
  - Enable/disable.
  - View logs.
- [x] Audit MCP actions:
  - Refresh servers.
  - Create server.
  - Enable/disable server.
  - Refresh tools.
  - Delete server.
  - Diagnostics visibility.
- [x] Audit Settings actions:
  - Provider select/add/edit/test/save.
  - Permission mode change.
  - Appearance/general settings.
  - IM bridge form/test.
  - Agents section.
  - Skills refresh/open folder.
  - Computer Use settings/recheck.
  - Workspace focus save/clear.
  - Workspace memory clear.
  - Open logs/data directory.
- [x] Audit Agent Skills actions:
  - Refresh skills.
  - Open MCP.
  - Open Settings.
  - Skill/agent matrix controls.
- [x] Audit Appearance actions:
  - Theme/density/radius/motion/accent changes.
  - Transparency/font scale sliders.
  - Language/reasoning/preflight changes.
  - Link back to Settings.
- [x] Audit Component Playground actions:
  - Ensure it is clearly a non-business validation surface.

Deliverable:

- [x] Add `docs/frontend-v2-interaction-audit.md` with an action table, status, source file, and follow-up recommendation.

## Phase 2: Remove or Label Placeholders

Objective: users should not see controls that imply unsupported behavior.

- [x] Replace placeholder-only actions with disabled controls plus explicit disabled reason, or remove them from primary surfaces.
- [x] Convert intentionally read-only skill/agent areas into clear read-only views.
- [x] Ensure all disabled actions use consistent UI treatment.
- [x] Add component tests for any placeholder removal.

Expected early candidates:

- IM bridge connection test.
- Add agent / agent toggle.
- Skills open folder.
- Computer Use recheck.
- Open logs.
- Open data folder.

First pass completed:

- IM bridge test, Computer Use recheck, Open logs, and Open data folder no longer receive placeholder click handlers.
- Settings now shows explicit notes for unavailable desktop shell/permission/IM actions.
- Agents remain visibly read-only until runtime agent management is defined.
- Runtime-only actions that currently set a generic error string.

## Phase 3: Close P0 Desktop Utility Gaps

Objective: make high-value desktop utility actions actually work.

- [x] Implement Tauri-side folder opening for logs/data directory, or explicitly remove the buttons from Settings.
- [x] Decide and implement Computer Use permission recheck behavior, or mark Computer Use as read-only configuration.
- [x] Add user-facing success/error to settings persistence paths that currently change state silently.
- [x] Verify behavior in the Tauri desktop client, not only browser mock mode.

Progress:

- Added `open_app_path` Tauri command for `logs` and `data`.
- Browser/mock mode keeps local folder actions disabled.
- Desktop mode wires Settings buttons to `runtimeClient.openAppPath`.
- Computer Use recheck now performs a conservative local capability check: clipboard API, desktop shell bridge, and sensitive-confirmation readiness are reported, while screen/browser/system-shortcut probes are explicitly listed as not wired.
- Added success toasts for search settings, provider settings, command policy, permission mode, and appearance/general settings persistence.

Verification:

- [x] `npm.cmd test`
- [x] `npm.cmd run build`
- [x] Targeted Rust check if Tauri bridge changes are made.
- [x] Targeted desktop smoke test if Tauri bridge changes are made.

## Phase 4: Skill and Agent Management

Objective: turn the Skills/Agents surfaces into real configuration only where backend semantics are ready.

- [x] Align frontend with runtime skill registry capabilities.
- [x] Add inspect skill prompt/details if runtime exposes it.
- [x] Add create/edit/delete custom skill only after persistence and validation are available.
- [x] Add enable/disable only if runtime supports per-skill activation semantics.
- [x] Define agent profile schema before exposing add/toggle agent controls.
- [x] Add tests covering read-only vs editable modes.

Progress:

- Preserved runtime skill metadata (`systemPrompt`, `toolWhitelist`, `isBuiltin`) when mapping skill registry records into frontend views.
- Added a read-only skill inspector in the Agent Skills workspace so users can check the prompt and tool allowlist exposed by the runtime registry.
- Added create/edit/delete controls for custom presets only, wired through `runtimeClient.createSkill`, `runtimeClient.updateSkill`, and `runtimeClient.deleteSkill`.
- Kept built-in presets read-only and kept enable/disable controls hidden until runtime activation semantics are fully defined.
- Verified that current runtime skill persistence has no per-skill `enabled` or `active` semantics; the UI should continue to treat listed skills as available presets rather than independently toggleable records.
- Defined the agent profile schema proposal in `docs/frontend-v2-agent-profile-schema.md`; Settings agent add/toggle controls remain hidden until the shared RPC and runtime persistence exist.

## Phase 5: MCP Live QA and Hardening

Objective: prove MCP management works with live runtime data.

- [x] Test list/create/update/delete/toggle/refresh tools against desktop runtime.
- [x] Make server errors persistent and visible without losing saved config.
- [x] Add or update tests for error cases.
- [x] Confirm shared RPC, Tauri bridge, runtime client, and runtime backend naming are aligned.

Progress:

- Confirmed MCP list/create/update/delete RPC names are aligned across shared types, Tauri commands, runtime client, runtime RPC server, orchestrator, and SQLite store.
- Added frontend edit/update support for existing MCP servers so the UI now reaches the existing `runtimeClient.updateMcpServer` path beyond enable/disable toggles.
- Added MCP workspace tests covering create, edit/update, toggle, tool refresh, and delete callbacks.
- Added persistent MCP error banner state in the workspace. Failed create/update keeps the user's draft intact for correction.
- Added and ran `npm.cmd run e2e:desktop:mcp`, which starts the Tauri desktop client with a temporary database and verifies MCP list/create/update/enable/refresh/disable/delete against the live runtime bridge.

## Phase 6: Session Workbench Polish

Objective: make the core daily workflow easier to trust and inspect.

- [ ] Review task state transitions under live runtime.
- [ ] Confirm approval cards remain visible until resolved.
- [ ] Confirm patch diff loading handles missing/large diffs.
- [x] Add copy controls where users naturally expect them: command output, trace detail, patch paths.
- [ ] Normalize raw/mock/provider copy in user-facing text.
- [ ] Re-check long text and narrow desktop layout.

Progress:

- Added command output and trace detail copy controls in the Session Workbench.
- Unified patch path copy through the App-level clipboard helper so copy actions have visible success/error feedback.

## Phase 7: Visual and Regression Gate

Objective: keep the V2 shell stable as interactions become real.

- [ ] Run `npm.cmd test`.
- [ ] Run `npm.cmd run build`.
- [ ] Run `npm.cmd run visual:regression` after UI/layout changes.
- [ ] Run `npm.cmd run e2e:desktop:ui` when desktop shell or Tauri bridge changes.
- [ ] Run `npm.cmd run e2e:desktop:mcp` when MCP bridge or UI management changes.
- [ ] Run `npm.cmd run e2e:desktop:recovery` when session persistence or routing changes.
- [ ] Update `docs/frontend-v2-bug-list.md` for reopened or newly found issues.

## Commit Strategy

- Commit Phase 1 audit documentation separately.
- Commit placeholder cleanup separately from bridge/runtime behavior.
- Commit Tauri bridge changes with matching tests or desktop smoke notes.
- Avoid mixing unrelated `runtime/` work unless the frontend task requires it.
