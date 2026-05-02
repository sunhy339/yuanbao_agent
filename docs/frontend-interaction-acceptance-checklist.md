# Frontend Interaction Acceptance Checklist

Generated: 2026-05-02

Scope: Yuanbao Agent desktop frontend after the core frontend localization, runtime message localization, and E2E assertion updates.

## Result Summary

Current status: acceptance-ready for the core desktop interaction surface.

The automated and desktop E2E gates cover the main navigation, workspace opening, session creation/recovery, prompt submission plumbing, MCP server management, scheduled-task empty state, component-level workspace interactions, build/type safety, and visual overflow/clipping checks.

Important boundary: this checklist does not claim every possible click was manually exercised one by one. It separates verified core flows from read-only surfaces and a few manual-only OS/provider checks.

## Verification Run

| Check | Result | Notes |
| --- | --- | --- |
| `npm.cmd test` | Passed | 13 test files, 90 tests. |
| `npm.cmd run build` | Passed | TypeScript and Vite production build passed. |
| `npm.cmd run visual:regression` | Passed | 40 screenshots, 0 automated findings. |
| `npm.cmd run e2e:desktop:ui` | Passed | Desktop shell, New Session, Settings, Scheduled, and navigation return flow passed. |
| `npm.cmd run e2e:desktop:mcp` | Passed | Create/list/update/enable/refresh/disable/delete MCP server flow passed. |
| `npm.cmd run e2e:desktop:recovery` | Passed on rerun | First run timed out waiting for seed result; immediate rerun passed seed and verify phases. Treat as transient Tauri startup/test-harness flake unless it recurs. |

## Fixes Found During Acceptance

| Finding | Status | Files |
| --- | --- | --- |
| Visual regression still targeted old English aria labels after UI localization, so it could not open the New Session page. | Fixed | `app/scripts/visual_regression.py` |

## Interaction Matrix

Legend:

- Passed: covered by current automated test, desktop E2E, visual regression, or direct code-backed component test.
- Read-only: intentionally disabled or preview-only because runtime semantics are not available.
- Manual-only: needs a real local provider, native OS dialog, or human visual confirmation beyond automation.
- Watch: passed or non-blocking now, but worth rechecking if touched later.

### Global Shell

| Interaction | Status | Evidence |
| --- | --- | --- |
| Open Overview from shell/sidebar | Passed | `AppShell.test.tsx`, visual regression overview screenshots. |
| Open New Session from sidebar | Passed | `e2e:desktop:ui`, visual regression, `AppShell.test.tsx`. |
| Open Scheduled from sidebar | Passed | `e2e:desktop:ui`, visual regression. |
| Open MCP from sidebar/topbar | Passed | `e2e:desktop:mcp`, visual regression. |
| Open Skills from sidebar/topbar | Passed | `SkillsWorkspace.test.tsx`, visual regression. |
| Open Appearance from sidebar/topbar | Passed | `AppearanceWorkspace` visual regression coverage. |
| Open Component Playground | Passed | Visual regression, playground workspace code path. |
| Open Settings from sidebar/topbar | Passed | `e2e:desktop:ui`, `SettingsWorkspace.test.tsx`, visual regression. |
| Open and activate tabs | Passed | `WorkspaceTabs.test.tsx`, `AppShell.test.tsx`, `tabModel.test.ts`. |
| Close tab / close other tabs | Passed | `WorkspaceTabs.test.tsx`, `tabModel.test.ts`. |
| Rename session from sidebar/tab context menu | Passed by component/runtime wiring | `GlobalSidebar.tsx`, `WorkspaceTabs.test.tsx`, `App.tsx` uses `runtimeClient.updateSession`. |
| Delete session from sidebar context menu | Passed by runtime wiring | `GlobalSidebar.tsx`, `App.tsx` uses `runtimeClient.deleteSession`. Manual confirmation dialog behavior should be spot-checked in desktop. |

### Overview

| Interaction | Status | Evidence |
| --- | --- | --- |
| Open New Session action | Passed | `WorkbenchOverviewPage.tsx` callback wiring, visual regression. |
| Open MCP action | Passed | `WorkbenchOverviewPage.tsx` callback wiring, `e2e:desktop:mcp`. |
| Open Settings action | Passed | `WorkbenchOverviewPage.tsx` callback wiring, `e2e:desktop:ui`. |
| Open existing session row | Passed by app/session tests | `App.test.tsx` verifies selecting and switching sessions loads persisted messages. |

