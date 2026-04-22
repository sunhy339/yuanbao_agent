# Desktop Workbench UI Design

Date: 2026-04-23
Project: `yuanbao_agent`
Status: Pending user review

## Summary

This document defines the next UI direction for the Tauri desktop app. The current frontend is a single oversized React view that mixes session creation, active conversation, providers, permissions, trace, approvals, and task details into one page. The new design replaces that structure with a desktop-workbench shell:

- a fixed global sidebar
- a top tab strip for opened pages and opened sessions
- one focused content view at a time
- a fixed bottom composer only for conversation-oriented views

The visual language should move toward a restrained "Republic-era desk terminal" feeling: warm paper surfaces, wood-brown accents, ink-dark text, rounded geometry, and minimal chrome. The design should borrow structural behavior from the provided references without copying their exact styling.

## Goals

1. Stop stacking unrelated content in a single scrolling page.
2. Make the app feel like a desktop workspace instead of a dashboard.
3. Separate global navigation, opened work areas, and active content.
4. Make `New session`, `Scheduled`, and `Settings` first-class workspaces.
5. Turn `Settings` into a dedicated workspace with a stable internal navigation model.
6. Preserve room for existing runtime features such as approvals, patch review, trace, and task controls inside the conversation workspace.

## Non-Goals

1. This design does not change backend runtime semantics.
2. This design does not redefine provider/config persistence formats.
3. This design does not introduce dark mode as a required feature.
4. This design does not attempt a full visual rewrite of every subpanel in one pass; it defines the shell and the first target views.

## Product Direction

### Visual Thesis

The application should feel like a modern coding workbench placed on a warm archival desk: paper-toned surfaces, lacquer-brown actions, soft brass hints, dark ink typography, and broad areas of calm whitespace. The interface should be elegant and quiet rather than nostalgic or theatrical.

### Content Plan

The shell should always communicate four layers clearly:

1. who and where you are: brand, workspace context
2. what spaces exist globally: sidebar navigation
3. what spaces are currently open: top tabs
4. what task you are doing right now: single focused main pane

### Interaction Thesis

The interface should feel like a document desk rather than a control center:

1. opened pages become tabs instead of replacing the whole app state invisibly
2. settings sections switch cleanly in place without long-page stacking
3. the conversation composer stays anchored and separate from content

## Information Architecture

## Global Shell

The app root becomes a persistent shell with four regions:

1. `GlobalSidebar`
2. `WorkspaceTabs`
3. `WorkspaceContent`
4. `ComposerDock`

`ComposerDock` is conditionally visible. It appears only in conversation-oriented workspaces such as `New session` and an opened session tab. It is hidden in `Scheduled` and `Settings`.

## Global Sidebar

The sidebar is fixed and always visible.

Top area:

- brand mark and product name
- optional repo/account affordance

Primary global actions:

- `新会话`
- `调度`

Middle utility area:

- session search
- session history list

Bottom utility area:

- `设置`

Sidebar responsibilities:

1. present global destinations
2. expose session history
3. open destinations into the top tab strip
4. never render the actual workspace content inline

The sidebar is not the place where content switches are fully displayed. Clicking a sidebar destination opens or activates a top tab.

## Top Tab Strip

The tab strip represents opened work areas.

System tabs:

- `新会话`
- `调度`
- `设置`

Session tabs:

- each opened session record

Rules:

1. system tabs can be activated repeatedly and should remain stable
2. session tabs can be opened from the sidebar session list
3. session tabs may be closable
4. only one tab is active at a time
5. switching tabs replaces the entire main content pane

This is the key behavior that prevents content from being stacked on one page.

## Main Workspaces

The main content pane supports four primary workspace types in the first design phase:

1. `new-session`
2. `session`
3. `scheduled`
4. `settings`

Each workspace owns the entire visible content pane.

## Workspace Specifications

## New Session Workspace

Purpose:

- provide a calm starting point for a fresh coding session
- allow workspace selection / attachment
- keep the composer dock visually independent

Layout:

- centered empty-state / welcome composition
- minimal orientation copy
- large negative space
- fixed composer dock at the bottom

Composer behavior:

- multiline prompt input
- model selector
- action button
- optional attachment/workspace affordances

Constraints:

1. no settings content here
2. no scheduled-task content here
3. no trace/tool/approval panels visible until an actual session is opened or created

## Session Workspace

Purpose:

- show one active conversation and its operational context

Layout:

