import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import type {
  AgentProfilePreviewToolsResult,
  AgentProfileRecord,
  AgentProfileValidateResult,
} from "@shared";
import {
  type SettingsAgentConfig,
  type SettingsAgentDraft,
  type SettingsAgentFeedback,
} from "./settingsTypes";

type AgentRole = NonNullable<SettingsAgentDraft["role"]>;

interface AgentDraftForm {
  id: string;
  name: string;
  description: string;
  role: AgentRole;
  cwd: string;
  enabled: boolean;
  permissionMode: NonNullable<SettingsAgentDraft["permissionMode"]>;
  providerProfileId: string;
  model: string;
  skillIds: string;
  mcpServerIds: string;
  allowedTools: string;
  deniedTools: string;
  requiresApproval: string;
  systemPrompt: string;
}

const roleOptions: Array<{ value: AgentRole; label: string }> = [
  { value: "planner", label: "Planner" },
  { value: "builder", label: "Builder" },
  { value: "reviewer", label: "Reviewer" },
  { value: "researcher", label: "Researcher" },
  { value: "custom", label: "Custom" },
];

const permissionOptions: Array<{ value: AgentDraftForm["permissionMode"]; label: string }> = [
  { value: "ask", label: "Ask" },
  { value: "plan", label: "Plan" },
  { value: "auto", label: "Auto" },
  { value: "skip", label: "Skip" },
];

function updateDraftValue<K extends keyof AgentDraftForm>(
  setDraft: Dispatch<SetStateAction<AgentDraftForm>>,
  key: K,
  value: AgentDraftForm[K],
) {
  setDraft((current) => ({ ...current, [key]: value }));
}

