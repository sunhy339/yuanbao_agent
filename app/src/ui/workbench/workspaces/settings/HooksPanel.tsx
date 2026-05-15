import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import {
  runtimeHookActionTypes,
  runtimeHookEvents,
  runtimeHookFailureModes,
  type SettingsHookConfig,
  type SettingsHookDraft,
  type SettingsHookFeedback,
} from "./settingsTypes";
import type { RuntimeHookAction, RuntimeHookExecutionRecord } from "@shared";

type HookActionType = (typeof runtimeHookActionTypes)[number];
type HookFailureMode = (typeof runtimeHookFailureModes)[number];
type HookEvent = (typeof runtimeHookEvents)[number];

interface HookDraftForm {
  name: string;
  enabled: boolean;
  event: HookEvent;
  priority: string;
  actionType: HookActionType;
  actionNote: string;
  actionMessage: string;
  actionCommand: string;
  actionCwd: string;
  conditionsJson: string;
  authorityJson: string;
  retryJson: string;
  timeoutMs: string;
  onFailure: HookFailureMode;
}

function prettyJson(value: unknown, fallback = "{}"): string {
  if (!value || (typeof value === "object" && Object.keys(value as Record<string, unknown>).length === 0)) {
    return fallback;
  }
  return JSON.stringify(value, null, 2);
}

