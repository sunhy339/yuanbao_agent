import { useEffect, useState } from "react";
import { type SettingsAboutInfo } from "./settingsTypes";

export function AboutPanel({
  about,
  workspaceFocus,
  workspaceFocusBusy,
  onSaveWorkspaceFocus,
  workspaceMemorySummary,
  workspaceMemoryBusy,
  onClearWorkspaceMemory,
  onOpenLogs,
  onOpenDataDirectory,
}: {
  about?: SettingsAboutInfo;
  workspaceFocus?: string | null;
  workspaceFocusBusy: boolean;
  onSaveWorkspaceFocus?: (focus: string) => void | Promise<void>;
  workspaceMemorySummary?: string | null;
  workspaceMemoryBusy: boolean;
  onClearWorkspaceMemory?: () => void | Promise<void>;
  onOpenLogs?: () => void;
  onOpenDataDirectory?: () => void;
}) {
  const rows = [
    ["版本", about?.version ?? "0.1.0"],
    ["运行时", about?.runtime ?? "Tauri + React"],
    ["数据路径", about?.dataPath ?? "未连接"],
    ["构建", about?.build ?? "development"],
  ];
  const memoryPreview = workspaceMemorySummary
    ?.split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && line !== "Project memory:" && line !== "项目记忆：")
    .slice(0, 4)
    .join("\n");
  const [focusDraft, setFocusDraft] = useState(workspaceFocus ?? "");

  useEffect(() => {
    setFocusDraft(workspaceFocus ?? "");
  }, [workspaceFocus]);

  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">关于</p>
          <h2>关于</h2>
          <p>本地智能体工作台，用于多会话编排、定时任务和受控工具执行。</p>
        </div>
      </header>
      <dl className="settings-definition-list">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="settings-provider-actions">
        <button type="button" className="settings-secondary-action" onClick={onOpenLogs} disabled={!onOpenLogs} title={!onOpenLogs ? "打开日志需要桌面 shell 桥接。" : undefined} aria-label="打开日志">打开日志</button>
        <button type="button" className="settings-secondary-action" onClick={onOpenDataDirectory} disabled={!onOpenDataDirectory} title={!onOpenDataDirectory ? "打开数据目录需要桌面 shell 桥接。" : undefined} aria-label="打开数据目录">打开数据目录</button>
      </div>
      {!onOpenLogs || !onOpenDataDirectory ? (
        <p className="settings-action-note settings-panel-note">打开本地目录还在等待 Tauri shell 桥接；上方路径可用于手动检查。</p>
      ) : null}
      <section className="settings-memory-card" aria-label="项目焦点">
        <div>
          <p className="settings-kicker">上下文</p>
          <h3>项目焦点</h3>
          <p>将项目目标、边界和偏好固定到新任务上下文中。</p>
        </div>
        <label className="settings-field" htmlFor="project-focus">
          <span>固定焦点</span>
          <textarea
            id="project-focus"
            aria-label="固定焦点"
            value={focusDraft}
            rows={4}
            placeholder="示例：优先保障稳定的本地编码智能体工作流，避免无关重构。"
            onChange={(event) => setFocusDraft(event.currentTarget.value)}
          />
        </label>
        <div className="settings-provider-actions">
          <button
            type="button"
            className="settings-primary-action"
            aria-label="保存项目焦点"
            onClick={() => void onSaveWorkspaceFocus?.(focusDraft)}
            disabled={workspaceFocusBusy || !onSaveWorkspaceFocus}
          >
            {workspaceFocusBusy ? "保存中..." : "保存项目焦点"}
          </button>
          <button
            type="button"
            className="settings-secondary-action"
            aria-label="清空项目焦点"
            onClick={() => {
              setFocusDraft("");
              void onSaveWorkspaceFocus?.("");
            }}
            disabled={workspaceFocusBusy || !onSaveWorkspaceFocus || !focusDraft.trim()}
          >
            清空项目焦点
          </button>
        </div>
      </section>
      <section className="settings-memory-card" aria-label="项目记忆">
        <div>
          <p className="settings-kicker">上下文</p>
          <h3>项目记忆</h3>
          <p>持久化项目笔记可加入未来模型上下文。</p>
        </div>
        <pre>{memoryPreview || "暂无项目记忆。"}</pre>
        <button
          type="button"
          className="settings-secondary-action"
          aria-label="清空项目记忆"
          onClick={() => void onClearWorkspaceMemory?.()}
          disabled={workspaceMemoryBusy || !workspaceMemorySummary?.trim() || !onClearWorkspaceMemory}
        >
          {workspaceMemoryBusy ? "清空中..." : "清空项目记忆"}
        </button>
      </section>
    </div>
  );
}
