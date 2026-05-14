import type { ReactNode } from "react";
import { type SettingsSkillConfig } from "./settingsTypes";

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

export function SkillsPanel({ skills, onRefreshSkills, onOpenSkillsFolder }: { skills: SettingsSkillConfig[]; onRefreshSkills?: () => void | Promise<void>; onOpenSkillsFolder?: () => void }) {
  return (
    <div className="settings-panel">
      <header className="settings-panel-header">
        <div>
          <p className="settings-kicker">技能库</p>
          <h2>技能</h2>
          <p>技能为本地智能体扩展专项工作流。已安装预设会从 ~/.codex/skills/ 提供给运行时。</p>
        </div>
        <div className="settings-header-actions">
          <button type="button" className="settings-secondary-action" onClick={onOpenSkillsFolder} disabled={!onOpenSkillsFolder} title={!onOpenSkillsFolder ? "技能目录打开功能尚未接入。" : undefined} aria-label="打开目录">打开目录</button>
          <button type="button" className="settings-primary-action" onClick={onRefreshSkills} disabled={!onRefreshSkills}>刷新技能</button>
        </div>
      </header>
      {!onOpenSkillsFolder ? <p className="settings-action-note settings-panel-note">打开目录还在等待桌面 shell 桥接；刷新仍会使用运行时技能注册表。</p> : null}
      <ListOrEmpty emptyTitle="暂无已安装技能" emptyText="将技能放入 ~/.codex/skills/ 后会在这里显示。">
        {skills.map((skill) => (
          <article key={skill.id} className="settings-row-card settings-skill-card">
            <span className="settings-skill-marker" aria-hidden="true" />
            <span>
              <strong>{skill.name}</strong>
              <small>{skill.description ?? "暂无描述"}</small>
              <small>{skill.path ?? "未记录路径"}</small>
            </span>
            <em>{skill.enabled ? "可用" : "不可用"}</em>
            {skill.updateAvailable ? <em>有更新</em> : null}
          </article>
        ))}
      </ListOrEmpty>
    </div>
  );
}
