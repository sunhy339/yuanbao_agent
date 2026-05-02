# Frontend V2 Interaction Audit

Generated: 2026-05-02

Baseline commit: `1068d40 feat: add frontend workbench v2`

Status legend:

- Real: reaches runtime or persisted app state and gives visible state/feedback.
- Local: changes frontend-only state or draft UI state.
- Placeholder: visible action does not complete the implied behavior.
- Read-only: intentionally non-editable until backend semantics exist.
- Unknown: needs live desktop QA.

## Summary

Most core workbench actions are wired to real runtime/client methods. The remaining user-visible gaps are concentrated in Settings utility actions and future Agent/Skill management.

P0 placeholder candidates from the initial audit:

- IM bridge connection test.
- Computer Use permission recheck.
- Open logs folder.
- Open data folder.

First follow-up pass:

- These actions were changed from clickable placeholder handlers to disabled/read-only controls with visible notes.

P1 read-only or not-yet-defined areas:

- Agent add/toggle controls.
- Custom skill create/edit/delete/enable-disable.
- Skill folder opening.
- Live MCP error persistence and diagnostics hardening.

## Global Shell

| Area | Action | Status | Source | Notes |
| --- | --- | --- | --- | --- |
| Sidebar | Open system workspace | Real | `app/src/App.tsx`, `app/src/ui/workbench/GlobalSidebar.tsx` | Calls `handleOpenSystemTab`, opens/dedupes tabs. |
| Sidebar | Open session tab | Real | `app/src/App.tsx`, `app/src/ui/workbench/GlobalSidebar.tsx` | Calls `handleOpenSessionTab`. |
| Sidebar | Rename session | Real | `app/src/App.tsx` | Calls `runtimeClient.updateSession`. |
| Sidebar | Delete session | Real | `app/src/App.tsx` | Calls `runtimeClient.deleteSession`. |
| Tabs | Activate tab | Real | `app/src/App.tsx`, `app/src/ui/workbench/tabModel.ts` | Updates active tab state. |
| Tabs | Close tab | Real | `app/src/ui/workbench/tabModel.ts` | Pure tab-state update. |
| Tabs | Close other tabs | Real | `app/src/ui/workbench/tabModel.ts` | Pure tab-state update. |
| Topbar | Open MCP / Skills / Appearance / Settings | Real | `app/src/ui/v2/layout/AppShellV2.tsx` | Routes to real workspaces. |
| Composer | Submit prompt | Real | `app/src/App.tsx` | Ensures session and calls `runtimeClient.sendMessage`. |

## Overview

| Action | Status | Source | Notes |
| --- | --- | --- | --- |
| Open new session | Real | `app/src/App.tsx`, `app/src/ui/v2/pages/WorkbenchOverviewPage.tsx` | Opens `system:new-session`. |
| Open existing session | Real | `app/src/App.tsx` | Opens session tab. |
| Open scheduled workspace | Real | `app/src/App.tsx` | Opens `system:scheduled`. |
| Open MCP workspace | Real | `app/src/App.tsx` | Opens `system:mcp`. |
| Open settings | Real | `app/src/App.tsx` | Opens `system:settings`. |

## New Session

| Action | Status | Source | Notes |
| --- | --- | --- | --- |
| Edit workspace path | Local | `app/src/App.tsx`, `app/src/ui/workbench/workspaces/NewSessionWorkspace.tsx` | Updates React state until open/create. |
| Browse folder | Real | `app/src/ui/workbench/workspaces/NewSessionWorkspace.tsx` | Uses Tauri dialog when available. Browser fallback is no-op-safe. |
| Open workspace | Real | `app/src/App.tsx` | Calls `runtimeClient.openWorkspace`. |
| Edit session title | Local | `app/src/App.tsx` | Used by create session. |
| Select model/provider profile | Real | `app/src/App.tsx` | Persists selected provider profile through config update. |
| Select template | Local | `NewSessionWorkspace.tsx` | Applies template title. |
| Create session | Real | `app/src/App.tsx` | Calls `runtimeClient.createSession`. |

## Session Workbench