- conversation header: session title, status, last updated, lightweight meta
- main stream: user / assistant messages
- supporting operational blocks inserted with restraint:
  - task progress
  - approvals
  - patch review
  - trace / tool timeline
- fixed composer dock at the bottom

Behavior:

1. the conversation stream remains the primary surface
2. supporting operational data appears as secondary sections inside the session workspace only
3. these sections should not leak into `New session`, `Scheduled`, or `Settings`

Design note:

The existing rich runtime functionality from the current `App.tsx` should be preserved, but reorganized into this workspace instead of being globally visible.

## Scheduled Workspace

Purpose:

- manage recurring tasks without mixing them into the conversation page

Layout:

- page title and short explanation
- summary metrics row
- scheduled task list
- expandable execution log region for a selected task

Behavior:

1. no composer dock
2. no conversation stream
3. task statistics and execution history remain in this dedicated workspace

## Settings Workspace

Purpose:

- provide a dedicated configuration environment separated from conversation and scheduling

Shell:

- left-side internal settings navigation
- right-side single-detail content panel
- no bottom composer

Internal sections:

1. `服务商`
2. `权限`
3. `通用`
4. `IM 接入`
5. `Agents`
6. `技能`
7. `Computer Use`
8. `关于`

The settings workspace should open as one top-level tab named `设置`. The left settings navigation changes only the content inside that workspace and does not open more top tabs.

## Settings Section Specifications

## 服务商

Purpose:

- manage model/provider endpoints and activation state

Layout:

- title and one-sentence description
- top-right `添加服务商` action
- vertical provider list cards below

Provider list item:

- status dot
- provider name
- endpoint / model description
- optional activation chip
- active provider has stronger border emphasis

Add provider modal:

- centered overlay modal
- preset tabs: `DeepSeek`, `Zhipu GLM`, `Kimi`, `MiniMax`, `Custom`
- fields:
  - name
  - note
  - base URL
  - API key
  - model mapping
  - JSON settings block
- actions:
  - test connection
  - cancel
  - add

Behavior:

1. modal overlays the settings workspace rather than opening a new tab
2. provider cards remain simple and list-like, not dashboard tiles

## 权限

Purpose:

- choose how tool execution approval works

Layout:

- title and helper copy
- vertical option list of permission modes

Options shown in the approved reference:

- 访问权限
- 接受编辑
- 计划模式
- 跳过全部

Interaction:

- one selected item at a time
- active option gets a stronger border and positive selection mark
- dangerous modes still use the same geometry but include clearer warning tone in text/accent

## 通用

Purpose:

- host general app preferences with calm form grouping

Initial groups:

- 配色主题
- 语言
- 推理强度
- WebFetch 预检

Layout:

- title and explanation for each group
- pill toggles / segmented controls for binary or few-choice selections
- one-column vertical composition with generous spacing

Behavior:

1. keep controls broad and readable
2. avoid dense settings tables

## IM 接入

Purpose:

- configure external IM adapters

Initial design guidance:

- use the same title + explanation + main content structure
- if empty, show a restrained empty state instead of filler cards

## Agents

Purpose:

- configure agent-related behavior and inventory

Initial design guidance:

- use the same settings content skeleton
- the first implementation may ship as a structurally complete but low-density section until its final controls are ready

## 技能

Purpose:

- show installed skills

Layout:

- title and explanation
- single large empty-state container when there are no installed skills

Behavior:

1. empty state should be informative and spacious
2. do not force grid cards when there is no data

## Computer Use

Purpose:

- show readiness, authorization, and app integration state

Initial design guidance:

- keep consistency with the new settings layout
- use stacked status blocks rather than squeezing technical details into tables

## 关于

Purpose:

- show build/app information in a low-noise, non-prominent footer section

Placement:

- anchored at the bottom of the left settings navigation

## State Model

The UI should move from "many booleans inside one page" to an explicit workspace model.

Recommended top-level state concepts:

1. `openTabs`
2. `activeTabId`
3. `sidebarSelectionIntent`
4. `settingsSection`
5. `composerVisibility`

The shell state should determine what view is rendered. The content should not depend on dozens of unrelated conditional blocks sharing the same root markup.

## Component Boundaries

The current `App.tsx` should be decomposed into bounded components with clear ownership.

Recommended top-level component shape:

- `AppShell`
- `GlobalSidebar`
- `WorkspaceTabs`
- `WorkspaceFrame`
- `ComposerDock`

