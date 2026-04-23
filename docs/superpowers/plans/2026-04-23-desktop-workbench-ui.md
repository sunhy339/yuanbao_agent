# Desktop Workbench UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the React/Tauri frontend into a desktop workbench shell with global sidebar navigation, opened-workspace tabs, focused content views, a conditional bottom composer, and the approved settings workspace.

**Architecture:** Keep runtime integration in `App.tsx` during the first pass, but move navigation state, presentational shell, workspaces, and settings surfaces into focused files under `app/src/ui/workbench/`. Use pure functions for tab and settings selection behavior so the shell can be tested before the visual rewrite. Preserve current backend contracts and mock-browser fallback behavior.

**Tech Stack:** React 18, TypeScript, Vite, Tauri 2, CSS custom properties, Vitest, React Testing Library.

---

## Design Decisions

- Color palette: paper ivory background (`#f7f2e8`), aged paper panels (`#fffaf0`), lacquer wood primary (`#9b4f32`), copper border (`#d8b8a6`), ink text (`#1f1a17`), muted text (`#7b6a5f`), restrained success green (`#2f9b64`).
- Typography: use `"Songti SC"`, `"Noto Serif SC"`, `"Source Han Serif SC"` for headings when available; use `"Microsoft YaHei"`, `"PingFang SC"` for body; use `"JetBrains Mono"`, `"Consolas"` for code/config blocks.
- Spacing system: 4px base unit, with 8px, 12px, 16px, 24px, 32px, 48px as standard layout steps.
- Border-radius strategy: 18px for cards and controls, 24px for composer and modal surfaces, 999px for segmented controls.
- Shadow hierarchy: one light workbench elevation only, `0 18px 50px rgba(63, 38, 24, 0.08)`, with borders carrying most hierarchy.
- Motion style: 140ms for hover/focus changes, 180ms for tab/content transitions, `cubic-bezier(.2,.8,.2,1)`, disabled when `prefers-reduced-motion: reduce`.

---

## File Structure

Create:

- `app/src/ui/workbench/types.ts`: shared UI-only types for workspace tabs, settings sections, and composer visibility.
- `app/src/ui/workbench/tabModel.ts`: pure functions for opening, activating, and closing tabs.
- `app/src/ui/workbench/tabModel.test.ts`: unit tests for tab deduplication, activation, and close behavior.
- `app/src/ui/workbench/settingsModel.ts`: pure settings-section metadata and selection helpers.
- `app/src/ui/workbench/settingsModel.test.ts`: unit tests for settings navigation.
- `app/src/ui/workbench/AppShell.tsx`: overall shell layout component.
- `app/src/ui/workbench/GlobalSidebar.tsx`: brand, global actions, search, session list, settings button.
- `app/src/ui/workbench/WorkspaceTabs.tsx`: top tab strip.
- `app/src/ui/workbench/WorkspaceFrame.tsx`: active workspace wrapper with content scroll behavior.
- `app/src/ui/workbench/ComposerDock.tsx`: fixed bottom composer UI.
- `app/src/ui/workbench/workbench.css`: new visual system and workbench layout CSS.
- `app/src/ui/workbench/workspaces/NewSessionWorkspace.tsx`: new-session welcome workspace.
- `app/src/ui/workbench/workspaces/SessionWorkspace.tsx`: active conversation workspace.
- `app/src/ui/workbench/workspaces/ScheduledWorkspace.tsx`: scheduled-task workspace.
- `app/src/ui/workbench/workspaces/settings/SettingsWorkspace.tsx`: settings workspace shell.
- `app/src/ui/workbench/workspaces/settings/SettingsSidebar.tsx`: settings internal navigation.
- `app/src/ui/workbench/workspaces/settings/ProviderSettingsView.tsx`: provider list and add-provider entry point.
- `app/src/ui/workbench/workspaces/settings/ProviderModal.tsx`: add-provider modal.
- `app/src/ui/workbench/workspaces/settings/PermissionSettingsView.tsx`: permission mode selection view.
- `app/src/ui/workbench/workspaces/settings/GeneralSettingsView.tsx`: theme, language, reasoning, WebFetch settings view.
- `app/src/ui/workbench/workspaces/settings/SkillsSettingsView.tsx`: installed-skills empty/list view.
- `app/src/ui/workbench/workspaces/settings/SimpleSettingsView.tsx`: low-density settings views for IM, Agents, Computer Use, About.
- `app/src/ui/workbench/workspaces/settings/SettingsWorkspace.test.tsx`: React tests for settings section switching and modal visibility.
- `app/src/ui/workbench/AppShell.test.tsx`: React tests for tab activation and composer visibility.

Modify:

- `app/package.json`: add test dependencies and scripts.
- `app/tsconfig.json`: include Vitest globals.
- `app/src/styles.css`: import workbench CSS and keep only reusable legacy styles that still apply.
- `app/src/App.tsx`: keep runtime orchestration, but render the new workbench shell and route active tabs to workspace components.

Do not modify:

- `shared/src/*`: no shared runtime contract changes are required.
- `runtime/*`: backend behavior is outside this UI plan.

---

### Task 1: Add Test Tooling And Pure Tab Model

**Files:**

- Modify: `app/package.json`
- Modify: `app/tsconfig.json`
- Create: `app/src/ui/workbench/types.ts`
- Create: `app/src/ui/workbench/tabModel.ts`
- Test: `app/src/ui/workbench/tabModel.test.ts`

- [ ] **Step 1: Add test dependencies**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm install -D vitest jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom
```

Expected: npm installs the packages and updates `package-lock.json`.

- [ ] **Step 2: Modify package scripts**

In `app/package.json`, change the `scripts` block to include `test`:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "test:watch": "vitest",
    "tauri:dev": "tauri dev",
    "tauri:build": "tauri build"
  }
}
```

- [ ] **Step 3: Modify TypeScript test globals**

In `app/tsconfig.json`, update `compilerOptions.types`:

```json
"types": ["node", "vitest/globals", "@testing-library/jest-dom"]
```

- [ ] **Step 4: Write the failing tab model test**

Create `app/src/ui/workbench/tabModel.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { WorkbenchTab } from "./types";
import { closeTab, openSessionTab, openSystemTab } from "./tabModel";

describe("tabModel", () => {
  it("deduplicates system tabs and activates the requested tab", () => {
    const initial: WorkbenchTab[] = [{ id: "system:new-session", kind: "new-session", title: "新会话" }];

    const result = openSystemTab(initial, "settings");
    const duplicate = openSystemTab(result.tabs, "settings");

    expect(result.activeTabId).toBe("system:settings");
    expect(duplicate.activeTabId).toBe("system:settings");
    expect(duplicate.tabs.filter((tab) => tab.id === "system:settings")).toHaveLength(1);
  });

  it("deduplicates session tabs by session id", () => {
    const initial: WorkbenchTab[] = [{ id: "system:new-session", kind: "new-session", title: "新会话" }];

    const first = openSessionTab(initial, { id: "sess_1", title: "修复测试失败" });
    const second = openSessionTab(first.tabs, { id: "sess_1", title: "修复测试失败" });

    expect(second.activeTabId).toBe("session:sess_1");
    expect(second.tabs.filter((tab) => tab.id === "session:sess_1")).toHaveLength(1);
  });

  it("activates a neighbor when closing the active session tab", () => {
    const tabs: WorkbenchTab[] = [
      { id: "system:new-session", kind: "new-session", title: "新会话" },
      { id: "session:sess_1", kind: "session", title: "会话 A", sessionId: "sess_1", closable: true },
      { id: "system:settings", kind: "settings", title: "设置" },
    ];

    const result = closeTab(tabs, "session:sess_1", "session:sess_1");

    expect(result.tabs.map((tab) => tab.id)).toEqual(["system:new-session", "system:settings"]);
    expect(result.activeTabId).toBe("system:settings");
  });

  it("does not close system tabs", () => {
    const tabs: WorkbenchTab[] = [{ id: "system:settings", kind: "settings", title: "设置" }];

    const result = closeTab(tabs, "system:settings", "system:settings");

    expect(result.tabs).toEqual(tabs);
    expect(result.activeTabId).toBe("system:settings");
  });
});
```

- [ ] **Step 5: Run test to verify it fails**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test -- src/ui/workbench/tabModel.test.ts
```

Expected: FAIL because `types.ts` and `tabModel.ts` do not exist.

- [ ] **Step 6: Create shared workbench types**

Create `app/src/ui/workbench/types.ts`:

```ts
export type SystemWorkspaceKind = "new-session" | "scheduled" | "settings";

export type WorkbenchTab =
  | {
      id: `system:${SystemWorkspaceKind}`;
      kind: SystemWorkspaceKind;
      title: string;
      closable?: false;
    }
  | {
      id: `session:${string}`;
      kind: "session";
      title: string;
      sessionId: string;
      closable: true;
    };

