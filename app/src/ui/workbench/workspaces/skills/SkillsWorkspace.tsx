import { useEffect, useMemo, useState, type FormEvent } from "react";
import type { SettingsSkillConfig } from "../settings/SettingsWorkspace";
import { Button, StatusBadge } from "../../../v2/components/ui";
import type { McpServerRecord } from "@shared";
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
    title: "Planner",
    focus: "Break down tasks, acceptance criteria, and runtime checkpoints.",
    status: "ready",
  },
  {
    id: "coder",
    title: "Builder",
    focus: "Apply focused patches and keep changes inside the active workspace.",
    status: "ready",
  },
  {
    id: "reviewer",
    title: "Reviewer",
    focus: "Inspect diffs, verification gaps, and approval-sensitive actions.",
    status: "standby",
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
  return skill.toolWhitelist?.length ? skill.toolWhitelist : ["No tool allowlist published"];
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
          <p className="yb-kicker">Agent Squadron</p>
          <h1 id="skills-title">Agent Skills</h1>
          <p>Coordinate skill presets, agent lanes, and MCP tool capacity for the local runtime.</p>
        </div>
        <dl>
          <div>
            <dt>Skills</dt>
            <dd>{enabledSkills}/{skills.length}</dd>
          </div>
          <div>
            <dt>MCP</dt>
            <dd>{enabledServers}/{mcpServers.length}</dd>
          </div>
          <div>
            <dt>Tools</dt>
            <dd>{availableTools}</dd>
          </div>
          <div>
            <dt>Provider</dt>
            <dd>{providerLabel}</dd>
          </div>
        </dl>
      </section>

      <section className="skills-action-row" aria-label="Skills actions">
        <Button variant="primary" onClick={onRefreshSkills} disabled={!onRefreshSkills} disabledReason="Skill refresh is not wired">
          Refresh skills
        </Button>
        <Button variant="secondary" onClick={onOpenMcp} disabled={!onOpenMcp} disabledReason="MCP center is not available">
          Manage MCP
        </Button>
        <Button variant="ghost" onClick={onOpenSettings} disabled={!onOpenSettings} disabledReason="Settings are not available">
          Runtime settings
        </Button>
        <Button
          variant="secondary"
          onClick={openCreateEditor}
          disabled={!onCreateSkill}
          disabledReason="Custom skill persistence is not available"
        >
          New custom skill
        </Button>
      </section>

      {editorMode ? (
        <section className="skills-editor" aria-label={editorMode === "create" ? "Create custom skill" : "Edit custom skill"}>
          <header>
            <div>
              <p className="yb-kicker">{editorMode === "create" ? "Custom preset" : "Edit preset"}</p>
              <h2>{editorMode === "create" ? "New custom skill" : `Edit ${selectedSkill?.name ?? "skill"}`}</h2>
            </div>
            <Button
              variant="ghost"
              onClick={() => {
                setEditorMode(null);
                setDraft(emptySkillDraft);
              }}
              disabled={editorBusy}
            >
              Cancel
            </Button>
          </header>
          <form className="skills-editor-form" onSubmit={(event) => void handleSkillSubmit(event)}>
            <label>
              <span>Name</span>
              <input
                value={draft.name}
                onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
                placeholder="Research reviewer"
                required
              />
            </label>
            <label>
              <span>Category</span>
              <input
                value={draft.category}
                onChange={(event) => setDraft((current) => ({ ...current, category: event.target.value }))}
                placeholder="custom"
              />
            </label>
            <label className="skills-editor-wide">
              <span>Description</span>
              <input
                value={draft.description}
                onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
                placeholder="Summarizes source material and checks claims."
              />
            </label>
            <label className="skills-editor-wide">
              <span>System prompt</span>
              <textarea
                value={draft.systemPrompt}
                onChange={(event) => setDraft((current) => ({ ...current, systemPrompt: event.target.value }))}
                rows={5}
                placeholder="Describe how the agent should behave when this skill is selected."
              />
            </label>
            <label className="skills-editor-wide">
              <span>Tool allowlist</span>
              <textarea
                value={draft.toolWhitelist}
                onChange={(event) => setDraft((current) => ({ ...current, toolWhitelist: event.target.value }))}
                rows={3}
                placeholder="One tool per line or comma-separated"
              />
            </label>
            <div className="skills-editor-actions">
              <Button
                variant="primary"
                type="submit"
                disabled={!draft.name.trim() || editorBusy}
                loading={editorBusy}
              >
                {editorMode === "create" ? "Create skill" : "Save skill"}
              </Button>
            </div>
          </form>
        </section>
      ) : null}

      <section className="skills-grid">
        <div className="skills-agent-board" aria-label="Agent lanes">
          <header>
            <p className="yb-kicker">Agents</p>
            <h2>Execution lanes</h2>
          </header>
          <div className="skills-agent-lanes">
            {agentLanes.map((lane) => (
              <article key={lane.id} className="skills-agent-card">
                <div>
                  <span className="skills-agent-mark" aria-hidden="true">{lane.title.slice(0, 1)}</span>
                  <StatusBadge label={lane.status} tone={lane.status === "ready" ? "success" : "neutral"} compact />
                </div>
                <h3>{lane.title}</h3>
                <p>{lane.focus}</p>
              </article>
            ))}
          </div>
        </div>

        <aside className="skills-runtime-panel" aria-label="Runtime coverage">
          <p className="yb-kicker">Coverage</p>
          <h2>Runtime capacity</h2>
          <ul>
            <li>
              <strong>{enabledSkills ? "Skill routing ready" : "No skill presets"}</strong>
              <span>{enabledSkills ? `${enabledSkills} installed presets can shape agent behavior.` : "Refresh skills or add local presets."}</span>
            </li>
            <li>
              <strong>{enabledServers ? "MCP attached" : "MCP offline"}</strong>
              <span>{enabledServers ? `${availableTools} tools are advertised by enabled servers.` : "Add servers in MCP Center."}</span>
            </li>
            <li>
              <strong>Approvals remain explicit</strong>
              <span>Skill choices do not bypass command, patch, or browser approval policy.</span>
            </li>
          </ul>
        </aside>
      </section>

      <section className="skills-library" aria-label="Skill library">
        <header>
          <div>
            <p className="yb-kicker">Library</p>
            <h2>Installed presets</h2>
          </div>
          <StatusBadge label={`${skills.length} total`} tone="primary" />
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
                  <StatusBadge label={skill.enabled ? "available" : "unavailable"} tone={skill.enabled ? "success" : "neutral"} compact />
                  <StatusBadge label={skill.isBuiltin ? "built-in" : "custom"} tone={skill.isBuiltin ? "primary" : "neutral"} compact />
                  <span>{formatSkillCategory(skill.path)}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    pressed={selectedSkillId === skill.id}
                    onClick={() => setSelectedSkillId((current) => current === skill.id ? null : skill.id)}
                  >
                    Inspect
                  </Button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="skills-empty">
            <strong>No skill presets loaded</strong>
            <span>Use Refresh skills after adding presets to the local skill registry.</span>
          </div>
        )}
        {selectedSkill ? (
          <aside className="skills-inspector" aria-label={`${selectedSkill.name} skill details`}>
            <header>
              <div>
                <p className="yb-kicker">Inspect</p>
                <h3>{selectedSkill.name}</h3>
              </div>
              <div className="skills-inspector-actions">
                <StatusBadge label={selectedSkill.isBuiltin ? "built-in preset" : "custom preset"} tone={selectedSkill.isBuiltin ? "primary" : "neutral"} compact />
                {selectedSkillIsCustom ? (
                  <>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => openEditEditor(selectedSkill)}
                      disabled={!onUpdateSkill || busySkillId === selectedSkill.id}
                      loading={busySkillId === selectedSkill.id}
                    >
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => void onDeleteSkill?.(selectedSkill.id)}
                      disabled={!onDeleteSkill || busySkillId === selectedSkill.id}
                      loading={busySkillId === selectedSkill.id}
                    >
                      Delete
                    </Button>
                  </>
                ) : null}
              </div>
            </header>
            {!selectedSkillIsCustom ? (
              <p className="skills-readonly-note">Built-in presets are read-only; create a custom skill when you need editable behavior.</p>
            ) : null}
            <dl>
              <div>
                <dt>Category</dt>
                <dd>{formatSkillCategory(selectedSkill.path)}</dd>
              </div>
              <div>
                <dt>Activation</dt>
                <dd>{selectedSkill.enabled ? "available in runtime registry" : "not available"}</dd>
              </div>
            </dl>
            <section>
              <h4>Prompt</h4>
              <pre>{selectedSkill.systemPrompt?.trim() || "No prompt text published by the runtime for this preset."}</pre>
            </section>
            <section>
              <h4>Tool allowlist</h4>
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
