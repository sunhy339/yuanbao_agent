# Frontend V2 Bug List

This list tracks visual and interaction bugs found during the desktop frontend upgrade. Re-check every open item after the main V2 upgrade pass before calling the frontend complete.

## Open

### FEV2-BUG-005: MCP workspace contains mojibake in user-facing copy

- Status: Open
- Priority: P1
- Area: MCP Workspace / Chinese localization
- Observed: The MCP workspace passes runtime and desktop E2E flows, but several visible labels and descriptions render as mojibake instead of readable Chinese.
- Expected: All user-facing MCP workspace copy should render as valid readable Simplified Chinese while technical identifiers such as MCP, stdio, SSE, HTTP, command, args, env, and tool names remain unchanged.
- Acceptance check:
  - MCP workspace title, metrics, form labels, buttons, empty states, inspector labels, and error banner are readable.
  - `npm run e2e:desktop:mcp` still passes.
  - `npm test` covers the corrected accessible labels where practical.
  - No runtime protocol field names are translated.

### FEV2-BUG-001: Native scrollbar looks visually unfinished

- Status: Fixed
- Priority: P2
- Area: Desktop shell / scroll containers
- Observed: Some scrollbars render as very thick native gray tracks with arrow buttons. They visually clash with the V2 workbench style and look especially awkward in narrow scroll regions.
- Expected: Desktop scrollbars should use a restrained V2 style: thinner track, softer thumb, no oversized arrow-button appearance where CSS can control it, and colors that work in both dark and light themes.
- Acceptance check:
  - Main workspace scroll, right rail scroll, trace/detail scroll, command output scroll, and modal scroll all look intentional in desktop screenshots.
  - Dark and light visual regression screenshots show no oversized native scrollbar dominating the UI.
  - Keyboard and mouse wheel scrolling still work normally.
- Fix notes:
  - Added V2-scoped thin scrollbar styling in `app/src/ui/v2/theme/tokens.css`.
  - Verified by `npm.cmd run visual:regression`: 18 desktop screenshots, 0 automated findings.

### FEV2-BUG-002: Trace feed exposes low-level noisy events instead of meaningful operations

- Status: Fixed
- Priority: P1
- Area: Session Workbench / Trace feed
- Observed: The trace feed shows low-level events such as `assistant.token`, `provider.request`, `task.started`, and `task.orphaned` as full cards, often with raw JSON payloads. This creates a long noisy stream and makes the UI feel like an internal debug log rather than an operator-facing activity panel.
- Expected: The user-facing trace feed should show important operations only, such as tool calls, command execution, patch/result summaries, approval gates, task phase changes, and meaningful errors. Token deltas and provider request internals should be hidden, aggregated, or moved behind an explicit debug/diagnostics mode.
- Acceptance check:
  - A normal session no longer creates one visible card per `assistant.token`.
  - Raw provider payloads and token JSON are not shown in the default Session Workbench.
  - Tool calls, command results, approvals, patches, and task outcomes remain visible and easy to inspect.
  - Debug-level trace data is still accessible only through a deliberate diagnostics affordance, if needed.
- Fix notes:
  - Added user-facing trace filtering and raw JSON suppression in `app/src/ui/workbench/workspaces/session/SessionWorkspace.tsx`.
  - Updated `app/src/e2e/tauriProviderFlow.ts` so persisted trace data is still required, while low-level trace cards must not leak into the default UI.
  - Added regression coverage in `app/src/ui/workbench/workspaces/session/SessionWorkspace.test.tsx`.

### FEV2-BUG-003: Trace card visual hierarchy is too heavy for routine runtime events

- Status: Fixed
- Priority: P2
- Area: Session Workbench / runtime event cards
- Observed: Trace cards use large white blocks, a strong left accent, repeated labels, and large spacing. When many trace events appear, the page becomes visually noisy and hard to scan.
- Expected: Routine runtime events should render as compact rows or grouped summaries. Only important actionable states, such as failed commands or pending approvals, should use full-card emphasis.
- Acceptance check:
  - Trace/feed rows are scannable at desktop density.
  - Actionable events have clear emphasis without every trace item looking equally important.
  - The feed remains readable with 20+ runtime events.
- Fix notes:
  - The biggest visual noise source is reduced because low-level trace events are no longer rendered as full cards by default.
  - Remaining user-visible diagnostics now render as compact rows in `app/src/ui/workbench/workspaces/session/SessionWorkspace.tsx` and `app/src/ui/workbench/workspaces/session/session.css`.
  - The right runtime column now keeps empty lanes compact when the composer is visible, so the desktop Session workbench no longer cuts off the Diagnostics lane in the default screenshots.

### FEV2-BUG-004: Skill controls imply unsupported enable/disable behavior

- Status: Fixed
- Priority: P2
- Area: Settings / Agent Skills
- Observed: Installed skills appeared with checkbox-style controls and disabled custom-skill creation, making the UI look unfinished and implying runtime enable/disable behavior that the current backend schema does not expose.
- Expected: Runtime-backed actions should remain available, while unsupported skill registry operations should not look like active controls.
- Acceptance check:
  - Settings renders installed skills as read-only available presets, not toggles.
  - Agent Skills no longer shows disabled custom-skill authoring as a primary action.
  - Refresh skills, MCP management, and runtime settings remain clickable where wired.
- Fix notes:
  - Updated `app/src/ui/workbench/workspaces/settings/SettingsWorkspace.tsx` and `settings.css` to render skills as read-only available presets.
  - Removed the disabled custom-skill action from `app/src/ui/workbench/workspaces/skills/SkillsWorkspace.tsx`.
  - Added a Settings regression test for read-only skill presets.

## Review Gate

Final review status: Completed on 2026-05-02.

- Rechecked the open list: all tracked frontend V2 bugs remain fixed.
- `npm.cmd run visual:regression` now covers 40 screenshots across dark/light, 1440px desktop, 1024px narrow desktop, and a Chat Long Text stress page with 0 automated findings.
- `npm.cmd run e2e:desktop:ui` passed.
- `npm.cmd run e2e:desktop:mcp` passed.
- `npm.cmd run e2e:desktop:recovery` passed and verifies recovered task status visibility in the Session Workbench.
- No reopened bugs or new frontend V2 bugs were found during the final gate pass.

Before closing the frontend V2 upgrade:

1. Reproduce or inspect each reopened bug in the local desktop client.
2. Confirm the fix in both dark and light desktop screenshots.
3. Update each item to Fixed with the commit or file references that resolved it.