export type SettingsSection =
  | "providers"
  | "permissions"
  | "general"
  | "im-adapters"
  | "agents"
  | "skills"
  | "computer-use"
  | "about";

export interface WorkbenchTabResult {
  tabs: WorkbenchTab[];
  activeTabId: WorkbenchTab["id"];
}
```

- [ ] **Step 7: Implement the tab model**

Create `app/src/ui/workbench/tabModel.ts`:

```ts
import type { SystemWorkspaceKind, WorkbenchTab, WorkbenchTabResult } from "./types";

const SYSTEM_TITLES: Record<SystemWorkspaceKind, string> = {
  "new-session": "新会话",
  scheduled: "调度",
  settings: "设置",
};

export function getInitialTabs(): WorkbenchTab[] {
  return [{ id: "system:new-session", kind: "new-session", title: SYSTEM_TITLES["new-session"] }];
}

export function openSystemTab(tabs: WorkbenchTab[], kind: SystemWorkspaceKind): WorkbenchTabResult {
  const id = `system:${kind}` as const;
  if (tabs.some((tab) => tab.id === id)) {
    return { tabs, activeTabId: id };
  }
  return {
    tabs: [...tabs, { id, kind, title: SYSTEM_TITLES[kind] }],
    activeTabId: id,
  };
}

export function openSessionTab(
  tabs: WorkbenchTab[],
  session: { id: string; title: string },
): WorkbenchTabResult {
  const id = `session:${session.id}` as const;
  if (tabs.some((tab) => tab.id === id)) {
    return { tabs, activeTabId: id };
  }
  return {
    tabs: [...tabs, { id, kind: "session", title: session.title || "未命名会话", sessionId: session.id, closable: true }],
    activeTabId: id,
  };
}

export function closeTab(
  tabs: WorkbenchTab[],
  tabId: WorkbenchTab["id"],
  activeTabId: WorkbenchTab["id"],
): WorkbenchTabResult {
  const index = tabs.findIndex((tab) => tab.id === tabId);
  const target = tabs[index];
  if (!target || !target.closable) {
    return { tabs, activeTabId };
  }

  const nextTabs = tabs.filter((tab) => tab.id !== tabId);
  if (activeTabId !== tabId) {
    return { tabs: nextTabs, activeTabId };
  }

  const neighbor = nextTabs[index] ?? nextTabs[index - 1] ?? nextTabs[0] ?? getInitialTabs()[0];
  return {
    tabs: nextTabs.length ? nextTabs : getInitialTabs(),
    activeTabId: neighbor.id,
  };
}
```

- [ ] **Step 8: Run test to verify it passes**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test -- src/ui/workbench/tabModel.test.ts
```

Expected: PASS.

- [ ] **Step 9: Run typecheck**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run typecheck
```

Expected: PASS.

- [ ] **Step 10: Commit**

```powershell
cd D:\py\yuanbao_agent
git add app/package.json app/package-lock.json app/tsconfig.json app/src/ui/workbench/types.ts app/src/ui/workbench/tabModel.ts app/src/ui/workbench/tabModel.test.ts
git commit -m "test: add workbench tab model"
```

---

### Task 2: Build The App Shell Components

**Files:**

- Create: `app/src/ui/workbench/AppShell.tsx`
- Create: `app/src/ui/workbench/GlobalSidebar.tsx`
- Create: `app/src/ui/workbench/WorkspaceTabs.tsx`
- Create: `app/src/ui/workbench/WorkspaceFrame.tsx`
- Create: `app/src/ui/workbench/ComposerDock.tsx`
- Create: `app/src/ui/workbench/AppShell.test.tsx`
- Create: `app/src/ui/workbench/workbench.css`
- Modify: `app/src/styles.css`

- [ ] **Step 1: Write the failing shell test**

Create `app/src/ui/workbench/AppShell.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";
import { getInitialTabs } from "./tabModel";
import type { WorkbenchTab } from "./types";

const sessions = [
  {
    id: "sess_1",
    title: "修复测试失败",
    summary: "Inspect tests and patch the issue.",
    status: "active" as const,
    workspaceId: "ws_1",
    createdAt: 1,
    updatedAt: 2,
  },
];

function renderShell(options: { activeTab?: WorkbenchTab["id"] } = {}) {
  const tabs = getInitialTabs();
  const activeTabId = options.activeTab ?? "system:new-session";
  const handlers = {
    onOpenSystemTab: vi.fn(),
    onOpenSessionTab: vi.fn(),
    onActivateTab: vi.fn(),
    onCloseTab: vi.fn(),
    onSubmitPrompt: vi.fn(),
  };

  render(
    <AppShell
      tabs={tabs}
      activeTabId={activeTabId}
      sessions={sessions}
      activeSessionId={null}
      workspaceName="yuanbao_agent"
      composerVisible={activeTabId === "system:new-session"}
      promptValue=""
      onPromptChange={vi.fn()}
      disabled={false}
      providerLabel="MiniMax-M2.7-highspeed"
      cwdLabel="D:/py/yuanbao_agent"
      {...handlers}
    >
      <section aria-label="workspace content">Content</section>
    </AppShell>,
  );

  return handlers;
}

