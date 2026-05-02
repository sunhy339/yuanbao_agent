import { useCallback } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { Button, Panel, SelectField, StatusBadge, TextField } from "../../v2/components/ui";
import "./newSession.css";

export interface NewSessionWorkspaceProps {
  workspacePath: string;
  hostStatusText: string;
  sessionTitle?: string;
  modelLabel?: string;
  modelOptions?: Array<{
    id: string;
    label: string;
  }>;
  selectedModelId?: string;
  workspaceBusy?: boolean;
  sessionBusy?: boolean;
  onSelectModel?(modelId: string): void;
  onSessionTitleChange?(title: string): void;
  onWorkspacePathChange?(path: string): void;
  onOpenWorkspace?(): void | Promise<void>;
  onCreateSession?(): void | Promise<void>;
}

const sessionTemplates = [
  {
    id: "code-change",
    title: "Code change",
    description: "Implement a scoped patch, run checks, and summarize files changed.",
  },
  {
    id: "debug",
    title: "Debug run",
    description: "Inspect failing behavior, collect evidence, then fix with verification.",
  },
  {
    id: "planning",
    title: "Plan breakdown",
    description: "Turn a larger product goal into sequenced agent-ready work.",
  },
];

export function NewSessionWorkspace({
  workspacePath,
  hostStatusText,
  sessionTitle = "New Yuanbao Session",
  modelLabel = "Model not configured",
  modelOptions = [],
  selectedModelId,
  workspaceBusy = false,
  sessionBusy = false,
  onSelectModel,
  onSessionTitleChange,
  onWorkspacePathChange,
  onOpenWorkspace,
  onCreateSession,
}: NewSessionWorkspaceProps) {
  const resolvedModelId = selectedModelId ?? modelOptions[0]?.id ?? "";
  const modelSelectOptions = modelOptions.length
    ? modelOptions.map((option) => ({ label: option.label, value: option.id }))
    : [{ label: modelLabel, value: "" }];
  const startupChecks = [
    {
      id: "runtime",
      label: "Runtime host",
      status: hostStatusText,
      tone: "success" as const,
    },
    {
      id: "provider",
      label: "Provider profile",
      status: modelOptions.length ? modelLabel : "Configure in Settings",
      tone: modelOptions.length ? ("info" as const) : ("warning" as const),
    },
    {
      id: "workspace",
      label: "Workspace root",
      status: workspacePath.trim() ? "ready" : "missing",
      tone: workspacePath.trim() ? ("success" as const) : ("warning" as const),
    },
  ];

  const handleBrowseFolder = useCallback(async () => {
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        title: "Select workspace folder",
      });
      if (selected) {
        onWorkspacePathChange?.(selected);
      }
    } catch {
      // The desktop dialog may be cancelled or unavailable in browser tests.
    }
  }, [onWorkspacePathChange]);

  return (
    <main className="new-session-workspace" aria-labelledby="new-session-title">
      <section className="new-session-hero" aria-label="New session desk">
        <div className="new-session-left-rail">
          <div className="new-session-copy-block">
            <p className="new-session-kicker">Yuanbao Agent</p>
            <h1 id="new-session-title">Workspace Launcher</h1>
            <p className="new-session-copy">
              Select a workspace, confirm the runtime profile, and launch a session with the command bar ready.
            </p>
            <div className="new-session-signal-row" aria-label="Session status">
              <StatusBadge label={hostStatusText} tone="success" pulse />
              <StatusBadge label={modelLabel} tone={modelOptions.length ? "info" : "warning"} />
            </div>
          </div>

          <Panel className="new-session-check-panel" eyebrow="Startup Checks" title="Readiness">
            <div className="new-session-check-list">
              {startupChecks.map((check) => (
                <div key={check.id} className="new-session-check-row">
                  <span>{check.label}</span>
                  <StatusBadge label={check.status} tone={check.tone} compact />
                </div>
              ))}
            </div>
          </Panel>
        </div>

        <div className="new-session-control-stack">
          <Panel
            eyebrow="Launch Control"
            title="Session setup"
            description="Prepare the workspace lane before sending work to the agent."
          >
            <form
              className="new-session-form"
              onSubmit={(event) => {
                event.preventDefault();
                void onCreateSession?.();
              }}
            >
              <TextField
                aria-label="Session title"
                label="Session title"
                helperText="Used for the sidebar and tab title."
                value={sessionTitle}
                onChange={(value) => onSessionTitleChange?.(value)}
                disabled={!onSessionTitleChange || sessionBusy}
              />

              <SelectField
                aria-label="Select model"
                label="Active model"
                helperText={modelOptions.length ? "Switches the active provider profile." : "Configure providers in Settings first."}
                value={resolvedModelId}
                options={modelSelectOptions}
                disabled={!modelOptions.length || !onSelectModel || sessionBusy}
                onChange={(value) => onSelectModel?.(value)}
              />

              <div className="new-session-workspace-row">
                <TextField
                  aria-label="Workspace folder"
                  label="Workspace folder"
                  helperText="Commands, patches, and searches will use this root."
                  value={workspacePath}
                  onChange={(value) => onWorkspacePathChange?.(value)}
                  disabled={workspaceBusy || sessionBusy}
                />
                <div className="new-session-folder-actions">
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={workspaceBusy || sessionBusy}
                    onClick={handleBrowseFolder}
                    title="Browse folder"
                  >
                    Browse
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    loading={workspaceBusy}
                    disabled={!onOpenWorkspace || sessionBusy || !workspacePath.trim()}
                    onClick={() => {
                      void onOpenWorkspace?.();
                    }}
                  >
                    Apply workspace
                  </Button>
                </div>
              </div>

              <div className="new-session-action-bar">
                <Button
                  type="submit"
                  variant="primary"
                  size="lg"
                  loading={sessionBusy}
                  disabled={!onCreateSession || !sessionTitle.trim()}
                >
                  Create session
                </Button>
                <p>Or write directly in the bottom command bar to create and run in one step.</p>
              </div>
            </form>
          </Panel>

          <Panel className="new-session-template-panel" eyebrow="Session Templates" title="Start mode">
            <div className="new-session-template-grid">
              {sessionTemplates.map((template) => (
                <button
                  key={template.id}
                  type="button"
                  disabled={!onSessionTitleChange || sessionBusy}
                  onClick={() => onSessionTitleChange?.(template.title)}
                >
                  <strong>{template.title}</strong>
                  <span>{template.description}</span>
                </button>
              ))}
            </div>
          </Panel>
        </div>
      </section>
    </main>
  );
}

export default NewSessionWorkspace;
