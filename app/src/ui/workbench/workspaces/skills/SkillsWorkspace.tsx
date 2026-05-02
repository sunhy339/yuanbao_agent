import type { SettingsSkillConfig } from "../settings/SettingsWorkspace";
import { Button, StatusBadge } from "../../../v2/components/ui";
import type { McpServerRecord } from "@shared";
import "./skills.css";

export interface SkillsWorkspaceProps {
  skills: SettingsSkillConfig[];
  mcpServers: McpServerRecord[];
  mcpToolCount?: number;
  providerLabel: string;
  onRefreshSkills?: () => void | Promise<void>;
  onOpenMcp?: () => void;
  onOpenSettings?: () => void;
}

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

export function SkillsWorkspace({
  skills,
  mcpServers,
  mcpToolCount = 0,
  providerLabel,
  onRefreshSkills,
  onOpenMcp,
  onOpenSettings,
}: SkillsWorkspaceProps) {
  const enabledSkills = skills.filter((skill) => skill.enabled).length;
  const enabledServers = mcpServers.filter((server) => server.enabled).length;
  const availableTools = mcpToolCount;

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
      </section>

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
                  <span>{formatSkillCategory(skill.path)}</span>
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
      </section>
    </main>
  );
}