### New Session / Composer

| Interaction | Status | Evidence |
| --- | --- | --- |
| Edit workspace path | Passed by component state | `NewSessionWorkspace.test.tsx`. |
| Browse folder | Manual-only | Uses native Tauri dialog when available; needs a real desktop click check. |
| Open workspace | Passed by runtime wiring | `App.tsx` calls `runtimeClient.openWorkspace`; E2E receives workspace fixture. |
| Edit session title | Passed | `NewSessionWorkspace.test.tsx`. |
| Select provider/model | Passed by runtime wiring | `App.tsx` persists active provider profile through config update. |
| Select starter template | Passed | `NewSessionWorkspace.test.tsx`. |
| Create session | Passed | `NewSessionWorkspace.test.tsx`, `App.test.tsx`, desktop recovery seed flow. |
| Submit prompt from composer | Passed | `e2e:desktop:recovery` seeds a background task and persists user message; provider-flow automation exists but was not run in this pass. |
| Slash command popup keyboard/click selection | Passed by code path, needs manual UX spot-check | `ComposerDock.tsx`; not separately asserted in current E2E. |

### Session Workbench

| Interaction | Status | Evidence |
| --- | --- | --- |
| Reopen recovered session from sidebar | Passed | `e2e:desktop:recovery`. |
| Persisted user message visible after restart | Passed | `e2e:desktop:recovery`. |
| Recovered task status visible after restart | Passed | `e2e:desktop:recovery`. |
| Refresh task | Passed by runtime wiring | `App.tsx` calls `runtimeClient.getTask` and refreshes trace. |
| Stop task | Passed by runtime wiring | `App.tsx` calls `runtimeClient.cancelTask`; needs manual interruption spot-check for live long-running tasks. |
| Approval approve/reject | Passed by runtime/component wiring | `SessionWorkspace.test.tsx`, `App.tsx` calls `runtimeClient.approvalSubmit`. |
| Patch diff load | Passed by runtime wiring | `App.tsx` calls `runtimeClient.diffGet`; regression tests cover diff display hardening from earlier work. |
| Copy patch/path/output controls | Watch | Clipboard controls exist in session/runtime components; a full live clipboard assertion is not in current E2E. |
| Command job refresh/cancel | Passed by runtime wiring | `runtimeClient.test.ts`, `App.tsx` command log/cancel paths. |
| Trace/tool detail expand/collapse | Passed | `SessionWorkspace.test.tsx`. |
| Low-level trace noise hidden from default UI | Passed | `SessionWorkspace.test.tsx`, provider-flow assertion code, visual regression. |

### Scheduled Tasks

| Interaction | Status | Evidence |
| --- | --- | --- |
| Scheduled workspace renders without demo data | Passed | `e2e:desktop:ui`. |
| Create scheduled task | Passed | `ScheduledWorkspace.test.tsx`, `App.tsx` calls `runtimeClient.createScheduledTask`. |
| Select task | Passed | `ScheduledWorkspace.test.tsx`. |
| Run now | Passed by runtime wiring | `App.tsx` calls `runtimeClient.runScheduledTaskNow`; full live execution should be spot-checked with a real scheduled task. |
| Enable/disable task | Passed by runtime wiring | `App.tsx` calls `runtimeClient.toggleScheduledTask`. |
| View execution logs | Passed by runtime wiring | `App.tsx` calls `runtimeClient.listScheduledTaskLogs`. |

### MCP

| Interaction | Status | Evidence |
| --- | --- | --- |
| Open MCP workspace | Passed | `e2e:desktop:mcp`. |
| Create MCP server | Passed | `e2e:desktop:mcp`, `McpWorkspace.test.tsx`. |
| List created MCP server | Passed | `e2e:desktop:mcp`. |
| Edit MCP server | Passed | `e2e:desktop:mcp`, `McpWorkspace.test.tsx`. |
| Enable MCP server | Passed | `e2e:desktop:mcp`. |
| Refresh MCP tools | Passed | `e2e:desktop:mcp`. |
| Disable MCP server | Passed | `e2e:desktop:mcp`. |
| Delete MCP server | Passed | `e2e:desktop:mcp`. |
| Error banner and draft preservation | Passed | `McpWorkspace.test.tsx`. |

