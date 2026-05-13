import { useEffect, useMemo, useState, type FormEvent } from "react";
import type { SettingsSkillConfig } from "../settings/SettingsWorkspace";
import { Button, StatusBadge } from "../../../v2/components/ui";
import type { McpServerRecord } from "@shared";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import "./skills.css";

export interface SkillDraft {
  name: string;
  description: string;
  systemPrompt: string;
  toolWhitelist: string;
  category: string;
}

export interface SkillsWorkspaceProps {
  skills: SettingsSkillConfig[];
  mcpServers: McpServerRecord[];
  mcpToolCount?: number;
  providerLabel: string;
  busySkillId?: string | null;
  onRefreshSkills?: () => void | Promise<void>;
  onOpenMcp?: () => void;
  onOpenSettings?: () => void;
  onCreateSkill?: (draft: SkillDraft) => void | Promise<void>;
  onUpdateSkill?: (skillId: string, draft: SkillDraft) => void | Promise<void>;
  onDeleteSkill?: (skillId: string) => void | Promise<void>;
  onImportSkills?: (filePath: string) => void | Promise<void>;
  onOpenSkillsFolder?: () => void | Promise<void>;
}

const emptySkillDraft: SkillDraft = {
  name: "",
  description: "",
  systemPrompt: "",
  toolWhitelist: "",
  category: "custom",
};

const agentLanes = [
  {
    id: "planner",
    title: "规划",
    focus: "拆解任务、验收标准和运行时检查点。",
    status: "就绪",
  },
  {
    id: "coder",
    title: "构建",
    focus: "应用聚焦补丁，并将改动控制在当前工作区内。",
    status: "就绪",
  },
  {
    id: "reviewer",
    title: "审查",
    focus: "检查 diff、验证缺口和需要审批的敏感动作。",
    status: "待命",
  },
];

function formatSkillCategory(path?: string): string {
  if (!path) {
    return "custom";
  }
  if (path.startsWith("category:")) {
    return path.slice("category:".length);
  }
  return "local";
}

function formatToolList(skill: SettingsSkillConfig): string[] {
  return skill.toolWhitelist?.length ? skill.toolWhitelist : ["未发布工具白名单"];
}

function draftFromSkill(skill: SettingsSkillConfig): SkillDraft {
  return {
    name: skill.name,
    description: skill.description ?? "",
    systemPrompt: skill.systemPrompt ?? "",
    toolWhitelist: skill.toolWhitelist?.join("\n") ?? "",
    category: formatSkillCategory(skill.path),
  };
}