| Action | Status | Source | Notes |
| --- | --- | --- | --- |
| Send prompt | Real | `app/src/App.tsx` | Calls `runtimeClient.sendMessage`, streams/events into UI. |
| Refresh task | Real | `app/src/App.tsx` | Calls `runtimeClient.getTask` plus trace refresh. |
| Stop task | Real | `app/src/App.tsx` | Calls `runtimeClient.cancelTask`. |
| Approve approval | Real | `app/src/App.tsx` | Calls `runtimeClient.approvalSubmit`. |
| Reject approval | Real | `app/src/App.tsx` | Calls `runtimeClient.approvalSubmit`. |
| Load patch diff | Real | `app/src/App.tsx` | Calls `runtimeClient.diffGet`. |
| Copy patch path | Real | `app/src/App.tsx` | Uses clipboard API. |
| Refresh command job | Real | `app/src/App.tsx` | Calls `runtimeClient.commandLogGet`. |
| Stop command job | Real | `app/src/App.tsx` | Calls `runtimeClient.commandCancel`. |
| Refresh trace | Real | `app/src/App.tsx` | Calls `runtimeClient.listTrace`. |
| Trace/tool detail expansion | Real | `SessionWorkspace.tsx` | UI state expansion. |
| Copy tool/command output | Partial | `SessionWorkspace.tsx`, `RuntimeComponents.tsx` | Some copy controls exist in reusable components; live Session surface should be rechecked. |

## Scheduled Tasks

| Action | Status | Source | Notes |
| --- | --- | --- | --- |
| Select task | Real | `app/src/App.tsx`, `ScheduledWorkspace.tsx` | Updates selected task state. |
| Create task | Real | `app/src/App.tsx` | Calls `runtimeClient.createScheduledTask`. |
| Run now | Real | `app/src/App.tsx` | Calls `runtimeClient.runScheduledTaskNow`. |
| Enable/disable | Real | `app/src/App.tsx` | Calls `runtimeClient.toggleScheduledTask`. |
| View logs | Real | `app/src/App.tsx` | Logs are loaded with `runtimeClient.listScheduledTaskLogs`. |

## MCP

| Action | Status | Source | Notes |
| --- | --- | --- | --- |
| Refresh server list | Real | `app/src/App.tsx` | Calls `runtimeClient.listMcpServers`. |
| Create server | Real | `app/src/App.tsx` | Calls `runtimeClient.createMcpServer`. Runtime backend completeness still needs live QA. |
| Enable/disable server | Real | `app/src/App.tsx` | Calls `runtimeClient.updateMcpServer`. |
| Refresh tools | Real | `app/src/App.tsx` | Calls `runtimeClient.refreshMcpTools`. |
| Delete server | Real | `app/src/App.tsx` | Calls `runtimeClient.deleteMcpServer`. |
| Diagnostics visibility | Unknown | `McpWorkspace.tsx` | UI exists; live error persistence needs QA. |

## Settings