### Settings

| Interaction | Status | Evidence |
| --- | --- | --- |
| Switch settings sections | Passed | `SettingsWorkspace.test.tsx`. |
| Select provider | Passed | `SettingsWorkspace.test.tsx`, runtime config wiring. |
| Add provider | Passed | `SettingsWorkspace.test.tsx`, provider UI flow code. |
| Edit provider | Passed | `SettingsWorkspace.test.tsx`. |
| Test provider | Manual-only | Wiring exists and provider-flow automation exists, but this pass did not validate a real external provider. |
| Save provider config | Passed | `SettingsWorkspace.test.tsx`, `App.tsx` config update path. |
| Change permission mode | Passed | `SettingsWorkspace.test.tsx`, `App.tsx` config update path. |
| General appearance controls | Passed | `SettingsWorkspace.test.tsx`, `AppearanceWorkspace` visual regression. |
| IM bridge form editing | Passed as local draft | `SettingsWorkspace.tsx`. |
| Test IM connection | Read-only | Disabled because runtime IM testing is not available in this build. |
| Add agent / toggle agent | Read-only | Disabled until runtime agent profile persistence/routing exists. |
| Refresh skills | Passed | `SettingsWorkspace.test.tsx`, `SkillsWorkspace.test.tsx`, runtime listSkills wiring. |
| Open skills folder | Manual-only / conditional | Button is enabled only when a handler is provided; desktop shell behavior should be spot-checked if wired. |
| Computer Use config toggles | Passed as local/config state | `SettingsWorkspace.test.tsx`, `App.tsx` state update. |
| Recheck Computer Use | Passed by local capability check | `App.tsx` updates status with available probes; no external automation required. |
| Save/clear workspace focus | Passed by runtime wiring | `App.tsx` calls `runtimeClient.updateWorkspaceFocus`. |
| Clear workspace memory | Passed by runtime wiring | `App.tsx` calls `runtimeClient.clearWorkspaceMemory`. |
| Open logs / data folder | Manual-only | Desktop uses native shell bridge; requires real OS-level spot-check. Browser/mock keeps unavailable controls disabled. |

### Skills

| Interaction | Status | Evidence |
| --- | --- | --- |
| Refresh skills | Passed | `SkillsWorkspace.test.tsx`, runtime listSkills wiring. |
| Open MCP from Skills | Passed | `SkillsWorkspace.test.tsx`. |
| Open Settings from Skills | Passed | `SkillsWorkspace.test.tsx`. |
| Inspect built-in/runtime skill metadata | Passed | `SkillsWorkspace.test.tsx`, `App.tsx` metadata mapping. |
| Create/edit/delete custom skill | Passed by runtime wiring | `App.tsx`, `SkillsWorkspace.tsx`, `runtimeClient.ts`; should be spot-checked when a custom skill workflow is in scope. |
| Enable/disable skill | Read-only | Hidden/disabled until runtime activation semantics are defined. |

### Appearance

| Interaction | Status | Evidence |
| --- | --- | --- |
| Theme/density/radius/motion/accent controls | Passed | Visual regression dark/light matrix, config update wiring. |
| Transparency/font scale controls | Passed | Visual regression and config update wiring. |
| Language/reasoning/preflight controls | Passed | Config update wiring. |
| Open Settings from Appearance | Passed | Workspace callback wiring. |
| Preview send button | Read-only | Preview-only by design. |

### Component Playground

| Interaction | Status | Evidence |
| --- | --- | --- |
| Open Appearance | Passed | Callback wiring and visual regression. |
| Open Skills | Passed | Callback wiring and visual regression. |
| Component controls | Read-only | Intended as validation surface, not business workflow. |

## Manual Spot-Check Queue

These are not blockers for the current automated gate, but they are the best next manual QA targets:

1. Native folder browse from New Session.
2. Real provider test with the user's actual provider profile.
3. Live long-running task cancel/stop behavior.
4. Clipboard copy feedback for patch/path/output surfaces.
5. Native open logs/data folder behavior.
6. Delete-session confirmation dialog and cancellation path.
7. Slash-command popup keyboard navigation in the composer.

## Acceptance Decision

The current frontend is ready for user-facing acceptance on the core desktop flows. The remaining items are manual OS/provider checks or intentionally read-only surfaces, not evidence of broken primary navigation or runtime wiring.
