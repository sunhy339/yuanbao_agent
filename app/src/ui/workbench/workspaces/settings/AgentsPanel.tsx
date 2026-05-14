import type { ReactNode } from "react";
import { type SettingsAgentConfig } from "./settingsTypes";

function ListOrEmpty({ children, emptyTitle, emptyText }: { children: ReactNode; emptyTitle: string; emptyText: string }) {
  const childArray = Array.isArray(children) ? children : [children];
  if (childArray.filter(Boolean).length === 0) {
    return (
      <div className="settings-empty-state">
        <span aria-hidden="true">·</span>
        <strong>{emptyTitle}</strong>
        <small>{emptyText}</small>
      </div>
    );
  }
  return <div className="settings-form-stack">{children}</div>;
}

export function AgentsPanel({ agents, onAgentToggle, onAddAgent }: { agents: SettingsAgentConfig[]; onAgentToggle?: (agentId: string, enabled: boolean) => void; onAddAgent?: () => void }) {
  return (
    <div className="settings-panel">
      <header className="settings-panel-header">
        <div>
          <p className="settings-kicker">智能体队列</p>
          <h2>智能体</h2>
          <p>管理常驻智能体、工作目录和默认权限策略。</p>
        </div>
        <button type="button" className="settings-primary-action" onClick={onAddAgent} disabled={!onAddAgent} title={!onAddAgent ? "智能体配置管理尚未可用。" : undefined} aria-label="添加智能体">添加智能体</button>
      </header>
      {!onAddAgent || !onAgentToggle ? (
        <p className="settings-action-note settings-panel-note">运行时智能体管理定义完成前，智能体配置暂时只读。</p>
      ) : null}
      <ListOrEmpty emptyTitle="暂无智能体" emptyText="运行时集成后，常驻智能体会显示在这里。">
        {agents.map((agent) => (
          <label key={agent.id} className="settings-row-card">
            <input type="checkbox" checked={agent.enabled} disabled={!onAgentToggle} onChange={(event) => onAgentToggle?.(agent.id, event.currentTarget.checked)} />
            <span>
              <strong>{agent.name}</strong>
              <small>{agent.description ?? "暂无描述"}</small>
              <small>{agent.cwd ?? "未设置工作目录"}</small>
            </span>
            <em>{agent.permissionMode ?? "继承默认"}</em>
          </label>
        ))}
      </ListOrEmpty>
    </div>
  );
}