Workspace components:

- `NewSessionWorkspace`
- `SessionWorkspace`
- `ScheduledWorkspace`
- `SettingsWorkspace`

Settings subcomponents:

- `SettingsSidebar`
- `ProviderSettingsView`
- `PermissionSettingsView`
- `GeneralSettingsView`
- `ImAdaptersSettingsView`
- `AgentsSettingsView`
- `SkillsSettingsView`
- `ComputerUseSettingsView`
- `AboutSettingsView`
- `ProviderModal`

Session subcomponents should further split as needed:

- `SessionHeader`
- `ConversationStream`
- `TaskProgressPanel`
- `ApprovalPanel`
- `PatchPanel`
- `TracePanel`

These boundaries are intended to reduce cognitive load and prevent unrelated UI changes from colliding.

## Data Flow

Sidebar flow:

1. user clicks a global destination or session
2. shell resolves an existing open tab or opens a new one
3. active tab changes
4. content pane renders the matching workspace

Settings flow:

1. user activates `设置` in sidebar or top tabs
2. settings workspace opens
3. left internal settings nav switches only the right settings content pane
4. provider modal opens as an overlay on top of settings when needed

Session flow:

1. user starts a new session or opens an existing session
2. tab strip activates that session tab
3. session workspace reads session/task/runtime data and renders only those conversation-specific sections
4. composer dock submits messages in place

## Error Handling

The UI should handle missing or empty data gracefully within each workspace.

Requirements:

1. every workspace must have an intentional empty state
2. every modal/form should have a visible error area for failed actions
3. failures in one workspace should not degrade the shell itself
4. settings sections without data should remain calm, not collapse layout
5. if runtime/session data is unavailable, `New session` and `Settings` must still remain usable

## Accessibility and Usability

1. selected states must not depend on color alone
2. tabs, sidebar items, and settings options must have clear focus states
3. the composer must remain keyboard-friendly
4. dialog actions in the provider modal must remain visible near the bottom edge
5. long JSON/config blocks must scroll inside their own area rather than stretching the entire modal excessively

## Visual System

Color direction:

- paper ivory background
- warm wood / copper actions and highlights
- dark ink body text
- very restrained green for healthy/active states
- muted terracotta or brown-red for destructive emphasis

Typography direction:

- stronger editorial headings
- highly readable body text
- avoid futuristic or overly geometric feel

Surface direction:

- rounded corners that are more pronounced than the current UI
- very light borders
- subtle paper-like depth
- minimal shadow, no glassmorphism

Anti-goals:

1. no purple accents
2. no cyber or neon visuals
3. no dashboard-card mosaic
4. no stacked mixed-content mega page

## Technical Constraints

1. existing runtime APIs should remain usable during the redesign
2. the shell refactor should avoid changing backend contracts unless strictly necessary
3. the initial implementation should prefer structure first, then visual refinement
4. existing mock-browser fallback should continue to work after the UI split

## Rollout Strategy

Recommended rollout order:

1. build the app shell and tab model
2. move `New session` into its own workspace
3. move session detail into its own workspace
4. build `Settings` workspace and ship the approved sections
5. move `Scheduled` into its own workspace
6. polish visual system and motion

This order reduces risk because it creates the new navigation spine before rewriting every detail panel.

## Testing Strategy

Design-level acceptance checks:

1. opening `新会话`, `调度`, and `设置` does not stack content in one page
2. session tabs open independently and switch correctly
3. the composer is visible only in `新会话` and `会话`
4. settings internal navigation swaps one right-side view at a time
5. provider modal overlays correctly and remains scroll-safe
6. empty states exist for skills and other sparse sections

Implementation-level tests should include:

1. tab activation and deduplication behavior
2. settings section switching behavior
3. composer visibility rules
4. provider modal open/close flow
5. session tab close behavior

## Open Decisions Resolved in This Spec

1. `新会话 / 调度 / 设置` belong in the sidebar as global entry points.
2. opened pages and opened sessions appear in the top tab strip.
3. `设置` is a single top-level workspace with internal left navigation.
4. the settings page should follow the approved structure shown in the provided screenshots.
5. the visual style should be "Republic-era desk terminal", not a literal clone of the references.

## Scope Check

This spec is intentionally limited to shell architecture, workspace boundaries, settings-page structure, and the guiding visual system. It is suitable for a single implementation plan, though the implementation should be delivered in multiple phases.