describe("AppShell", () => {
  it("renders sidebar, tabs, focused content, and composer for new session", () => {
    renderShell();

    expect(screen.getByRole("button", { name: "新会话" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "调度" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "设置" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "新会话" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("workspace content")).toBeInTheDocument();
    expect(screen.getByLabelText("输入任务")).toBeInTheDocument();
  });

  it("hides composer when composerVisible is false", () => {
    const tabs: WorkbenchTab[] = [{ id: "system:settings", kind: "settings", title: "设置" }];
    render(
      <AppShell
        tabs={tabs}
        activeTabId="system:settings"
        sessions={sessions}
        activeSessionId={null}
        workspaceName="yuanbao_agent"
        composerVisible={false}
        promptValue=""
        onPromptChange={vi.fn()}
        onOpenSystemTab={vi.fn()}
        onOpenSessionTab={vi.fn()}
        onActivateTab={vi.fn()}
        onCloseTab={vi.fn()}
        onSubmitPrompt={vi.fn()}
        disabled={false}
        providerLabel="MiniMax-M2.7-highspeed"
        cwdLabel="D:/py/yuanbao_agent"
      >
        <section>Settings</section>
      </AppShell>,
    );

    expect(screen.queryByLabelText("输入任务")).not.toBeInTheDocument();
  });

  it("calls open handlers from sidebar", async () => {
    const handlers = renderShell();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "调度" }));
    await user.click(screen.getByRole("button", { name: "设置" }));
    await user.click(screen.getByRole("button", { name: /修复测试失败/ }));

    expect(handlers.onOpenSystemTab).toHaveBeenCalledWith("scheduled");
    expect(handlers.onOpenSystemTab).toHaveBeenCalledWith("settings");
    expect(handlers.onOpenSessionTab).toHaveBeenCalledWith(sessions[0]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test -- src/ui/workbench/AppShell.test.tsx
```

Expected: FAIL because shell components do not exist.

- [ ] **Step 3: Create `GlobalSidebar.tsx`**

```tsx
import type { SessionRecord } from "@shared";
import type { SystemWorkspaceKind } from "./types";

interface GlobalSidebarProps {
  sessions: SessionRecord[];
  activeSessionId: string | null;
  workspaceName: string;
  onOpenSystemTab: (kind: SystemWorkspaceKind) => void;
  onOpenSessionTab: (session: SessionRecord) => void;
}

export function GlobalSidebar({
  sessions,
  activeSessionId,
  workspaceName,
  onOpenSystemTab,
  onOpenSessionTab,
}: GlobalSidebarProps) {
  return (
    <aside className="workbench-sidebar" aria-label="全局导航">
      <div className="sidebar-brand">
        <span className="brand-mark" aria-hidden="true">元</span>
        <div>
          <strong>Yuanbao Agent</strong>
          <span>{workspaceName}</span>
        </div>
      </div>

      <nav className="sidebar-primary" aria-label="工作区">
        <button type="button" onClick={() => onOpenSystemTab("new-session")}>新会话</button>
        <button type="button" onClick={() => onOpenSystemTab("scheduled")}>调度</button>
      </nav>

      <label className="sidebar-search">
        <span>会话搜索</span>
        <input type="search" aria-label="Search sessions" />
      </label>

      <div className="session-rail" aria-label="会话列表">
        {sessions.length ? (
          sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              className="session-rail-item"
              data-active={session.id === activeSessionId}
              onClick={() => onOpenSessionTab(session)}
            >
              <span className="session-dot" aria-hidden="true" />
              <span>{session.title || "未命名会话"}</span>
            </button>
          ))
        ) : (
          <p className="sidebar-empty">暂无会话</p>
        )}
      </div>

      <div className="sidebar-footer">
        <button type="button" onClick={() => onOpenSystemTab("settings")}>设置</button>
      </div>
    </aside>
  );
}
```

- [ ] **Step 4: Create `WorkspaceTabs.tsx`**

```tsx
import type { WorkbenchTab } from "./types";

interface WorkspaceTabsProps {
  tabs: WorkbenchTab[];
  activeTabId: WorkbenchTab["id"];
  onActivateTab: (tabId: WorkbenchTab["id"]) => void;
  onCloseTab: (tabId: WorkbenchTab["id"]) => void;
}

export function WorkspaceTabs({ tabs, activeTabId, onActivateTab, onCloseTab }: WorkspaceTabsProps) {
  return (
    <div className="workspace-tabs" role="tablist" aria-label="已打开工作区">
      {tabs.map((tab) => (
        <div key={tab.id} className="workspace-tab-wrap" data-active={tab.id === activeTabId}>
          <button
            type="button"
            role="tab"
            aria-selected={tab.id === activeTabId}
            className="workspace-tab"
            onClick={() => onActivateTab(tab.id)}
          >
            {tab.title}
          </button>
          {tab.closable ? (
            <button
              type="button"
              className="workspace-tab-close"
              aria-label={`关闭 ${tab.title}`}
              onClick={() => onCloseTab(tab.id)}
            >
              ×
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Create `WorkspaceFrame.tsx`**

```tsx
import type { ReactNode } from "react";

interface WorkspaceFrameProps {
  children: ReactNode;
  composerVisible: boolean;
}

export function WorkspaceFrame({ children, composerVisible }: WorkspaceFrameProps) {
  return (
    <main className="workspace-frame" data-composer-visible={composerVisible}>
      {children}
    </main>
  );
}
```

- [ ] **Step 6: Create `ComposerDock.tsx`**

```tsx
interface ComposerDockProps {
  value: string;
  providerLabel: string;
  cwdLabel: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
}

export function ComposerDock({
  value,
  providerLabel,
  cwdLabel,
  disabled,
  onChange,
  onSubmit,
}: ComposerDockProps) {
  return (
    <section className="composer-dock" aria-label="任务输入区">
      <textarea
        aria-label="输入任务"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby="composer-help"
      />
      <span id="composer-help" className="composer-help">告诉我你想构建、修复或检查什么。</span>
      <div className="composer-actions">
        <button type="button" className="composer-ghost">+</button>
        <span className="composer-pill">{cwdLabel}</span>
        <span className="composer-pill">{providerLabel}</span>
        <button type="button" className="composer-run" disabled={disabled || !value.trim()} onClick={onSubmit}>
          Run
        </button>
      </div>
    </section>
  );
}
```

- [ ] **Step 7: Create `AppShell.tsx`**

```tsx
import type { ReactNode } from "react";
import type { SessionRecord } from "@shared";
import { ComposerDock } from "./ComposerDock";
import { GlobalSidebar } from "./GlobalSidebar";
import { WorkspaceFrame } from "./WorkspaceFrame";
import { WorkspaceTabs } from "./WorkspaceTabs";
import type { SystemWorkspaceKind, WorkbenchTab } from "./types";

interface AppShellProps {
  tabs: WorkbenchTab[];
  activeTabId: WorkbenchTab["id"];
  sessions: SessionRecord[];
  activeSessionId: string | null;
  workspaceName: string;
  composerVisible: boolean;
  promptValue: string;
  providerLabel: string;
  cwdLabel: string;
  disabled: boolean;
  children: ReactNode;
  onPromptChange: (value: string) => void;
  onSubmitPrompt: () => void;
  onOpenSystemTab: (kind: SystemWorkspaceKind) => void;
  onOpenSessionTab: (session: SessionRecord) => void;
  onActivateTab: (tabId: WorkbenchTab["id"]) => void;
  onCloseTab: (tabId: WorkbenchTab["id"]) => void;
}

export function AppShell(props: AppShellProps) {
  return (
    <div className="workbench-shell">
      <GlobalSidebar
        sessions={props.sessions}
        activeSessionId={props.activeSessionId}
        workspaceName={props.workspaceName}
        onOpenSystemTab={props.onOpenSystemTab}
        onOpenSessionTab={props.onOpenSessionTab}
      />
      <section className="workbench-main">
        <WorkspaceTabs
          tabs={props.tabs}
          activeTabId={props.activeTabId}
          onActivateTab={props.onActivateTab}
          onCloseTab={props.onCloseTab}
        />
        <WorkspaceFrame composerVisible={props.composerVisible}>{props.children}</WorkspaceFrame>
        {props.composerVisible ? (
          <ComposerDock
            value={props.promptValue}
            providerLabel={props.providerLabel}
            cwdLabel={props.cwdLabel}
            disabled={props.disabled}
            onChange={props.onPromptChange}
            onSubmit={props.onSubmitPrompt}
          />
        ) : null}
      </section>
    </div>
  );
}
```

- [ ] **Step 8: Add workbench CSS**

Create `app/src/ui/workbench/workbench.css`:

```css
:root {
  --wb-paper: #f7f2e8;
  --wb-paper-strong: #fffaf0;
  --wb-wood: #9b4f32;
  --wb-wood-dark: #713622;
  --wb-copper: #d8b8a6;
  --wb-ink: #1f1a17;
  --wb-muted: #7b6a5f;
  --wb-green: #2f9b64;
  --wb-radius: 22px;
  --wb-shadow: 0 18px 50px rgba(63, 38, 24, 0.08);
  font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
}

body {
  background:
    linear-gradient(90deg, rgba(155, 79, 50, 0.035) 1px, transparent 1px),
    linear-gradient(180deg, rgba(155, 79, 50, 0.025) 1px, transparent 1px),
    var(--wb-paper);
  color: var(--wb-ink);
}

button,
textarea,
input {
  font: inherit;
}

.workbench-shell {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  min-height: 100vh;
  background: var(--wb-paper);
}

.workbench-sidebar {
  display: flex;
  flex-direction: column;
  min-width: 0;
  border-right: 1px solid var(--wb-copper);
  background: rgba(255, 250, 240, 0.72);
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 28px 22px 20px;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border: 1px solid var(--wb-copper);
  border-radius: 14px;
  color: var(--wb-wood);
  background: var(--wb-paper-strong);
  font-family: "Songti SC", "Noto Serif SC", serif;
  font-weight: 800;
}

.sidebar-brand strong,
.sidebar-brand span {
  display: block;
}

.sidebar-brand span {
  color: var(--wb-muted);
  font-size: 12px;
}

.sidebar-primary,
.sidebar-footer {
  display: grid;
  gap: 4px;
  padding: 8px 16px;
}

.sidebar-primary button,
.sidebar-footer button,
.session-rail-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  border: 0;
  border-radius: 16px;
  padding: 12px 14px;
  background: transparent;
  color: var(--wb-ink);
  text-align: left;
  cursor: pointer;
}

.sidebar-primary button:hover,
.sidebar-footer button:hover,
.session-rail-item:hover,
.session-rail-item[data-active="true"] {
  background: rgba(155, 79, 50, 0.08);
}

.sidebar-search {
  display: grid;
  gap: 8px;
  padding: 18px 16px 8px;
}

.sidebar-search span {
  color: var(--wb-muted);
  font-size: 12px;
}

.sidebar-search input {
  width: 100%;
  border: 1px solid var(--wb-copper);
  border-radius: 14px;
  padding: 10px 12px;
  background: var(--wb-paper-strong);
}

.session-rail {
  flex: 1;
  overflow: auto;
  padding: 6px 16px;
}

.session-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--wb-copper);
}

.sidebar-empty {
  margin: 10px 14px;
  color: var(--wb-muted);
}

.sidebar-footer {
  border-top: 1px solid rgba(216, 184, 166, 0.7);
  padding-block: 16px;
}

.workbench-main {
  position: relative;
  display: grid;
  grid-template-rows: 48px minmax(0, 1fr);
  min-width: 0;
  min-height: 100vh;
}

.workspace-tabs {
  display: flex;
  overflow-x: auto;
  border-bottom: 1px solid var(--wb-copper);
  background: rgba(255, 250, 240, 0.72);
}

.workspace-tab-wrap {
  display: flex;
  align-items: center;
  min-width: 140px;
  border-right: 1px solid rgba(216, 184, 166, 0.7);
  background: transparent;
}

.workspace-tab-wrap[data-active="true"] {
  background: var(--wb-paper-strong);
}

.workspace-tab {
  flex: 1;
  border: 0;
  padding: 14px 18px;
  background: transparent;
  color: var(--wb-ink);
  text-align: left;
  cursor: pointer;
}

.workspace-tab-close {
  border: 0;
  margin-right: 8px;
  border-radius: 999px;
  background: transparent;
  color: var(--wb-muted);
}

.workspace-frame {
  overflow: auto;
  padding: 32px 42px;
}

.workspace-frame[data-composer-visible="true"] {
  padding-bottom: 190px;
}

.composer-dock {
  position: absolute;
  left: 50%;
  right: 42px;
  bottom: 28px;
  transform: translateX(-50%);
  display: grid;
  width: min(960px, calc(100% - 84px));
  border: 1px solid var(--wb-copper);
  border-radius: 24px;
  background: rgba(255, 250, 240, 0.96);
  box-shadow: var(--wb-shadow);
  overflow: hidden;
}

.composer-dock textarea {
  min-height: 76px;
  border: 0;
  padding: 18px 20px;
  resize: none;
  background: transparent;
  color: var(--wb-ink);
}

.composer-help {
  padding: 0 20px 12px;
  color: var(--wb-muted);
  font-size: 13px;
}

.composer-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  border-top: 1px solid rgba(216, 184, 166, 0.7);
  padding: 12px 14px;
}

.composer-ghost,
.composer-pill,
.composer-run {
  border-radius: 999px;
}

.composer-ghost {
  border: 0;
  width: 36px;
  height: 36px;
  background: transparent;
}

.composer-pill {
  padding: 8px 14px;
  background: rgba(155, 79, 50, 0.08);
  color: var(--wb-muted);
}

.composer-run {
  margin-left: auto;
  border: 0;
  padding: 10px 22px;
  background: var(--wb-wood);
  color: white;
}

@media (max-width: 900px) {
  .workbench-shell {
    grid-template-columns: 1fr;
  }

  .workbench-sidebar {
    display: none;
  }

  .workspace-frame {
    padding: 22px;
  }

  .composer-dock {
    right: 16px;
    bottom: 16px;
    width: calc(100% - 32px);
  }
}
```

- [ ] **Step 9: Import workbench CSS**

Add this at the top of `app/src/styles.css`:

```css
@import "./ui/workbench/workbench.css";
```

- [ ] **Step 10: Run shell test**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test -- src/ui/workbench/AppShell.test.tsx
```

Expected: PASS.

- [ ] **Step 11: Run typecheck**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run typecheck
```

Expected: PASS.

- [ ] **Step 12: Commit**

```powershell
cd D:\py\yuanbao_agent
git add app/src/styles.css app/src/ui/workbench
git commit -m "feat: add workbench shell components"
```

---

### Task 3: Integrate The Shell Into App Without Moving Runtime Logic

**Files:**

- Modify: `app/src/App.tsx`
- Create: `app/src/ui/workbench/workspaces/NewSessionWorkspace.tsx`
- Create: `app/src/ui/workbench/workspaces/SessionWorkspace.tsx`

- [ ] **Step 1: Create new-session workspace**

Create `app/src/ui/workbench/workspaces/NewSessionWorkspace.tsx`:

```tsx
interface NewSessionWorkspaceProps {
  workspacePath: string;
  hostStatusText: string;
}

export function NewSessionWorkspace({ workspacePath, hostStatusText }: NewSessionWorkspaceProps) {
  return (
    <section className="workspace-center" aria-label="新会话">
      <div className="workspace-seal" aria-hidden="true">元</div>
      <h1>新会话</h1>
      <p>从一个干净的工作台开始，把任务、审批、补丁和记录放回各自的位置。</p>
      <dl className="workspace-kv">
        <div>
          <dt>工作区</dt>
          <dd>{workspacePath}</dd>
        </div>
        <div>
          <dt>运行时</dt>
          <dd>{hostStatusText}</dd>
        </div>
      </dl>
    </section>
  );
}
```

- [ ] **Step 2: Create session workspace**

Create `app/src/ui/workbench/workspaces/SessionWorkspace.tsx`:

```tsx
import type { ReactNode } from "react";
import type { SessionRecord, TaskRecord } from "@shared";

interface SessionWorkspaceProps {
  session: SessionRecord | null;
  activeTask: TaskRecord | null;
  children: ReactNode;
}

export function SessionWorkspace({ session, activeTask, children }: SessionWorkspaceProps) {
  if (!session) {
    return (
      <section className="workspace-empty" aria-label="会话">
        <h1>会话未打开</h1>
        <p>从左侧选择一个会话，或在新会话页提交任务。</p>
      </section>
    );
  }

  return (
    <section className="session-workspace" aria-label="会话">
      <header className="workspace-heading">
        <span className="status-dot" data-status={session.status} />
        <div>
          <h1>{session.title || "未命名会话"}</h1>
          <p>
            {session.status} · {activeTask?.status ?? "no active task"}
          </p>
        </div>
      </header>
      <div className="session-body">{children}</div>
    </section>
  );
}
```

- [ ] **Step 3: Add workspace CSS**

Append to `app/src/ui/workbench/workbench.css`:

```css
.workspace-center {
  display: grid;
  place-items: center;
  align-content: center;
  min-height: 62vh;
  text-align: center;
}

.workspace-center h1,
.workspace-heading h1,
.workspace-empty h1 {
  margin: 0;
  font-family: "Songti SC", "Noto Serif SC", serif;
  font-size: clamp(32px, 5vw, 54px);
  color: var(--wb-ink);
}

.workspace-center p,
.workspace-heading p,
.workspace-empty p {
  margin: 8px 0 0;
  color: var(--wb-muted);
}

.workspace-seal {
  display: grid;
  place-items: center;
  width: 88px;
  height: 88px;
  margin-bottom: 28px;
  border: 1px solid var(--wb-copper);
  border-radius: 28px;
  background: var(--wb-paper-strong);
  box-shadow: var(--wb-shadow);
  color: var(--wb-wood);
  font-family: "Songti SC", "Noto Serif SC", serif;
  font-size: 40px;
  font-weight: 800;
}

.workspace-kv {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 12px;
  margin: 28px 0 0;
}

.workspace-kv div {
  min-width: 220px;
  border: 1px solid var(--wb-copper);
  border-radius: 18px;
  padding: 12px 16px;
  background: rgba(255, 250, 240, 0.8);
  text-align: left;
}

.workspace-kv dt {
  color: var(--wb-muted);
  font-size: 12px;
}

.workspace-kv dd {
  margin: 2px 0 0;
}

.workspace-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  max-width: 1080px;
  margin: 0 auto 24px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--wb-green);
}

.session-body {
  max-width: 1080px;
  margin: 0 auto;
}

.workspace-empty {
  display: grid;
  align-content: center;
  min-height: 58vh;
  text-align: center;
}
```

- [ ] **Step 4: Write an integration checkpoint before editing `App.tsx`**

Open `app/src/App.tsx` and locate the current final `return`. Identify these existing values before changing markup:

```ts
sessions
activeSessionId
activeSession
activeTask
prompt
loading
workspace
hostStatus
providerSettings
handleSend
setPrompt
```

Expected: all values already exist or have direct equivalents in `App.tsx`.

- [ ] **Step 5: Add imports to `App.tsx`**

Add imports near existing local imports:

```ts
import { AppShell } from "./ui/workbench/AppShell";
import { getInitialTabs, closeTab, openSessionTab, openSystemTab } from "./ui/workbench/tabModel";
import type { SystemWorkspaceKind, WorkbenchTab } from "./ui/workbench/types";
import { NewSessionWorkspace } from "./ui/workbench/workspaces/NewSessionWorkspace";
import { SessionWorkspace } from "./ui/workbench/workspaces/SessionWorkspace";
```

- [ ] **Step 6: Add tab state in `App.tsx`**

Inside `App`, add:

```ts
const [openTabs, setOpenTabs] = useState<WorkbenchTab[]>(() => getInitialTabs());
const [activeTabId, setActiveTabId] = useState<WorkbenchTab["id"]>("system:new-session");

function handleOpenSystemTab(kind: SystemWorkspaceKind): void {
  setOpenTabs((current) => {
    const result = openSystemTab(current, kind);
    setActiveTabId(result.activeTabId);
    return result.tabs;
  });
}

function handleOpenSessionTab(session: SessionRecord): void {
  setOpenTabs((current) => {
    const result = openSessionTab(current, { id: session.id, title: session.title });
    setActiveTabId(result.activeTabId);
    setActiveSessionId(session.id);
    return result.tabs;
  });
}

function handleCloseTab(tabId: WorkbenchTab["id"]): void {
  setOpenTabs((current) => {
    const result = closeTab(current, tabId, activeTabId);
    setActiveTabId(result.activeTabId);
    if (result.activeTabId.startsWith("session:")) {
      setActiveSessionId(result.activeTabId.replace("session:", ""));
    }
    return result.tabs;
  });
}
```

- [ ] **Step 7: Add active workspace rendering in `App.tsx`**

Before the final return, add:

```tsx
const activeTab = openTabs.find((tab) => tab.id === activeTabId) ?? openTabs[0];
const composerVisible = activeTab.kind === "new-session" || activeTab.kind === "session";
const workspaceName = workspace?.name ?? workspace?.rootPath?.split(/[\\/]/).filter(Boolean).pop() ?? "yuanbao_agent";
const providerLabel = providerSettings.model || providerSettings.name || "Provider";
const cwdLabel = workspace?.rootPath ?? DEFAULT_WORKSPACE_PATH;
const hostStatusText = hostStatus?.runtimeTransport ?? "mock-browser";

let workspaceContent: JSX.Element;
if (activeTab.kind === "new-session") {
  workspaceContent = <NewSessionWorkspace workspacePath={cwdLabel} hostStatusText={hostStatusText} />;
} else if (activeTab.kind === "session") {
  workspaceContent = (
    <SessionWorkspace session={activeSession} activeTask={activeTask}>
      {/* Move the existing chat/task/approval/patch/trace markup here in Task 6. */}
      <p className="workspace-empty">会话运行记录将在迁移任务中接入。</p>
    </SessionWorkspace>
  );
} else {
  workspaceContent = <p className="workspace-empty">{activeTab.title} 工作区将在后续任务接入。</p>;
}
```

- [ ] **Step 8: Replace final shell markup in `App.tsx`**

Replace the existing final returned `<div className="shell">...</div>` with:

```tsx
return (
  <AppShell
    tabs={openTabs}
    activeTabId={activeTabId}
    sessions={sessions}
    activeSessionId={activeSessionId}
    workspaceName={workspaceName}
    composerVisible={composerVisible}
    promptValue={prompt}
    providerLabel={providerLabel}
    cwdLabel={cwdLabel}
    disabled={loading}
    onPromptChange={setPrompt}
    onSubmitPrompt={handleSend}
    onOpenSystemTab={handleOpenSystemTab}
    onOpenSessionTab={handleOpenSessionTab}
    onActivateTab={(tabId) => {
      setActiveTabId(tabId);
      if (tabId.startsWith("session:")) {
        setActiveSessionId(tabId.replace("session:", ""));
      }
    }}
    onCloseTab={handleCloseTab}
  >
    {workspaceContent}
  </AppShell>
);
```

- [ ] **Step 9: Run typecheck**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run typecheck
```

Expected: PASS. If duplicate JSX comments inside expressions trigger type errors, replace the comment with the visible `<p>` only.

- [ ] **Step 10: Run tests**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test
```

Expected: PASS.

- [ ] **Step 11: Commit**

```powershell
cd D:\py\yuanbao_agent
git add app/src/App.tsx app/src/ui/workbench
git commit -m "feat: integrate workbench shell"
```

---

### Task 4: Implement Settings Model And Settings Workspace

**Files:**

- Create: `app/src/ui/workbench/settingsModel.ts`
- Test: `app/src/ui/workbench/settingsModel.test.ts`
- Create: `app/src/ui/workbench/workspaces/settings/SettingsWorkspace.tsx`
- Create: `app/src/ui/workbench/workspaces/settings/SettingsSidebar.tsx`
- Create: `app/src/ui/workbench/workspaces/settings/ProviderSettingsView.tsx`
- Create: `app/src/ui/workbench/workspaces/settings/ProviderModal.tsx`
- Create: `app/src/ui/workbench/workspaces/settings/PermissionSettingsView.tsx`
- Create: `app/src/ui/workbench/workspaces/settings/GeneralSettingsView.tsx`
- Create: `app/src/ui/workbench/workspaces/settings/SkillsSettingsView.tsx`
- Create: `app/src/ui/workbench/workspaces/settings/SimpleSettingsView.tsx`
- Test: `app/src/ui/workbench/workspaces/settings/SettingsWorkspace.test.tsx`
- Modify: `app/src/App.tsx`

- [ ] **Step 1: Write failing settings model test**

Create `app/src/ui/workbench/settingsModel.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { getInitialSettingsSection, getSettingsNavItems, isSettingsSection } from "./settingsModel";

describe("settingsModel", () => {
  it("uses providers as the initial settings section", () => {
    expect(getInitialSettingsSection()).toBe("providers");
  });

  it("contains all approved settings sections in display order", () => {
    expect(getSettingsNavItems().map((item) => item.id)).toEqual([
      "providers",
      "permissions",
      "general",
      "im-adapters",
      "agents",
      "skills",
      "computer-use",
      "about",
    ]);
  });

  it("validates settings section ids", () => {
    expect(isSettingsSection("providers")).toBe(true);
    expect(isSettingsSection("missing")).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test -- src/ui/workbench/settingsModel.test.ts
```

Expected: FAIL because `settingsModel.ts` does not exist.

- [ ] **Step 3: Implement settings model**

Create `app/src/ui/workbench/settingsModel.ts`:

```ts
import type { SettingsSection } from "./types";

export interface SettingsNavItem {
  id: SettingsSection;
  label: string;
  description: string;
}

const SETTINGS_NAV_ITEMS: SettingsNavItem[] = [
  { id: "providers", label: "服务商", description: "管理 API 服务商以访问模型。" },
  { id: "permissions", label: "权限", description: "控制工具执行权限的处理方式。" },
  { id: "general", label: "通用", description: "调整主题、语言和推理强度。" },
  { id: "im-adapters", label: "IM 接入", description: "配置外部消息入口。" },
  { id: "agents", label: "Agents", description: "管理 agent 行为与清单。" },
  { id: "skills", label: "技能", description: "查看已安装技能。" },
  { id: "computer-use", label: "Computer Use", description: "检查本机控制能力状态。" },
  { id: "about", label: "关于", description: "查看应用版本与构建信息。" },
];

export function getInitialSettingsSection(): SettingsSection {
  return "providers";
}

export function getSettingsNavItems(): SettingsNavItem[] {
  return SETTINGS_NAV_ITEMS;
}

export function isSettingsSection(value: string): value is SettingsSection {
  return SETTINGS_NAV_ITEMS.some((item) => item.id === value);
}
```

- [ ] **Step 4: Run settings model test**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test -- src/ui/workbench/settingsModel.test.ts
```

Expected: PASS.

- [ ] **Step 5: Write failing settings workspace test**

Create `app/src/ui/workbench/workspaces/settings/SettingsWorkspace.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SettingsWorkspace } from "./SettingsWorkspace";

describe("SettingsWorkspace", () => {
  it("renders providers as the default section", () => {
    render(<SettingsWorkspace />);

    expect(screen.getByRole("heading", { name: "服务商" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加服务商" })).toBeInTheDocument();
  });

  it("switches settings sections inside the workspace", async () => {
    render(<SettingsWorkspace />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "权限" }));

    expect(screen.getByRole("heading", { name: "权限模式" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加服务商" })).not.toBeInTheDocument();
  });

  it("opens and closes the provider modal", async () => {
    render(<SettingsWorkspace />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "添加服务商" }));
    expect(screen.getByRole("dialog", { name: "添加服务商" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog", { name: "添加服务商" })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 6: Run test to verify it fails**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test -- src/ui/workbench/workspaces/settings/SettingsWorkspace.test.tsx
```

Expected: FAIL because settings workspace components do not exist.

- [ ] **Step 7: Create `SettingsSidebar.tsx`**

```tsx
import { getSettingsNavItems } from "../../settingsModel";
import type { SettingsSection } from "../../types";

interface SettingsSidebarProps {
  activeSection: SettingsSection;
  onSelectSection: (section: SettingsSection) => void;
}

export function SettingsSidebar({ activeSection, onSelectSection }: SettingsSidebarProps) {
  return (
    <aside className="settings-sidebar" aria-label="设置导航">
      <nav>
        {getSettingsNavItems()
          .filter((item) => item.id !== "about")
          .map((item) => (
            <button
              key={item.id}
              type="button"
              data-active={item.id === activeSection}
              onClick={() => onSelectSection(item.id)}
            >
              {item.label}
            </button>
          ))}
      </nav>
      <button
        type="button"
        className="settings-about"
        data-active={activeSection === "about"}
        onClick={() => onSelectSection("about")}
      >
        关于
      </button>
    </aside>
  );
}
```

- [ ] **Step 8: Create provider settings view**

Create `app/src/ui/workbench/workspaces/settings/ProviderSettingsView.tsx`:

```tsx
import type { ProviderProfile } from "@shared";

interface ProviderSettingsViewProps {
  profiles?: ProviderProfile[];
  activeProfileId?: string;
  onAddProvider: () => void;
}

export function ProviderSettingsView({ profiles = [], activeProfileId, onAddProvider }: ProviderSettingsViewProps) {
  const visibleProfiles = profiles.length
    ? profiles
    : [
        {
          id: "mock-default",
          name: "Claude 官方",
          mode: "mock" as const,
          baseUrl: "Anthropic 原生接入",
          model: "no API key required",
          defaultModel: "no API key required",
        },
      ];

  return (
    <section className="settings-content-view">
      <div className="settings-content-header">
        <div>
          <h1>服务商</h1>
          <p>管理 API 服务商以访问模型。</p>
        </div>
        <button type="button" className="wood-button" onClick={onAddProvider}>添加服务商</button>
      </div>
      <ul className="provider-list">
        {visibleProfiles.map((profile) => {
          const active = profile.id === activeProfileId || (!activeProfileId && profile.id === "mock-default");
          return (
            <li key={profile.id} className="provider-row" data-active={active}>
              <span className="provider-dot" data-active={active} />
              <div>
                <strong>{profile.name}</strong>
                <p>{profile.baseUrl} · {profile.model}</p>
              </div>
              {active ? <span className="active-chip">已激活</span> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
```

- [ ] **Step 9: Create provider modal**

Create `app/src/ui/workbench/workspaces/settings/ProviderModal.tsx`:

```tsx
interface ProviderModalProps {
  open: boolean;
  onClose: () => void;
}

export function ProviderModal({ open, onClose }: ProviderModalProps) {
  if (!open) {
    return null;
  }

  return (
    <div className="modal-backdrop">
      <section className="provider-modal" role="dialog" aria-modal="true" aria-label="添加服务商">
        <header>
          <h2>添加服务商</h2>
          <button type="button" aria-label="关闭" onClick={onClose}>×</button>
        </header>
        <div className="preset-row" aria-label="预设">
          {["DeepSeek", "Zhipu GLM", "Kimi", "MiniMax", "Custom"].map((preset) => (
            <button key={preset} type="button" data-active={preset === "DeepSeek"}>{preset}</button>
          ))}
        </div>
        <div className="provider-form-grid">
          <label>
            <span>名称 *</span>
            <input defaultValue="DeepSeek" />
          </label>
          <label>
            <span>备注</span>
            <input aria-label="备注" />
          </label>
          <label>
            <span>接口地址</span>
            <input defaultValue="https://api.deepseek.com/anthropic" />
          </label>
          <label>
            <span>API 密钥 *</span>
            <input aria-label="API 密钥" type="password" />
          </label>
        </div>
        <div className="model-map">
          <label><span>主模型 *</span><input defaultValue="DeepSeek-V3.2" /></label>
          <label><span>Haiku 模型</span><input defaultValue="DeepSeek-V3.2" /></label>
          <label><span>Sonnet 模型</span><input defaultValue="DeepSeek-V3.2" /></label>
          <label><span>Opus 模型</span><input defaultValue="DeepSeek-V3.2" /></label>
        </div>
        <button type="button" className="secondary-line">测试连接</button>
        <label className="json-field">
          <span>设置 JSON</span>
          <textarea defaultValue={'{\n  "env": {\n    "ANTHROPIC_AUTH_TOKEN": "(your API key)"\n  }\n}'} />
        </label>
        <footer>
          <button type="button" className="secondary-line" onClick={onClose}>取消</button>
          <button type="button" className="wood-button">添加</button>
        </footer>
      </section>
    </div>
  );
}
```

- [ ] **Step 10: Create permission, general, skills, and simple views**

Create `PermissionSettingsView.tsx`:

```tsx
const MODES = [
  ["访问权限", "执行工具前先询问"],
  ["接受编辑", "自动批准文件编辑，其他操作仍访问"],
  ["计划模式", "仅思考和规划，不执行操作"],
  ["跳过全部", "跳过所有权限检查（危险）"],
] as const;

export function PermissionSettingsView() {
  return (
    <section className="settings-content-view narrow">
      <h1>权限模式</h1>
      <p>控制工具执行权限的处理方式。</p>
      <div className="permission-list">
        {MODES.map(([title, description], index) => (
          <button key={title} type="button" data-active={index === 3}>
            <strong>{title}</strong>
            <span>{description}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
```

Create `GeneralSettingsView.tsx`:

```tsx
export function GeneralSettingsView() {
  return (
    <section className="settings-content-view narrow">
      <h1>通用</h1>
      <div className="settings-group">
        <h2>配色主题</h2>
        <p>在亮色与暗色工作区之间切换，不影响原有亮色主题。</p>
        <div className="segmented"><button data-active="true">亮色</button><button>暗色</button></div>
      </div>
      <div className="settings-group">
        <h2>语言</h2>
        <p>选择应用程序的显示语言。</p>
        <div className="segmented"><button>English</button><button data-active="true">中文</button></div>
      </div>
      <div className="settings-group">
        <h2>推理强度</h2>
        <p>控制模型使用的计算量。</p>
        <div className="segmented four"><button>低</button><button>中</button><button>高</button><button data-active="true">最大</button></div>
      </div>
      <div className="settings-group outlined">
        <h2>WebFetch 预检</h2>
        <label className="check-row"><input type="checkbox" defaultChecked /> 跳过 WebFetch 域名预检</label>
        <p>只有在你明确需要恢复上游默认安全预检时，才建议关闭这个选项。</p>
      </div>
    </section>
  );
}
```

Create `SkillsSettingsView.tsx`:

```tsx
export function SkillsSettingsView() {
  return (
    <section className="settings-content-view">
      <h1>已安装技能</h1>
      <p>技能扩展 Agent 的能力。在全局技能目录中管理技能。</p>
      <div className="empty-cabinet">
        <strong>暂无已安装技能</strong>
        <span>重启 Codex 后，全局技能会在新会话中可用。</span>
      </div>
    </section>
  );
}
```

Create `SimpleSettingsView.tsx`:

```tsx
interface SimpleSettingsViewProps {
  title: string;
  description: string;
}

export function SimpleSettingsView({ title, description }: SimpleSettingsViewProps) {
  return (
    <section className="settings-content-view">
      <h1>{title}</h1>
      <p>{description}</p>
      <div className="empty-cabinet">
        <strong>{title} 暂无可配置项目</strong>
        <span>该区域保留结构，后续功能会在这里接入。</span>
      </div>
    </section>
  );
}
```

- [ ] **Step 11: Create `SettingsWorkspace.tsx`**

```tsx
import { useState } from "react";
import type { AppConfig } from "@shared";
import { getInitialSettingsSection, getSettingsNavItems } from "../../settingsModel";
import type { SettingsSection } from "../../types";
import { GeneralSettingsView } from "./GeneralSettingsView";
import { PermissionSettingsView } from "./PermissionSettingsView";
import { ProviderModal } from "./ProviderModal";
import { ProviderSettingsView } from "./ProviderSettingsView";
import { SettingsSidebar } from "./SettingsSidebar";
import { SimpleSettingsView } from "./SimpleSettingsView";
import { SkillsSettingsView } from "./SkillsSettingsView";

interface SettingsWorkspaceProps {
  config?: AppConfig | null;
}

export function SettingsWorkspace({ config }: SettingsWorkspaceProps) {
  const [activeSection, setActiveSection] = useState<SettingsSection>(getInitialSettingsSection);
  const [providerModalOpen, setProviderModalOpen] = useState(false);

  const content = (() => {
    if (activeSection === "providers") {
      return (
        <ProviderSettingsView
          profiles={config?.provider.profiles}
          activeProfileId={config?.provider.activeProfileId}
          onAddProvider={() => setProviderModalOpen(true)}
        />
      );
    }
    if (activeSection === "permissions") return <PermissionSettingsView />;
    if (activeSection === "general") return <GeneralSettingsView />;
    if (activeSection === "skills") return <SkillsSettingsView />;
    const navItem = getSettingsNavItems().find((item) => item.id === activeSection);
    return <SimpleSettingsView title={navItem?.label ?? "设置"} description={navItem?.description ?? "配置应用行为。"} />;
  })();

  return (
    <section className="settings-workspace" aria-label="设置">
      <SettingsSidebar activeSection={activeSection} onSelectSection={setActiveSection} />
      <div className="settings-content">{content}</div>
      <ProviderModal open={providerModalOpen} onClose={() => setProviderModalOpen(false)} />
    </section>
  );
}
```

- [ ] **Step 12: Add settings CSS**

Append to `app/src/ui/workbench/workbench.css`:

```css
.settings-workspace {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  min-height: calc(100vh - 112px);
  margin: -32px -42px;
}

.settings-sidebar {
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--wb-copper);
  background: rgba(255, 250, 240, 0.48);
}

.settings-sidebar nav {
  display: grid;
  gap: 4px;
  padding: 18px 0;
}

.settings-sidebar button {
  border: 0;
  padding: 14px 20px;
  background: transparent;
  color: var(--wb-ink);
  text-align: left;
}

.settings-sidebar button[data-active="true"] {
  background: rgba(155, 79, 50, 0.09);
  font-weight: 700;
}

.settings-about {
  margin-top: auto;
  border-top: 1px solid rgba(216, 184, 166, 0.7) !important;
}

.settings-content {
  overflow: auto;
  padding: 32px 44px;
}

.settings-content-view {
  max-width: 1060px;
}

.settings-content-view.narrow {
  max-width: 720px;
}

.settings-content-view h1 {
  margin: 0;
  font-family: "Songti SC", "Noto Serif SC", serif;
  color: var(--wb-ink);
}

.settings-content-view p {
  color: var(--wb-muted);
}

.settings-content-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  margin-bottom: 24px;
}

.wood-button {
  border: 0;
  border-radius: 14px;
  padding: 10px 18px;
  background: var(--wb-wood);
  color: white;
}

.secondary-line {
  border: 1px solid var(--wb-copper);
  border-radius: 12px;
  padding: 9px 14px;
  background: var(--wb-paper-strong);
  color: var(--wb-wood);
}

.provider-list {
  display: grid;
  gap: 12px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.provider-row {
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
  border: 1px solid var(--wb-copper);
  border-radius: 20px;
  padding: 18px 20px;
  background: rgba(255, 250, 240, 0.76);
}

.provider-row[data-active="true"] {
  border-color: var(--wb-wood);
  background: rgba(155, 79, 50, 0.06);
}

.provider-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--wb-muted);
}

.provider-dot[data-active="true"] {
  background: var(--wb-green);
}

.provider-row p {
  margin: 3px 0 0;
}

.active-chip {
  border-radius: 999px;
  padding: 3px 8px;
  background: rgba(155, 79, 50, 0.14);
  color: var(--wb-wood);
  font-size: 12px;
  font-weight: 700;
}

.permission-list,
.settings-group {
  display: grid;
  gap: 12px;
  margin-top: 18px;
}

.permission-list button,
.settings-group.outlined {
  border: 1px solid var(--wb-copper);
  border-radius: 18px;
  padding: 16px 18px;
  background: rgba(255, 250, 240, 0.72);
  text-align: left;
}

.permission-list button[data-active="true"] {
  border-color: var(--wb-wood);
}

.permission-list span {
  display: block;
  color: var(--wb-muted);
}

.segmented {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.segmented.four {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.segmented button {
  border: 1px solid var(--wb-copper);
  border-radius: 999px;
  padding: 10px;
  background: var(--wb-paper-strong);
}

.segmented button[data-active="true"] {
  border-color: var(--wb-wood);
  background: var(--wb-wood);
  color: white;
}

.empty-cabinet {
  display: grid;
  place-items: center;
  min-height: 180px;
  margin-top: 24px;
  border: 1px dashed var(--wb-copper);
  border-radius: 22px;
  color: var(--wb-muted);
}

.modal-backdrop {
  position: fixed;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 28px;
  background: rgba(31, 26, 23, 0.28);
  z-index: 50;
}

.provider-modal {
  display: grid;
  gap: 18px;
  width: min(860px, 100%);
  max-height: min(920px, calc(100vh - 56px));
  overflow: auto;
  border: 1px solid var(--wb-copper);
  border-radius: 26px;
  padding: 28px;
  background: var(--wb-paper);
  box-shadow: 0 28px 80px rgba(31, 26, 23, 0.22);
}

.provider-modal header,
.provider-modal footer,
.preset-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.provider-modal header {
  justify-content: space-between;
}

.provider-modal footer {
  justify-content: flex-end;
}

.provider-form-grid,
.model-map {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.provider-form-grid label,
.model-map label,
.json-field {
  display: grid;
  gap: 6px;
}

.provider-form-grid input,
.model-map input,
.json-field textarea {
  border: 1px solid var(--wb-copper);
  border-radius: 12px;
  padding: 11px 12px;
  background: var(--wb-paper-strong);
}

.json-field textarea {
  min-height: 180px;
  font-family: "JetBrains Mono", "Consolas", monospace;
}
```

- [ ] **Step 13: Wire settings workspace into `App.tsx`**

Add import:

```ts
import { SettingsWorkspace } from "./ui/workbench/workspaces/settings/SettingsWorkspace";
```

Change the active workspace rendering branch:

```tsx
} else if (activeTab.kind === "settings") {
  workspaceContent = <SettingsWorkspace config={config} />;
} else {
  workspaceContent = <p className="workspace-empty">{activeTab.title} 工作区将在调度任务中接入。</p>;
}
```

- [ ] **Step 14: Run tests and typecheck**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test
npm run typecheck
```

Expected: PASS for both commands.

- [ ] **Step 15: Commit**

```powershell
cd D:\py\yuanbao_agent
git add app/src/App.tsx app/src/ui/workbench app/src/ui/workbench/workspaces/settings
git commit -m "feat: add settings workspace"
```

---

### Task 5: Add Scheduled Workspace And Composer Visibility Coverage

**Files:**

- Create: `app/src/ui/workbench/workspaces/ScheduledWorkspace.tsx`
- Modify: `app/src/App.tsx`
- Modify: `app/src/ui/workbench/AppShell.test.tsx`

- [ ] **Step 1: Add failing composer visibility test case**

Append to `AppShell.test.tsx`:

```tsx
it("keeps composer out of scheduled and settings workspaces", () => {
  const tabs: WorkbenchTab[] = [
    { id: "system:scheduled", kind: "scheduled", title: "调度" },
    { id: "system:settings", kind: "settings", title: "设置" },
  ];

  const { rerender } = render(
    <AppShell
      tabs={tabs}
      activeTabId="system:scheduled"
      sessions={sessions}
      activeSessionId={null}
      workspaceName="yuanbao_agent"
      composerVisible={false}
      promptValue=""
      onPromptChange={vi.fn()}
      onOpenSystemTab={vi.fn()}
      onOpenSessionTab={vi.fn()}
      onActivateTab={vi.fn()}
      onCloseTab={vi.fn()}
      onSubmitPrompt={vi.fn()}
      disabled={false}
      providerLabel="MiniMax-M2.7-highspeed"
      cwdLabel="D:/py/yuanbao_agent"
    >
      <section>Scheduled</section>
    </AppShell>,
  );

  expect(screen.queryByLabelText("输入任务")).not.toBeInTheDocument();

  rerender(
    <AppShell
      tabs={tabs}
      activeTabId="system:settings"
      sessions={sessions}
      activeSessionId={null}
      workspaceName="yuanbao_agent"
      composerVisible={false}
      promptValue=""
      onPromptChange={vi.fn()}
      onOpenSystemTab={vi.fn()}
      onOpenSessionTab={vi.fn()}
      onActivateTab={vi.fn()}
      onCloseTab={vi.fn()}
      onSubmitPrompt={vi.fn()}
      disabled={false}
      providerLabel="MiniMax-M2.7-highspeed"
      cwdLabel="D:/py/yuanbao_agent"
    >
      <section>Settings</section>
    </AppShell>,
  );

  expect(screen.queryByLabelText("输入任务")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run shell tests**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test -- src/ui/workbench/AppShell.test.tsx
```

Expected: PASS if composer visibility is already controlled by props. If it fails, fix `AppShell` so it renders `ComposerDock` only when `composerVisible` is true.

- [ ] **Step 3: Create scheduled workspace**

Create `app/src/ui/workbench/workspaces/ScheduledWorkspace.tsx`:

```tsx
interface ScheduledWorkspaceProps {
  activeCount: number;
  disabledCount: number;
}

export function ScheduledWorkspace({ activeCount, disabledCount }: ScheduledWorkspaceProps) {
  const total = activeCount + disabledCount;
  return (
    <section className="scheduled-workspace" aria-label="调度">
      <div className="settings-content-header">
        <div>
          <h1>调度任务</h1>
          <p>按计划运行任务，或者在需要时手动触发。</p>
        </div>
        <button type="button" className="wood-button">新建任务</button>
      </div>
      <div className="scheduled-notice">调度任务只会在桌面应用保持打开时按时运行。</div>
      <div className="scheduled-metrics">
        <div><strong>{total}</strong><span>总任务</span></div>
        <div><strong>{activeCount}</strong><span>运行中</span></div>
        <div><strong>{disabledCount}</strong><span>已停用</span></div>
      </div>
      <div className="scheduled-list">
        <article>
          <span className="provider-dot" />
          <div>
            <strong>暂无调度任务</strong>
            <p>后续接入 runtime 调度数据后会显示在这里。</p>
          </div>
        </article>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Add scheduled CSS**

Append to `workbench.css`:

```css
.scheduled-workspace {
  max-width: 1180px;
  margin: 0 auto;
}

.scheduled-notice {
  border: 1px solid rgba(191, 145, 69, 0.32);
  border-radius: 16px;
  padding: 14px 16px;
  background: rgba(191, 145, 69, 0.08);
  color: var(--wb-muted);
}

.scheduled-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
  margin: 28px 0;
}

.scheduled-metrics div {
  border: 1px solid rgba(216, 184, 166, 0.7);
  border-radius: 20px;
  padding: 20px;
  background: rgba(255, 250, 240, 0.68);
}

.scheduled-metrics strong {
  display: block;
  font-size: 34px;
  line-height: 1;
}

.scheduled-metrics span {
  color: var(--wb-muted);
}

.scheduled-list article {
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr);
  gap: 16px;
  border-top: 1px solid var(--wb-copper);
  padding: 22px 0;
}
```

- [ ] **Step 5: Wire scheduled workspace into `App.tsx`**

Add import:

```ts
import { ScheduledWorkspace } from "./ui/workbench/workspaces/ScheduledWorkspace";
```

Change active workspace rendering:

```tsx
} else if (activeTab.kind === "scheduled") {
  workspaceContent = <ScheduledWorkspace activeCount={0} disabledCount={0} />;
} else if (activeTab.kind === "settings") {
  workspaceContent = <SettingsWorkspace config={config} />;
}
```

- [ ] **Step 6: Run tests, typecheck, and build**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test
npm run typecheck
npm run build
```

Expected: PASS for all commands.

- [ ] **Step 7: Commit**

```powershell
cd D:\py\yuanbao_agent
git add app/src/App.tsx app/src/ui/workbench
git commit -m "feat: add scheduled workspace"
```

---

### Task 6: Migrate Existing Session Runtime Panels Into Session Workspace

**Files:**

- Modify: `app/src/App.tsx`
- Modify: `app/src/ui/workbench/workspaces/SessionWorkspace.tsx`
- Create: `app/src/ui/workbench/workspaces/session/ConversationStream.tsx`
- Create: `app/src/ui/workbench/workspaces/session/TaskProgressPanel.tsx`
- Create: `app/src/ui/workbench/workspaces/session/ApprovalPanel.tsx`
- Create: `app/src/ui/workbench/workspaces/session/PatchPanel.tsx`
- Create: `app/src/ui/workbench/workspaces/session/TracePanel.tsx`

- [ ] **Step 1: Identify current session-only markup**

In `app/src/App.tsx`, locate these existing render sections:

```text
chat-list
steps
task-controls
approval-stack
patch-stack
trace-stack
tool-stack
timeline rich
```

Expected: these are currently rendered in the old single page and must move under `SessionWorkspace`.

- [ ] **Step 2: Create `ConversationStream.tsx`**

```tsx
import type { ChatMessageView } from "../../../App";

interface ConversationStreamProps {
  messages: ChatMessageView[];
}

export function ConversationStream({ messages }: ConversationStreamProps) {
  if (!messages.length) {
    return (
      <div className="empty-cabinet">
        <strong>暂无消息</strong>
        <span>发送第一条任务后，会话内容会显示在这里。</span>
      </div>
    );
  }

  return (
    <div className="chat-list">
      {messages.map((message) => (
        <article key={message.id} className="chat-message" data-role={message.role}>
          <div className="chat-meta">
            <strong>{message.role}</strong>
            {message.streaming ? <span className="streaming-pill">streaming</span> : null}
          </div>
          <p>{message.content}</p>
        </article>
      ))}
    </div>
  );
}
```

Before using this exact code, export `ChatMessageView` from `App.tsx` by changing:

```ts
interface ChatMessageView {
```

to:

```ts
export interface ChatMessageView {
```

- [ ] **Step 3: Extract current panels one at a time**

For each panel, copy the exact current JSX from `App.tsx` into its new component, then pass the already-computed view models as props:

```tsx
<TaskProgressPanel activeTask={activeTask} taskControlActions={taskControlActions} />
<ApprovalPanel approvalCards={approvalCards} />
<PatchPanel patchCards={patchCards} />
<TracePanel traceItems={traceItems} toolTimelineItems={toolTimelineItems} eventItems={eventItems} />
```

Use this prop rule:

```ts
interface PanelProps {
  items: ReadonlyArray<ExistingViewModel>;
  loading?: boolean;
  onAction?: (id: string) => void;
}
```

Do not recompute derived runtime state inside the panel components in this task. Keep derivation in `App.tsx` for now.

- [ ] **Step 4: Render migrated panels inside `SessionWorkspace`**

Replace the temporary session body in `App.tsx` with:

```tsx
<SessionWorkspace session={activeSession} activeTask={activeTask}>
  <ConversationStream messages={chatMessages} />
  <TaskProgressPanel activeTask={activeTask} taskControlActions={taskControlActions} />
  <ApprovalPanel approvalCards={approvalCards} />
  <PatchPanel patchCards={patchCards} />
  <TracePanel traceItems={traceItems} toolTimelineItems={toolTimelineItems} eventItems={eventItems} />
</SessionWorkspace>
```

- [ ] **Step 5: Delete the old single-page panel locations**

After the new session workspace compiles, remove the old occurrences of:

```text
approval-stack
patch-stack
trace-stack
tool-stack
timeline rich
```

Expected: no duplicated runtime panels remain outside `SessionWorkspace`.

- [ ] **Step 6: Run typecheck after each extracted panel**

Run after each panel extraction:

```powershell
cd D:\py\yuanbao_agent\app
npm run typecheck
```

Expected: PASS after each extraction.

- [ ] **Step 7: Run full tests and build**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test
npm run build
```

Expected: PASS for both commands.

- [ ] **Step 8: Commit**

```powershell
cd D:\py\yuanbao_agent
git add app/src/App.tsx app/src/ui/workbench
git commit -m "refactor: move runtime panels into session workspace"
```

---

### Task 7: Visual Polish And Browser Verification

**Files:**

- Modify: `app/src/ui/workbench/workbench.css`
- Modify: `app/src/styles.css`

- [ ] **Step 1: Remove obsolete old shell rules**

In `app/src/styles.css`, remove old layout rules that conflict with the workbench shell:

```css
.shell
.sidebar
.main
.grid
.hero
```

Keep reusable component rules that still apply to migrated panels:

```css
.badge
.meta
.chat-list
.chat-message
.approval-card
.patch-card
.tool-card
.trace-card
.patch-diff
.error-banner
.success-banner
```

- [ ] **Step 2: Add focus states**

Append to `workbench.css`:

```css
.workbench-shell :focus-visible {
  outline: 2px solid color-mix(in srgb, var(--wb-wood), white 20%);
  outline-offset: 3px;
}

.wood-button,
.composer-run,
.workspace-tab,
.sidebar-primary button,
.sidebar-footer button,
.settings-sidebar button,
.session-rail-item {
  transition:
    background-color 140ms cubic-bezier(.2,.8,.2,1),
    border-color 140ms cubic-bezier(.2,.8,.2,1),
    transform 140ms cubic-bezier(.2,.8,.2,1);
}

@media (prefers-reduced-motion: reduce) {
  .wood-button,
  .composer-run,
  .workspace-tab,
  .sidebar-primary button,
  .sidebar-footer button,
  .settings-sidebar button,
  .session-rail-item {
    transition: none;
  }
}
```

- [ ] **Step 3: Run static verification**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run test
npm run typecheck
npm run build
```

Expected: PASS for all commands.

- [ ] **Step 4: Start dev server**

Run:

```powershell
cd D:\py\yuanbao_agent\app
npm run dev -- --host 127.0.0.1 --port 1420
```

Expected: Vite starts on `http://127.0.0.1:1420`.

- [ ] **Step 5: Browser verification checklist**

Open `http://127.0.0.1:1420` and verify:

```text
1. The left global sidebar is visible on desktop.
2. Clicking 新会话, 调度, and 设置 opens or activates top tabs.
3. The main content pane shows one workspace at a time.
4. The composer is visible on 新会话.
5. The composer is hidden on 设置 and 调度.
6. Settings left navigation switches right-side content only.
7. 服务商 opens the 添加服务商 modal.
8. The modal scrolls internally when viewport height is small.
9. No console errors or warnings appear.
10. At 900px width, the layout remains usable with sidebar hidden.
```

- [ ] **Step 6: Commit**

```powershell
cd D:\py\yuanbao_agent
git add app/src/styles.css app/src/ui/workbench/workbench.css
git commit -m "style: polish workbench visual system"
```

---

## Self-Review Checklist

Spec coverage:

- Global sidebar: Task 2.
- Top opened-workspace tabs: Tasks 1 and 2.
- New session workspace: Task 3.
- Session workspace: Tasks 3 and 6.
- Scheduled workspace: Task 5.
- Settings workspace: Task 4.
- Provider modal: Task 4.
- Composer visibility rules: Tasks 2 and 5.
- Visual system: Tasks 2 and 7.
- Test coverage: Tasks 1, 2, 4, 5, and 7.

Plan guardrails:

- No backend runtime API changes are required.
- No shared type contract changes are required.
- Existing untracked `scripts/*` and `skills/*` files are unrelated to this implementation and should not be staged by these tasks.
- Every commit command must stage only files listed in its task.
