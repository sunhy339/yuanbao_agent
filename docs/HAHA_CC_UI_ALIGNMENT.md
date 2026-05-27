# haha-cc UI Alignment

This note tracks the current UI direction for the local agent workbench.

## Principles

- The main chat is the primary work log. It must show natural assistant text, command/tool activity, approvals, failures, recovery, changed files, verification commands, and diff previews.
- The right workspace is file-only. It is for browsing and previewing project files, not for command history, Git, terminal, diagnostics, or review state.
- Diff/review information belongs in the chat stream as compact change summaries and expandable diff previews.
- Avoid stacked dashboard cards. Prefer simple timeline rows, small metadata chips, and lightweight expandable blocks.
- Chat and file workspace must scroll independently. The divider should be thin and unobtrusive.
- Long-running work must keep showing meaningful status, such as the current task phase, current command/tool, approval wait, or latest failure.

## Implemented In This Pass

- Rebuilt the session layout around a chat column plus a file-only right pane.
- Removed the old right-side tool tabs from the active workspace surface.
- Changed command/background job visibility so non-background command facts are surfaced in chat.
- Kept completed `run_command` calls in the main chat worklog, including single-command runs such as `python main.py`.
- Simplified the work summary from card piles into a compact summary line with changed files, recent commands, verification count, and diff count.
- Added explicit verification command rows to the chat summary, including command and status.
- Added a haha-cc-style current-turn change block in the main chat: file count, total additions/deletions, changed-file rows, and expandable diff previews.
- Switched message timestamps to compact time-only display to avoid the floating time column problem.
- Tightened the file workspace layout so preview and tree scroll independently.
- Updated session tests to enforce the new file-only right pane and chat-visible command/verification facts.

## Still Needed

- Backend runtime events should emit higher-quality progress deltas during long model/tool waits, so the chat does not sit on a generic thinking state.
- The file preview should get a final visual pass for very narrow panes and fullscreen mode.
- A desktop smoke test should verify the real Tauri shell after future layout changes.