export function SkillsWorkspace({
  skills,
  mcpServers,
  mcpToolCount = 0,
  providerLabel,
  busySkillId = null,
  onRefreshSkills,
  onOpenMcp,
  onOpenSettings,
  onCreateSkill,
  onUpdateSkill,
  onDeleteSkill,
  onImportSkills,
  onOpenSkillsFolder,
}: SkillsWorkspaceProps) {
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null);
  const [editorMode, setEditorMode] = useState<"create" | "edit" | null>(null);
  const [draft, setDraft] = useState<SkillDraft>(emptySkillDraft);
  const enabledSkills = skills.filter((skill) => skill.enabled).length;
  const enabledServers = mcpServers.filter((server) => server.enabled).length;
  const availableTools = mcpToolCount;
  const selectedSkill = useMemo(
    () => skills.find((skill) => skill.id === selectedSkillId) ?? null,
    [selectedSkillId, skills],
  );

  useEffect(() => {
    if (selectedSkillId && !skills.some((skill) => skill.id === selectedSkillId)) {
      setSelectedSkillId(null);
      setEditorMode(null);
    }
  }, [selectedSkillId, skills]);

  async function handleSkillSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.name.trim()) {
      return;
    }
    if (editorMode === "edit" && selectedSkill && onUpdateSkill) {
      await onUpdateSkill(selectedSkill.id, draft);
      setEditorMode(null);
      return;
    }
    if (editorMode === "create" && onCreateSkill) {
      await onCreateSkill(draft);
      setDraft(emptySkillDraft);
      setEditorMode(null);
    }
  }

  function openCreateEditor() {
    setSelectedSkillId(null);
    setDraft(emptySkillDraft);
    setEditorMode("create");
  }

  function normalizeDialogPath(selected: string | string[] | null): string | null {
    return Array.isArray(selected) ? selected[0] ?? null : selected;
  }

  async function handleImportFileClick() {
    const selected = await openDialog({
      multiple: false,
      filters: [{ name: "Skill package", extensions: ["json", "zip"] }],
    });
    const selectedPath = normalizeDialogPath(selected);
    if (!selectedPath) {
      return;
    }
    await onImportSkills?.(selectedPath);
  }

  async function handleImportFolderClick() {
    const selected = await openDialog({
      directory: true,
      multiple: false,
    });
    const selectedPath = normalizeDialogPath(selected);
    if (!selectedPath) {
      return;
    }
    await onImportSkills?.(selectedPath);
  }

  function openEditEditor(skill: SettingsSkillConfig) {
    setSelectedSkillId(skill.id);
    setDraft(draftFromSkill(skill));
    setEditorMode("edit");
  }

  const selectedSkillIsCustom = Boolean(selectedSkill && !selectedSkill.isBuiltin);
  const editorBusy = busySkillId === "create" || Boolean(editorMode === "edit" && selectedSkill && busySkillId === selectedSkill.id);

  return (
    <main className="skills-workspace" aria-labelledby="skills-title">
      <section className="skills-command-strip">
        <div>
          <p className="yb-kicker">智能体编队</p>
          <h1 id="skills-title">智能体技能</h1>
          <p>协调本地运行时的技能预设、智能体通道和 MCP 工具容量。</p>
        </div>
        <dl>
          <div>
            <dt>技能</dt>
            <dd>{enabledSkills}/{skills.length}</dd>
          </div>
          <div>
            <dt>MCP</dt>
            <dd>{enabledServers}/{mcpServers.length}</dd>
          </div>
          <div>
            <dt>工具</dt>
            <dd>{availableTools}</dd>
          </div>
          <div>
            <dt>供应商</dt>
            <dd>{providerLabel}</dd>
          </div>
        </dl>
      </section>

      <section className="skills-action-row" aria-label="技能操作">
        <Button variant="primary" onClick={onRefreshSkills} disabled={!onRefreshSkills} disabledReason="技能刷新尚未接入">
          刷新技能
        </Button>
        <Button variant="secondary" onClick={onOpenMcp} disabled={!onOpenMcp} disabledReason="MCP 中心不可用">
          管理 MCP
        </Button>
        <Button variant="ghost" onClick={onOpenSettings} disabled={!onOpenSettings} disabledReason="设置不可用">
          运行时设置
        </Button>
        <Button
          variant="secondary"
          onClick={openCreateEditor}
          disabled={!onCreateSkill}
          disabledReason="自定义技能持久化尚不可用"
        >
          新建自定义技能
        </Button>
        <Button
          variant="secondary"
          onClick={() => void handleImportFileClick()}
          disabled={!onImportSkills || busySkillId === "import"}
          loading={busySkillId === "import"}
          disabledReason="技能导入需要桌面运行时"
        >
          导入 JSON/ZIP
        </Button>
        <Button
          variant="secondary"
          onClick={() => void handleImportFolderClick()}
          disabled={!onImportSkills || busySkillId === "import"}
          loading={busySkillId === "import"}
          disabledReason="技能导入需要桌面运行时"
        >
          导入文件夹
        </Button>
        <Button
          variant="secondary"
          onClick={() => void onOpenSkillsFolder?.()}
          disabled={!onOpenSkillsFolder}
          disabledReason="打开技能目录需要桌面 shell 桥接"
        >
          打开目录
        </Button>
      </section>

      {editorMode ? (
        <section className="skills-editor" aria-label={editorMode === "create" ? "创建自定义技能" : "编辑自定义技能"}>
          <header>
            <div>
              <p className="yb-kicker">{editorMode === "create" ? "自定义预设" : "编辑预设"}</p>
              <h2>{editorMode === "create" ? "新建自定义技能" : `编辑 ${selectedSkill?.name ?? "技能"}`}</h2>
            </div>
            <Button
              variant="ghost"
              onClick={() => {
                setEditorMode(null);
                setDraft(emptySkillDraft);
              }}
              disabled={editorBusy}
            >
              取消
            </Button>
          </header>
          <form className="skills-editor-form" onSubmit={(event) => void handleSkillSubmit(event)}>
            <label>
              <span>名称</span>
              <input
                value={draft.name}
                onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
                placeholder="研究审阅器"
                required
              />
            </label>
            <label>
              <span>分类</span>
              <input
                value={draft.category}
                onChange={(event) => setDraft((current) => ({ ...current, category: event.target.value }))}
                placeholder="custom"
              />
            </label>
            <label className="skills-editor-wide">
              <span>描述</span>
              <input
                value={draft.description}
                onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
                placeholder="总结资料并检查关键主张。"
              />
            </label>
            <label className="skills-editor-wide">
              <span>系统提示词</span>
              <textarea
                value={draft.systemPrompt}
                onChange={(event) => setDraft((current) => ({ ...current, systemPrompt: event.target.value }))}
                rows={5}
                placeholder="描述选择该技能后智能体应如何行动。"
              />
            </label>
            <label className="skills-editor-wide">
              <span>工具白名单</span>
              <textarea
                value={draft.toolWhitelist}
                onChange={(event) => setDraft((current) => ({ ...current, toolWhitelist: event.target.value }))}
                rows={3}
                placeholder="每行一个工具，或用逗号分隔"
              />
            </label>
            <div className="skills-editor-actions">
              <Button
                variant="primary"
                type="submit"
                disabled={!draft.name.trim() || editorBusy}
                loading={editorBusy}
              >
                {editorMode === "create" ? "创建技能" : "保存技能"}
              </Button>
            </div>
          </form>
        </section>
      ) : null}

      <section className="skills-grid">
        <div className="skills-agent-board" aria-label="智能体通道">
          <header>
            <p className="yb-kicker">智能体</p>
            <h2>执行通道</h2>
          </header>
          <div className="skills-agent-lanes">
            {agentLanes.map((lane) => (
              <article key={lane.id} className="skills-agent-card">
                <div>
                  <span className="skills-agent-mark" aria-hidden="true">{lane.title.slice(0, 1)}</span>
                  <StatusBadge label={lane.status} tone={lane.status === "就绪" ? "success" : "neutral"} compact />
                </div>
                <h3>{lane.title}</h3>
                <p>{lane.focus}</p>
              </article>
            ))}
          </div>
        </div>

        <aside className="skills-runtime-panel" aria-label="运行时覆盖">
          <p className="yb-kicker">覆盖范围</p>
          <h2>运行时容量</h2>
          <ul>
            <li>
              <strong>{enabledSkills ? "技能路由已就绪" : "暂无技能预设"}</strong>
              <span>{enabledSkills ? `${enabledSkills} 个已安装预设可影响智能体行为。` : "刷新技能或添加本地预设。"}</span>
            </li>
            <li>
              <strong>{enabledServers ? "MCP 已接入" : "MCP 离线"}</strong>
              <span>{enabledServers ? `已启用服务器发布了 ${availableTools} 个工具。` : "在 MCP 中心添加服务器。"}</span>
            </li>
            <li>
              <strong>审批仍保持显式</strong>
              <span>选择技能不会绕过命令、补丁或浏览器审批策略。</span>
            </li>
          </ul>
        </aside>
      </section>

      <section className="skills-library" aria-label="技能库">
        <header>
          <div>
            <p className="yb-kicker">技能库</p>
            <h2>已安装预设</h2>
          </div>
          <StatusBadge label={`共 ${skills.length} 个`} tone="primary" />
        </header>
        {skills.length ? (
          <div className="skills-list">
            {skills.map((skill) => (
              <article key={skill.id} className="skills-skill-row" data-enabled={skill.enabled}>
                <div>
                  <strong>{skill.name}</strong>
                  <span>{skill.description}</span>
                </div>
                <div className="skills-skill-meta">
                  <StatusBadge label={skill.enabled ? "可用" : "不可用"} tone={skill.enabled ? "success" : "neutral"} compact />
                  <StatusBadge label={skill.isBuiltin ? "内置" : "自定义"} tone={skill.isBuiltin ? "primary" : "neutral"} compact />
                  <span>{formatSkillCategory(skill.path)}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    pressed={selectedSkillId === skill.id}
                    onClick={() => setSelectedSkillId((current) => current === skill.id ? null : skill.id)}
                  >
                    检查
                  </Button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="skills-empty">
            <strong>未加载技能预设</strong>
            <span>将预设加入本地技能注册表后，使用刷新技能加载。</span>
          </div>
        )}
        {selectedSkill ? (
          <aside className="skills-inspector" aria-label={`${selectedSkill.name} 技能详情`}>
            <header>
              <div>
                <p className="yb-kicker">检查</p>
                <h3>{selectedSkill.name}</h3>
              </div>
              <div className="skills-inspector-actions">
                <StatusBadge label={selectedSkill.isBuiltin ? "内置预设" : "自定义预设"} tone={selectedSkill.isBuiltin ? "primary" : "neutral"} compact />
                {selectedSkillIsCustom ? (
                  <>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => openEditEditor(selectedSkill)}
                      disabled={!onUpdateSkill || busySkillId === selectedSkill.id}
                      loading={busySkillId === selectedSkill.id}
                    >
                      编辑
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => void onDeleteSkill?.(selectedSkill.id)}
                      disabled={!onDeleteSkill || busySkillId === selectedSkill.id}
                      loading={busySkillId === selectedSkill.id}
                    >
                      删除
                    </Button>
                  </>
                ) : null}
              </div>
            </header>
            {!selectedSkillIsCustom ? (
              <p className="skills-readonly-note">内置预设为只读；需要可编辑行为时，请创建自定义技能。</p>
            ) : null}
            <dl>
              <div>
                <dt>分类</dt>
                <dd>{formatSkillCategory(selectedSkill.path)}</dd>
              </div>
              <div>
                <dt>激活状态</dt>
                <dd>{selectedSkill.enabled ? "可在运行时注册表中使用" : "不可用"}</dd>
              </div>
            </dl>
            <section>
              <h4>提示词</h4>
              <pre>{selectedSkill.systemPrompt?.trim() || "运行时未为该预设发布提示词文本。"}</pre>
            </section>
            <section>
              <h4>工具白名单</h4>
              <div className="skills-tool-list">
                {formatToolList(selectedSkill).map((tool) => (
                  <span key={tool}>{tool}</span>
                ))}
              </div>
            </section>
          </aside>
        ) : null}
      </section>
    </main>
  );
}