function splitList(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function joinList(value?: string[]): string {
  return (value ?? []).join("\n");
}

function draftFromAgent(agent?: SettingsAgentConfig | null): AgentDraftForm {
  return {
    id: agent?.id ?? "",
    name: agent?.name ?? "",
    description: agent?.description ?? "",
    role: (agent?.role ?? "custom") as AgentRole,
    cwd: agent?.cwd ?? "",
    enabled: agent?.enabled ?? true,
    permissionMode: (agent?.permissionMode as AgentDraftForm["permissionMode"]) ?? "ask",
    providerProfileId: agent?.providerProfileId ?? "",
    model: agent?.model ?? "",
    skillIds: joinList(agent?.skillIds),
    mcpServerIds: joinList(agent?.mcpServerIds),
    allowedTools: joinList(agent?.toolPolicy?.allowedTools),
    deniedTools: joinList(agent?.toolPolicy?.deniedTools),
    requiresApproval: joinList(agent?.toolPolicy?.requiresApproval),
    systemPrompt: agent?.systemPrompt ?? "",
  };
}

function payloadFromDraft(draft: AgentDraftForm): SettingsAgentDraft {
  const toolPolicy = {
    allowedTools: splitList(draft.allowedTools),
    deniedTools: splitList(draft.deniedTools),
    requiresApproval: splitList(draft.requiresApproval),
  };
  return {
    id: draft.id.trim() || undefined,
    name: draft.name.trim(),
    description: draft.description.trim() || undefined,
    role: draft.role,
    cwd: draft.cwd.trim() || undefined,
    enabled: draft.enabled,
    permissionMode: draft.permissionMode,
    providerProfileId: draft.providerProfileId.trim() || undefined,
    model: draft.model.trim() || undefined,
    skillIds: splitList(draft.skillIds),
    mcpServerIds: splitList(draft.mcpServerIds),
    toolPolicy:
      toolPolicy.allowedTools.length || toolPolicy.deniedTools.length || toolPolicy.requiresApproval.length
        ? toolPolicy
        : undefined,
    systemPrompt: draft.systemPrompt.trim() || undefined,
  };
}

function runtimeRoleForPreview(role: AgentRole): string {
  if (role === "reviewer") return "reviewer";
  if (role === "planner") return "planner";
  return "worker";
}

function ListOrEmpty({
  agents,
  onSelect,
  selectedId,
  onToggle,
  busyId,
}: {
  agents: SettingsAgentConfig[];
  onSelect(agent: SettingsAgentConfig): void;
  selectedId?: string | null;
  onToggle?: (agentId: string, enabled: boolean) => void;
  busyId?: string | null;
}) {
  if (agents.length === 0) {
    return (
      <div className="settings-empty-state">
        <span aria-hidden="true">A</span>
        <strong>No agent profiles</strong>
        <small>Create a profile to bind role, tools, skills, and model defaults.</small>
      </div>
    );
  }

  return (
    <div className="settings-form-stack">
      {agents.map((agent) => (
        <article
          key={agent.id}
          className={selectedId === agent.id ? "settings-row-card settings-agent-row is-selected" : "settings-row-card settings-agent-row"}
        >
          <input
            aria-label={`Enable ${agent.name}`}
            type="checkbox"
            checked={agent.enabled}
            disabled={!onToggle || busyId === agent.id}
            onChange={(event) => onToggle?.(agent.id, event.currentTarget.checked)}
          />
          <button type="button" className="settings-agent-select" onClick={() => onSelect(agent)}>
            <span>
              <strong>{agent.name}</strong>
              <small>{agent.description || agent.systemPrompt || "No description"}</small>
              <small>{agent.cwd || "Workspace default cwd"}</small>
            </span>
          </button>
          <em>{agent.role ?? "custom"} / {agent.permissionMode ?? "ask"}</em>
        </article>
      ))}
    </div>
  );
}

function ToolPreview({ preview }: { preview: AgentProfilePreviewToolsResult | null }) {
  if (!preview) {
    return (
      <div className="settings-agent-preview" aria-live="polite">
        <strong>Tool preview</strong>
        <small>Run preview to see what this profile would expose to a provider turn.</small>
      </div>
    );
  }
  return (
    <div className="settings-agent-preview" aria-live="polite">
      <strong>Tool preview</strong>
      <div>
        <span className="settings-status-pill is-pass">{preview.allowedTools.length} allowed</span>
        <span className={preview.deniedTools.length ? "settings-status-pill is-fail" : "settings-status-pill"}>
          {preview.deniedTools.length} denied
        </span>
      </div>
      <small>Allowed: {preview.allowedTools.slice(0, 10).join(", ") || "none"}</small>
      <small>Denied: {preview.deniedTools.slice(0, 10).join(", ") || "none"}</small>
    </div>
  );
}

export function AgentsPanel({
  agents,
  agentBusyId,
  agentFeedback,
  onRefreshAgents,
  onAgentToggle,
  onAddAgent,
  onUpdateAgent,
  onDeleteAgent,
  onValidateAgent,
  onPreviewAgentTools,
}: {
  agents: SettingsAgentConfig[];
  agentBusyId?: string | null;
  agentFeedback?: SettingsAgentFeedback | null;
  onRefreshAgents?: () => void | Promise<void>;
  onAgentToggle?: (agentId: string, enabled: boolean) => void | Promise<void>;
  onAddAgent?: (payload: SettingsAgentDraft) => void | Promise<void>;
  onUpdateAgent?: (agentId: string, payload: Partial<SettingsAgentDraft>) => void | Promise<void>;
  onDeleteAgent?: (agentId: string) => void | Promise<void>;
  onValidateAgent?: (payload: { name?: string; role?: string; toolPolicy?: AgentProfileRecord["toolPolicy"] }) => AgentProfileValidateResult | Promise<AgentProfileValidateResult>;
  onPreviewAgentTools?: (payload: { role?: string; permissionMode?: string; toolPolicy?: AgentProfileRecord["toolPolicy"] }) => AgentProfilePreviewToolsResult | Promise<AgentProfilePreviewToolsResult>;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(agents[0]?.id ?? null);
  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === selectedId) ?? null,
    [agents, selectedId],
  );
  const fallbackAgent = agents[0] ?? null;
  const [draft, setDraft] = useState<AgentDraftForm>(() => draftFromAgent(selectedAgent));
  const [mode, setMode] = useState<"create" | "edit">(selectedAgent ? "edit" : "create");
  const [validation, setValidation] = useState<AgentProfileValidateResult | null>(null);
  const [preview, setPreview] = useState<AgentProfilePreviewToolsResult | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [manualCreate, setManualCreate] = useState(false);

  useEffect(() => {
    if (manualCreate) {
      return;
    }
    const nextAgent = selectedAgent ?? fallbackAgent;
    if (!nextAgent) {
      if (mode === "edit") {
        setSelectedId(null);
        setDraft(draftFromAgent(null));
        setMode("create");
        setValidation(null);
        setPreview(null);
        setLocalError(null);
      }
      return;
    }
    if (selectedId !== nextAgent.id) {
      setSelectedId(nextAgent.id);
    }
    setDraft(draftFromAgent(nextAgent));
    setMode("edit");
    setValidation(null);
    setPreview(null);
    setLocalError(null);
    setManualCreate(false);
  }, [fallbackAgent, manualCreate, mode, selectedAgent, selectedId]);

  function selectAgent(agent: SettingsAgentConfig) {
    setSelectedId(agent.id);
    setDraft(draftFromAgent(agent));
    setMode("edit");
    setManualCreate(false);
    setValidation(null);
    setPreview(null);
    setLocalError(null);
  }

  function startCreate() {
    setSelectedId(null);
    setDraft(draftFromAgent(null));
    setMode("create");
    setManualCreate(true);
    setValidation(null);
    setPreview(null);
    setLocalError(null);
  }

  async function validateCurrent(): Promise<AgentProfileValidateResult> {
    const payload = payloadFromDraft(draft);
    const result = onValidateAgent
      ? await onValidateAgent({
          name: payload.name,
          role: payload.role,
          toolPolicy: payload.toolPolicy,
        })
      : { valid: Boolean(payload.name), errors: payload.name ? [] : ["name is required"] };
    setValidation(result);
    return result;
  }

  async function submitForm() {
    setLocalError(null);
    const validationResult = await validateCurrent();
    if (!validationResult.valid) {
      setLocalError(validationResult.errors.join("; "));
      return;
    }
    const payload = payloadFromDraft(draft);
    if (mode === "edit" && selectedAgent) {
      await onUpdateAgent?.(selectedAgent.id, payload);
      return;
    }
    await onAddAgent?.(payload);
    startCreate();
  }

  async function previewTools() {
    setLocalError(null);
    const payload = payloadFromDraft(draft);
    const result = onPreviewAgentTools
      ? await onPreviewAgentTools({
          role: runtimeRoleForPreview(payload.role ?? "custom"),
          permissionMode: payload.permissionMode,
          toolPolicy: payload.toolPolicy,
        })
      : { allowedTools: [], deniedTools: [] };
    setPreview(result);
  }

  const busy = Boolean(agentBusyId);
  const canPersist = Boolean(onAddAgent || onUpdateAgent);

  return (
    <div className="settings-panel">
      <header className="settings-panel-header">
        <div>
          <p className="settings-kicker">Profiles</p>
          <h2>Agent profiles</h2>
          <p>Manage runtime-backed profiles for role, model, skills, MCP servers, and per-turn tool policy.</p>
        </div>
        <div className="settings-header-actions">
          <button type="button" className="settings-secondary-action" onClick={() => void onRefreshAgents?.()} disabled={!onRefreshAgents || agentBusyId === "refresh"}>
            Refresh
          </button>
          <button type="button" className="settings-primary-action" onClick={startCreate} disabled={!onAddAgent}>
            New profile
          </button>
        </div>
      </header>

      {agentFeedback ? (
        <p className={`settings-action-note settings-agent-feedback is-${agentFeedback.tone}`} role="status">
          {agentFeedback.message}
        </p>
      ) : null}

      <div className="settings-agent-grid">
        <ListOrEmpty
          agents={agents}
          selectedId={selectedId}
          onSelect={selectAgent}
          onToggle={onAgentToggle}
          busyId={agentBusyId}
        />

        <form
          className="settings-agent-editor"
          onSubmit={(event) => {
            event.preventDefault();
            void submitForm();
          }}
        >
          <div className="settings-agent-editor-header">
            <div>
              <p className="settings-kicker">{mode === "edit" ? "Edit" : "Create"}</p>
              <h3>{mode === "edit" ? selectedAgent?.name ?? "Profile" : "New profile"}</h3>
            </div>
            {selectedAgent?.isBuiltin ? <span className="settings-status-pill">Built-in</span> : null}
          </div>

          <div className="settings-provider-form">
            <label className="settings-field">
              Name
              <input
                value={draft.name}
                onChange={(event) => updateDraftValue(setDraft, "name", event.currentTarget.value)}
                placeholder="Code reviewer"
              />
            </label>
            <label className="settings-field">
              Id
              <input
                value={draft.id}
                disabled={mode === "edit"}
                onChange={(event) => updateDraftValue(setDraft, "id", event.currentTarget.value)}
                placeholder="Optional stable id"
              />
            </label>
            <label className="settings-field">
              Role
              <select value={draft.role} onChange={(event) => updateDraftValue(setDraft, "role", event.currentTarget.value as AgentRole)}>
                {roleOptions.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
            <label className="settings-field">
              Permission mode
              <select
                value={draft.permissionMode}
                onChange={(event) => updateDraftValue(setDraft, "permissionMode", event.currentTarget.value as AgentDraftForm["permissionMode"])}
              >
                {permissionOptions.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
            <label className="settings-field settings-form-wide">
              Description
              <input
                value={draft.description}
                onChange={(event) => updateDraftValue(setDraft, "description", event.currentTarget.value)}
                placeholder="When this profile should be used"
              />
            </label>
            <label className="settings-field">
              Cwd
              <input
                value={draft.cwd}
                onChange={(event) => updateDraftValue(setDraft, "cwd", event.currentTarget.value)}
                placeholder="Workspace default"
              />
            </label>
            <label className="settings-field">
              Model
              <input
                value={draft.model}
                onChange={(event) => updateDraftValue(setDraft, "model", event.currentTarget.value)}
                placeholder="Optional model override"
              />
            </label>
            <label className="settings-field">
              Provider profile id
              <input
                value={draft.providerProfileId}
                onChange={(event) => updateDraftValue(setDraft, "providerProfileId", event.currentTarget.value)}
                placeholder="Optional provider profile"
              />
            </label>
            <label className="settings-toggle-inline">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(event) => updateDraftValue(setDraft, "enabled", event.currentTarget.checked)}
              />
              Enabled
            </label>
            <label className="settings-field">
              Skill ids
              <textarea
                rows={3}
                value={draft.skillIds}
                onChange={(event) => updateDraftValue(setDraft, "skillIds", event.currentTarget.value)}
                placeholder="code_reviewer"
              />
            </label>
            <label className="settings-field">
              MCP server ids
              <textarea
                rows={3}
                value={draft.mcpServerIds}
                onChange={(event) => updateDraftValue(setDraft, "mcpServerIds", event.currentTarget.value)}
                placeholder="filesystem"
              />
            </label>
            <label className="settings-field">
              Allowed tools
              <textarea
                rows={3}
                value={draft.allowedTools}
                onChange={(event) => updateDraftValue(setDraft, "allowedTools", event.currentTarget.value)}
                placeholder="read_file&#10;git_diff"
              />
            </label>
            <label className="settings-field">
              Denied tools
              <textarea
                rows={3}
                value={draft.deniedTools}
                onChange={(event) => updateDraftValue(setDraft, "deniedTools", event.currentTarget.value)}
                placeholder="run_command"
              />
            </label>
            <label className="settings-field settings-form-wide">
              Tools requiring approval
              <input
                value={draft.requiresApproval}
                onChange={(event) => updateDraftValue(setDraft, "requiresApproval", event.currentTarget.value)}
                placeholder="run_command, apply_patch"
              />
            </label>
            <label className="settings-field settings-form-wide">
              System prompt
              <textarea
                rows={4}
                value={draft.systemPrompt}
                onChange={(event) => updateDraftValue(setDraft, "systemPrompt", event.currentTarget.value)}
                placeholder="Profile-specific behavior layer"
              />
            </label>
          </div>

          <ToolPreview preview={preview} />

          {validation && !validation.valid ? (
            <p className="settings-danger-note" role="alert">{validation.errors.join("; ")}</p>
          ) : null}
          {localError ? <p className="settings-danger-note" role="alert">{localError}</p> : null}

          <footer className="settings-modal-footer">
            <button type="button" className="settings-secondary-action" onClick={() => void validateCurrent()} disabled={busy || !onValidateAgent}>
              Validate
            </button>
            <button type="button" className="settings-secondary-action" onClick={() => void previewTools()} disabled={busy || !onPreviewAgentTools}>
              Preview tools
            </button>
            {mode === "edit" && selectedAgent && !selectedAgent.isBuiltin ? (
              <button
                type="button"
                className="settings-secondary-action"
                onClick={() => void onDeleteAgent?.(selectedAgent.id)}
                disabled={busy || !onDeleteAgent}
              >
                Delete
              </button>
            ) : null}
            <button type="submit" className="settings-primary-action" disabled={busy || !canPersist}>
              {mode === "edit" ? "Save profile" : "Create profile"}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
