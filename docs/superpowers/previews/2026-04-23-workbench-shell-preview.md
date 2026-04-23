# Workbench Shell Visual Preview

Date: 2026-04-23
Owner: Worker A

## Scope

Preview target is the Tauri/React workbench shell:

- `app/src/ui/workbench/AppShell.tsx`
- `app/src/ui/workbench/GlobalSidebar.tsx`
- `app/src/ui/workbench/WorkspaceTabs.tsx`
- `app/src/ui/workbench/WorkspaceFrame.tsx`
- `app/src/ui/workbench/ComposerDock.tsx`
- `app/src/ui/workbench/workspaces/NewSessionWorkspace.tsx`
- `app/src/ui/workbench/workspaces/newSession.css`
- `app/src/ui/workbench/workbench.css`

## Visual Notes

The shell now uses a warmer paper-and-wood palette, ink text, heavier rounded terminal surfaces, and visible sidebar labels matching the requested structure: 新会话, 会话, 调度, 设置. The new-session workspace presents a desk-like paper surface with a dark old-office terminal panel.

## Generated Screenshot

Actual preview captured with Chrome headless from the Vite dev server:

```text
D:\py\yuanbao_agent\docs\superpowers\previews\workbench-shell-2026-04-23.png
```

## Screenshot Instructions

From `D:\py\yuanbao_agent\app`:

```powershell
npm run dev
```

Open the local Vite URL in a browser, capture the workbench home/new-session screen, then stop the dev server with `Ctrl+C`.

Suggested output path if capturing manually:

```text
D:\py\yuanbao_agent\docs\superpowers\previews\workbench-shell-2026-04-23.png
```