| Area | Action | Status | Source | Notes |
| --- | --- | --- | --- | --- |
| Provider | Select provider | Real | `app/src/App.tsx` | Calls provider profile selection/config update. |
| Provider | Add provider | Real | `app/src/App.tsx` | Saves provider profile to config. |
| Provider | Edit provider | Real | `app/src/App.tsx` | Saves provider profile changes to config. |
| Provider | Test provider | Real | `app/src/App.tsx` | Calls `runtimeClient.testProvider`; live provider automation may still be flaky. |
| Provider | Save provider config | Real | `app/src/App.tsx` | Calls `runtimeClient.updateConfig`. |
| Permissions | Change approval mode | Real | `app/src/App.tsx` | Persists policy approval mode. |
| General/Appearance | Theme/density/radius/motion/accent | Real | `app/src/App.tsx` | Calls `runtimeClient.updateConfig`. |
| General/Appearance | Transparency/font scale | Real | `app/src/App.tsx` | Calls `runtimeClient.updateConfig`. |
| General/Appearance | Language/reasoning/preflight | Real | `app/src/App.tsx` | Calls `runtimeClient.updateConfig`. |
| IM | Edit IM form | Local | `SettingsWorkspace.tsx` | Stored in React state only. |
| IM | Test IM connection | Read-only | `SettingsWorkspace.tsx` | Disabled with note: runtime IM testing is not available in this desktop build. |
| Agents | Add agent | Read-only | `SettingsWorkspace.tsx` | Button disabled because no handler is passed. |
| Agents | Toggle agent | Read-only | `SettingsWorkspace.tsx` | Checkbox disabled because no handler is passed. |
| Skills | Refresh skills | Real | `app/src/App.tsx` | Calls `runtimeClient.listSkills`. |
| Skills | Open skills folder | Read-only | `SettingsWorkspace.tsx` | Disabled because no handler is passed. |
| Computer Use | Edit config | Local | `app/src/App.tsx` | React state only. |
| Computer Use | Recheck | Read-only | `SettingsWorkspace.tsx` | Disabled with note: desktop permission recheck is not implemented yet. |
| Workspace | Save focus | Real | `app/src/App.tsx` | Calls `runtimeClient.updateWorkspaceFocus`. |
| Workspace | Clear focus | Real | `app/src/App.tsx` | Calls same save path with empty focus. |
| Workspace | Clear memory | Real | `app/src/App.tsx` | Calls `runtimeClient.clearWorkspaceMemory`. |
| About | Open logs | Real in desktop / Read-only in browser mock | `app/src/App.tsx`, `app/src-tauri/src/lib.rs` | Desktop uses `open_app_path` to create/open the app logs directory; browser mock keeps the control disabled. |
| About | Open data folder | Real in desktop / Read-only in browser mock | `app/src/App.tsx`, `app/src-tauri/src/lib.rs` | Desktop uses `open_app_path` to create/open the app data directory; browser mock keeps the control disabled. |

## Agent Skills

| Action | Status | Source | Notes |
| --- | --- | --- | --- |
| Refresh skills | Real | `app/src/App.tsx`, `SkillsWorkspace.tsx` | Calls `runtimeClient.listSkills`. |
| Open MCP | Real | `app/src/App.tsx`, `SkillsWorkspace.tsx` | Opens MCP workspace. |
| Open Settings | Real | `app/src/App.tsx`, `SkillsWorkspace.tsx` | Opens Settings workspace. |
| Skill preset management | Read-only | `SkillsWorkspace.tsx` | Current UI intentionally avoids unsupported toggles/custom-skill actions. |

## Appearance

| Action | Status | Source | Notes |
| --- | --- | --- | --- |
| Theme/density/radius/motion/accent | Real | `app/src/App.tsx`, `AppearanceWorkspace.tsx` | Calls `handleGeneralSettingsChange`. |
| Transparency/font scale sliders | Real | `app/src/App.tsx`, `AppearanceWorkspace.tsx` | Persist through config. |
| Language/reasoning/preflight | Real | `app/src/App.tsx`, `AppearanceWorkspace.tsx` | Persist through config. |
| Open Settings | Real | `app/src/App.tsx`, `AppearanceWorkspace.tsx` | Opens Settings workspace. |
| Preview send button | Read-only | `AppearanceWorkspace.tsx` | Disabled with `Preview only`. |

## Component Playground

| Action | Status | Source | Notes |
| --- | --- | --- | --- |
| Open Appearance | Real | `app/src/App.tsx`, `ComponentPlaygroundWorkspace.tsx` | Opens Appearance workspace. |
| Open Skills | Real | `app/src/App.tsx`, `ComponentPlaygroundWorkspace.tsx` | Opens Skills workspace. |
| Component controls | Read-only | `ComponentPlaygroundWorkspace.tsx` | Intended as validation surface, not business workflow. |

## Recommended Execution Order From This Audit

1. Fix or remove P0 placeholders:
   - IM bridge test.
   - Computer Use recheck.
   - Open logs.
   - Open data folder.
2. Normalize disabled/read-only treatment:
   - Agent add/toggle.
   - Skills open folder.
   - Preview-only controls.
3. Live-QA MCP and provider actions through Tauri.
4. Recheck Session copy/detail controls and add missing copy feedback.
5. Update tests and visual regression after each UI behavior change.
