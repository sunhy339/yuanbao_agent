import "./newSession.css";

export interface NewSessionWorkspaceProps {
  workspacePath: string;
  hostStatusText: string;
  modelLabel?: string;
  modelOptions?: Array<{
    id: string;
    label: string;
  }>;
  selectedModelId?: string;
  workspaceBusy?: boolean;
  onSelectModel?(modelId: string): void;
  onWorkspacePathChange?(path: string): void;
  onOpenWorkspace?(): void | Promise<void>;
}

export function NewSessionWorkspace({
  workspacePath,
  hostStatusText,
  modelLabel = "未配置模型",
  modelOptions = [],
  selectedModelId,
  workspaceBusy = false,
  onSelectModel,
  onWorkspacePathChange,
  onOpenWorkspace,
}: NewSessionWorkspaceProps) {
  const resolvedModelId = selectedModelId ?? modelOptions[0]?.id ?? "";

  return (
    <main className="new-session-workspace" aria-labelledby="new-session-title">
      <section className="new-session-desk" aria-label="New session desk">
        <div className="new-session-main">
          <p className="new-session-kicker">Yuanbao Agent</p>
          <h1 id="new-session-title">New Session</h1>
          <p className="new-session-copy">
            Type a task in the command bar below. Yuanbao will create a session and stream the work into the
            conversation.
          </p>

          <div className="new-session-status-row" aria-label="Session status">
            <span data-status="ready">{hostStatusText}</span>
            <label className="new-session-control-pill">
              <span>选择模型</span>
              <select
                aria-label="选择模型"
                disabled={!modelOptions.length || !onSelectModel}
                onChange={(event) => onSelectModel?.(event.currentTarget.value)}
                value={resolvedModelId}
              >
                {modelOptions.length ? (
                  modelOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))
                ) : (
                  <option value="">{modelLabel}</option>
                )}
              </select>
            </label>
            <form
              className="new-session-folder-control"
              onSubmit={(event) => {
                event.preventDefault();
                void onOpenWorkspace?.();
              }}
            >
              <label>
                <span>工作文件夹</span>
                <input
                  aria-label="工作文件夹"
                  onChange={(event) => onWorkspacePathChange?.(event.currentTarget.value)}
                  value={workspacePath}
                />
              </label>
              <button disabled={workspaceBusy || !onOpenWorkspace} type="submit">
                {workspaceBusy ? "应用中" : "应用文件夹"}
              </button>
            </form>
          </div>
        </div>
      </section>
    </main>
  );
}

export default NewSessionWorkspace;
