import { type SettingsAgentBehaviorConfig } from "./settingsTypes";

export function AgentBehaviorPanel({
  value,
  onChange,
}: {
  value: SettingsAgentBehaviorConfig;
  onChange: (next: SettingsAgentBehaviorConfig) => void;
}) {
  const activeAutonomy = value.autonomyProfiles.find((profile) => profile.id === value.autonomyActiveProfileId)
    ?? value.autonomyProfiles[0];
  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">Agent</p>
          <h2>Autonomy and soul</h2>
          <p>Configure autonomy limits and the identity/system-prompt layer used before task-specific roles.</p>
        </div>
      </header>
      <div className="settings-form-stack">
        <label className="settings-field">
          <span>Autonomy profile</span>
          <select
            value={value.autonomyActiveProfileId}
            onChange={(event) => onChange({ ...value, autonomyActiveProfileId: event.target.value })}
          >
            {value.autonomyProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name} ({profile.level})
              </option>
            ))}
          </select>
          {activeAutonomy ? (
            <small>
              {`max steps ${activeAutonomy.maxSteps}, parallel subtasks ${activeAutonomy.maxParallelSubtasks}`}
            </small>
          ) : null}
        </label>
        <label className="settings-field">
          <span>Soul name</span>
          <input
            value={value.soulName}
            onChange={(event) => onChange({ ...value, soulName: event.target.value })}
          />
        </label>
        <label className="settings-field">
          <span>Identity</span>
          <textarea
            value={value.soulIdentity}
            rows={3}
            onChange={(event) => onChange({ ...value, soulIdentity: event.target.value })}
          />
        </label>
        <label className="settings-field">
          <span>Communication style</span>
          <input
            value={value.soulCommunicationStyle}
            onChange={(event) => onChange({ ...value, soulCommunicationStyle: event.target.value })}
          />
        </label>
        <label className="settings-field">
          <span>Reasoning style</span>
          <input
            value={value.soulReasoningStyle}
            onChange={(event) => onChange({ ...value, soulReasoningStyle: event.target.value })}
          />
        </label>
        <label className="settings-field">
          <span>Collaboration style</span>
          <input
            value={value.soulCollaborationStyle}
            onChange={(event) => onChange({ ...value, soulCollaborationStyle: event.target.value })}
          />
        </label>
        <label className="settings-field">
          <span>Custom system prompt</span>
          <textarea
            value={value.soulCustomSystemPrompt}
            rows={4}
            onChange={(event) => onChange({ ...value, soulCustomSystemPrompt: event.target.value })}
          />
          <small>Runtime safety and approval instructions are still appended and cannot be disabled here.</small>
        </label>
        <label className="settings-field">
          <span>Workspace instructions</span>
          <textarea
            value={value.workspaceInstructions}
            rows={4}
            onChange={(event) => onChange({ ...value, workspaceInstructions: event.target.value })}
          />
        </label>
      </div>
    </div>
  );
}