function parseJsonObject(value: string, label: string): Record<string, unknown> {
  const trimmed = value.trim();
  if (!trimmed) {
    return {};
  }
  const parsed = JSON.parse(trimmed) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object.`);
  }
  return parsed as Record<string, unknown>;
}

function hookActionType(action?: RuntimeHookAction): HookActionType {
  const rawType = action?.type;
  return runtimeHookActionTypes.includes(rawType as HookActionType)
    ? rawType as HookActionType
    : "audit_note";
}

function hookEvent(event?: string): HookEvent {
  return runtimeHookEvents.includes(event as HookEvent)
    ? event as HookEvent
    : "before_task_start";
}

function hookFailureMode(mode?: string): HookFailureMode {
  return runtimeHookFailureModes.includes(mode as HookFailureMode)
    ? mode as HookFailureMode
    : "warn";
}

function draftFromHook(hook?: SettingsHookConfig | null): HookDraftForm {
  const action = hook?.action ?? {};
  return {
    name: hook?.name ?? "",
    enabled: hook?.enabled ?? true,
    event: hookEvent(hook?.event),
    priority: String(hook?.priority ?? 100),
    actionType: hookActionType(action),
    actionNote: typeof action.note === "string" ? action.note : "",
    actionMessage: typeof action.message === "string" ? action.message : "",
    actionCommand: typeof action.command === "string" ? action.command : "",
    actionCwd: typeof action.cwd === "string" ? action.cwd : "",
    conditionsJson: prettyJson(hook?.conditions),
    authorityJson: prettyJson(hook?.authority),
    retryJson: prettyJson(hook?.retry, '{"maxAttempts":0}'),
    timeoutMs: String(hook?.timeoutMs ?? 60_000),
    onFailure: hookFailureMode(hook?.onFailure),
  };
}

function payloadFromDraft(draft: HookDraftForm, workspaceId: string): SettingsHookDraft {
  const action: RuntimeHookAction = { type: draft.actionType };
  if (draft.actionType === "audit_note" && draft.actionNote.trim()) {
    action.note = draft.actionNote.trim();
  }
  if (draft.actionType === "notification" && draft.actionMessage.trim()) {
    action.message = draft.actionMessage.trim();
  }
  if (draft.actionType === "run_command") {
    if (draft.actionCommand.trim()) {
      action.command = draft.actionCommand.trim();
    }
    if (draft.actionCwd.trim()) {
      action.cwd = draft.actionCwd.trim();
    }
  }

  return {
    workspaceId,
    name: draft.name.trim(),
    enabled: draft.enabled,
    event: draft.event,
    priority: Number.parseInt(draft.priority, 10),
    action,
    conditions: parseJsonObject(draft.conditionsJson, "Conditions"),
    authority: parseJsonObject(draft.authorityJson, "Authority"),
    retry: parseJsonObject(draft.retryJson, "Retry"),
    timeoutMs: Number.parseInt(draft.timeoutMs, 10),
    onFailure: draft.onFailure,
  };
}

function updateDraftValue<K extends keyof HookDraftForm>(
  setDraft: Dispatch<SetStateAction<HookDraftForm>>,
  key: K,
  value: HookDraftForm[K],
) {
  setDraft((current) => ({ ...current, [key]: value }));
}

function formatTime(value?: number | null): string {
  if (!value) {
    return "pending";
  }
  return new Date(value).toLocaleString();
}

function HookList({
  hooks,
  selectedId,
  busyId,
  onSelect,
  onToggle,
}: {
  hooks: SettingsHookConfig[];
  selectedId?: string | null;
  busyId?: string | null;
  onSelect: (hook: SettingsHookConfig) => void;
  onToggle?: (hookId: string, enabled: boolean) => void | Promise<void>;
}) {
  if (!hooks.length) {
    return (
      <div className="settings-empty-state">
        <span aria-hidden="true">H</span>
        <strong>No runtime hooks</strong>
        <small>Create a hook to attach actions to task, tool, provider, compaction, or worktree lifecycle events.</small>
      </div>
    );
  }

  return (
    <div className="settings-form-stack">
      {hooks.map((hook) => (
        <article
          key={hook.id}
          className={selectedId === hook.id ? "settings-row-card settings-hook-row is-selected" : "settings-row-card settings-hook-row"}
        >
          <input
            aria-label={`Enable ${hook.name}`}
            type="checkbox"
            checked={hook.enabled}
            disabled={!onToggle || busyId === hook.id}
            onChange={(event) => void onToggle?.(hook.id, event.currentTarget.checked)}
          />
          <button type="button" className="settings-agent-select" onClick={() => onSelect(hook)}>
            <span>
              <strong>{hook.name}</strong>
              <small>{hook.event}</small>
              <small>{hook.action?.type ?? "audit_note"} / {hook.onFailure}</small>
            </span>
          </button>
          <em>p{hook.priority}</em>
        </article>
      ))}
    </div>
  );
}

function HookExecutions({
  executions,
  onRefresh,
  selectedHookId,
  busy,
}: {
  executions: RuntimeHookExecutionRecord[];
  onRefresh?: (hookId?: string) => void | Promise<void>;
  selectedHookId?: string | null;
  busy?: boolean;
}) {
  return (
    <section className="settings-hook-executions" aria-live="polite">
      <header>
        <div>
          <p className="settings-kicker">Executions</p>
          <h3>Recent runs</h3>
        </div>
        <button
          type="button"
          className="settings-secondary-action"
          onClick={() => void onRefresh?.(selectedHookId ?? undefined)}
          disabled={!onRefresh || busy || !selectedHookId}
        >
          Refresh runs
        </button>
      </header>
      {executions.length ? (
        <div className="settings-hook-execution-list">
          {executions.slice(0, 8).map((execution) => (
            <article key={execution.id}>
              <strong>{execution.status}</strong>
              <span>{execution.event}</span>
              <small>{execution.conditionResult} / {execution.policyOutcome}</small>
              <small>{formatTime(execution.createdAt)}{execution.durationMs ? ` / ${execution.durationMs}ms` : ""}</small>
              {execution.errorSummary ? <small className="settings-hook-error">{execution.errorSummary}</small> : null}
              {execution.outputSummary ? <small>{execution.outputSummary}</small> : null}
            </article>
          ))}
        </div>
      ) : (
        <p className="settings-action-note">No executions recorded for the selected hook yet.</p>
      )}
    </section>
  );
}

export function HooksPanel({
  hooks,
  executions = [],
  hookBusyId,
  hookFeedback,
  workspaceId,
  onRefreshHooks,
  onHookToggle,
  onAddHook,
  onUpdateHook,
  onDeleteHook,
  onRefreshHookExecutions,
}: {
  hooks: SettingsHookConfig[];
  executions?: RuntimeHookExecutionRecord[];
  hookBusyId?: string | null;
  hookFeedback?: SettingsHookFeedback | null;
  workspaceId?: string | null;
  onRefreshHooks?: () => void | Promise<void>;
  onHookToggle?: (hookId: string, enabled: boolean) => void | Promise<void>;
  onAddHook?: (payload: SettingsHookDraft) => void | Promise<void>;
  onUpdateHook?: (hookId: string, payload: Partial<SettingsHookDraft>) => void | Promise<void>;
  onDeleteHook?: (hookId: string) => void | Promise<void>;
  onRefreshHookExecutions?: (hookId?: string) => void | Promise<void>;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(hooks[0]?.id ?? null);
  const selectedHook = useMemo(
    () => hooks.find((hook) => hook.id === selectedId) ?? null,
    [hooks, selectedId],
  );
  const fallbackHook = hooks[0] ?? null;
  const [mode, setMode] = useState<"create" | "edit">(selectedHook ? "edit" : "create");
  const [manualCreate, setManualCreate] = useState(false);
  const [draft, setDraft] = useState<HookDraftForm>(() => draftFromHook(selectedHook));
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    if (manualCreate) {
      return;
    }
    const nextHook = selectedHook ?? fallbackHook;
    if (!nextHook) {
      if (mode === "edit") {
        setSelectedId(null);
        setDraft(draftFromHook(null));
        setMode("create");
        setLocalError(null);
      }
      return;
    }
    if (selectedId !== nextHook.id) {
      setSelectedId(nextHook.id);
    }
    setDraft(draftFromHook(nextHook));
    setMode("edit");
    setLocalError(null);
    setManualCreate(false);
  }, [fallbackHook, manualCreate, mode, selectedHook, selectedId]);

  function selectHook(hook: SettingsHookConfig) {
    setSelectedId(hook.id);
    setDraft(draftFromHook(hook));
    setMode("edit");
    setManualCreate(false);
    setLocalError(null);
    void onRefreshHookExecutions?.(hook.id);
  }

  function startCreate() {
    setSelectedId(null);
    setDraft(draftFromHook(null));
    setMode("create");
    setManualCreate(true);
    setLocalError(null);
  }

  async function submitForm() {
    if (!workspaceId) {
      setLocalError("Open a workspace before creating or editing hooks.");
      return;
    }
    setLocalError(null);
    try {
      const payload = payloadFromDraft(draft, workspaceId);
      if (!payload.name) {
        setLocalError("Name is required.");
        return;
      }
      if (!Number.isFinite(payload.priority) || payload.priority === undefined) {
        setLocalError("Priority must be a number.");
        return;
      }
      if (!Number.isFinite(payload.timeoutMs) || payload.timeoutMs === undefined) {
        setLocalError("Timeout must be a number.");
        return;
      }
      if (payload.action?.type === "run_command" && !payload.action.command) {
        setLocalError("Run command hooks require a command.");
        return;
      }
      if (mode === "edit" && selectedHook) {
        const { workspaceId: _workspaceId, ...patch } = payload;
        await onUpdateHook?.(selectedHook.id, patch);
        return;
      }
      await onAddHook?.(payload);
      startCreate();
    } catch (reason) {
      setLocalError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  const filteredExecutions = selectedId
    ? executions.filter((execution) => execution.hookId === selectedId)
    : executions;
  const busy = Boolean(hookBusyId);
  const canPersist = Boolean(workspaceId && (onAddHook || onUpdateHook));

  return (
    <div className="settings-panel">
      <header className="settings-panel-header">
        <div>
          <p className="settings-kicker">Runtime Hooks</p>
          <h2>Hook settings</h2>
          <p>Manage lifecycle hooks for task, tool, provider, compaction, and worktree events.</p>
        </div>
        <div className="settings-header-actions">
          <button type="button" className="settings-secondary-action" onClick={() => void onRefreshHooks?.()} disabled={!onRefreshHooks || hookBusyId === "refresh"}>
            Refresh
          </button>
          <button type="button" className="settings-primary-action" onClick={startCreate} disabled={!workspaceId || !onAddHook}>
            New hook
          </button>
        </div>
      </header>

      {!workspaceId ? (
        <p className="settings-danger-note" role="alert">Open a workspace before managing runtime hooks.</p>
      ) : null}

      {hookFeedback ? (
        <p className={`settings-action-note settings-agent-feedback is-${hookFeedback.tone}`} role="status">
          {hookFeedback.message}
        </p>
      ) : null}

      <div className="settings-agent-grid">
        <HookList
          hooks={hooks}
          selectedId={selectedId}
          busyId={hookBusyId}
          onSelect={selectHook}
          onToggle={onHookToggle}
        />

        <form
          className="settings-agent-editor settings-hook-editor"
          onSubmit={(event) => {
            event.preventDefault();
            void submitForm();
          }}
        >
          <div className="settings-agent-editor-header">
            <div>
              <p className="settings-kicker">{mode === "edit" ? "Edit" : "Create"}</p>
              <h3>{mode === "edit" ? selectedHook?.name ?? "Hook" : "New hook"}</h3>
            </div>
            <span className={draft.enabled ? "settings-status-pill is-pass" : "settings-status-pill"}>{draft.enabled ? "Enabled" : "Disabled"}</span>
          </div>

          <div className="settings-provider-form">
            <label className="settings-field">
              Name
              <input
                value={draft.name}
                onChange={(event) => updateDraftValue(setDraft, "name", event.currentTarget.value)}
                placeholder="Audit provider turns"
              />
            </label>
            <label className="settings-field">
              Event
              <select value={draft.event} onChange={(event) => updateDraftValue(setDraft, "event", event.currentTarget.value as HookEvent)}>
                {runtimeHookEvents.map((eventName) => (
                  <option key={eventName} value={eventName}>{eventName}</option>
                ))}
              </select>
            </label>
            <label className="settings-field">
              Priority
              <input
                value={draft.priority}
                inputMode="numeric"
                onChange={(event) => updateDraftValue(setDraft, "priority", event.currentTarget.value)}
              />
            </label>
            <label className="settings-field">
              Timeout ms
              <input
                value={draft.timeoutMs}
                inputMode="numeric"
                onChange={(event) => updateDraftValue(setDraft, "timeoutMs", event.currentTarget.value)}
              />
            </label>
            <label className="settings-field">
              Action
              <select
                value={draft.actionType}
                onChange={(event) => updateDraftValue(setDraft, "actionType", event.currentTarget.value as HookActionType)}
              >
                {runtimeHookActionTypes.map((actionType) => (
                  <option key={actionType} value={actionType}>{actionType}</option>
                ))}
              </select>
            </label>
            <label className="settings-field">
              On failure
              <select
                value={draft.onFailure}
                onChange={(event) => updateDraftValue(setDraft, "onFailure", event.currentTarget.value as HookFailureMode)}
              >
                {runtimeHookFailureModes.map((modeName) => (
                  <option key={modeName} value={modeName}>{modeName}</option>
                ))}
              </select>
            </label>
            <label className="settings-toggle-inline">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(event) => updateDraftValue(setDraft, "enabled", event.currentTarget.checked)}
              />
              Enabled
            </label>
            {draft.actionType === "audit_note" ? (
              <label className="settings-field settings-form-wide">
                Audit note
                <input
                  value={draft.actionNote}
                  onChange={(event) => updateDraftValue(setDraft, "actionNote", event.currentTarget.value)}
                  placeholder="Record a lifecycle audit note"
                />
              </label>
            ) : null}
            {draft.actionType === "notification" ? (
              <label className="settings-field settings-form-wide">
                Notification message
                <input
                  value={draft.actionMessage}
                  onChange={(event) => updateDraftValue(setDraft, "actionMessage", event.currentTarget.value)}
                  placeholder="Task completed"
                />
              </label>
            ) : null}
            {draft.actionType === "run_command" ? (
              <>
                <label className="settings-field settings-form-wide">
                  Command
                  <input
                    value={draft.actionCommand}
                    onChange={(event) => updateDraftValue(setDraft, "actionCommand", event.currentTarget.value)}
                    placeholder="npm test"
                  />
                </label>
                <label className="settings-field settings-form-wide">
                  Command cwd
                  <input
                    value={draft.actionCwd}
                    onChange={(event) => updateDraftValue(setDraft, "actionCwd", event.currentTarget.value)}
                    placeholder="Workspace root"
                  />
                </label>
              </>
            ) : null}
            <label className="settings-field">
              Conditions JSON
              <textarea
                rows={5}
                value={draft.conditionsJson}
                onChange={(event) => updateDraftValue(setDraft, "conditionsJson", event.currentTarget.value)}
              />
            </label>
            <label className="settings-field">
              Authority JSON
              <textarea
                rows={5}
                value={draft.authorityJson}
                onChange={(event) => updateDraftValue(setDraft, "authorityJson", event.currentTarget.value)}
              />
            </label>
            <label className="settings-field settings-form-wide">
              Retry JSON
              <textarea
                rows={3}
                value={draft.retryJson}
                onChange={(event) => updateDraftValue(setDraft, "retryJson", event.currentTarget.value)}
              />
            </label>
          </div>

          <HookExecutions
            executions={filteredExecutions}
            onRefresh={onRefreshHookExecutions}
            selectedHookId={selectedId}
            busy={hookBusyId === "executions"}
          />

          {localError ? <p className="settings-danger-note" role="alert">{localError}</p> : null}

          <footer className="settings-modal-footer">
            {mode === "edit" && selectedHook ? (
              <button
                type="button"
                className="settings-secondary-action"
                onClick={() => void onRefreshHookExecutions?.(selectedHook.id)}
                disabled={busy || !onRefreshHookExecutions}
              >
                Load runs
              </button>
            ) : null}
            {mode === "edit" && selectedHook ? (
              <button
                type="button"
                className="settings-secondary-action"
                onClick={() => void onDeleteHook?.(selectedHook.id)}
                disabled={busy || !onDeleteHook}
              >
                Delete
              </button>
            ) : null}
            <button type="submit" className="settings-primary-action" disabled={busy || !canPersist}>
              {mode === "edit" ? "Save hook" : "Create hook"}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
