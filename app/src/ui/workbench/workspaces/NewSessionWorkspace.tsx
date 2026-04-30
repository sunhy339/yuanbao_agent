import { useCallback } from "react";
import { open } from "@tauri-apps/plugin-dialog";
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

  const handleBrowseFolder = useCallback(async () => {
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        title: "选择工作文件夹",
      });
      if (selected) {
        onWorkspacePathChange?.(selected);
      }
    } catch {
      // dialog cancelled or unavailable – silently ignore
    }
  }, [onWorkspacePathChange]);

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
              <span>🤖 模型</span>
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
                <span>📁 工作目录</span>
                <input
                  aria-label="工作文件夹"
                  onChange={(event) => onWorkspacePathChange?.(event.currentTarget.value)}
                  value={workspacePath}
                />
              </label>
              <button
                type="button"
                className="new-session-browse-btn"
                disabled={workspaceBusy}
                onClick={handleBrowseFolder}
                title="浏览文件夹"
              >
                📂 浏览
              </button>
              <button disabled={workspaceBusy || !onOpenWorkspace} type="submit">
                {workspaceBusy ? "应用中" : "应用"}
              </button>
            </form>
          </div>
        </div>
      </section>
    </main>
  );
}

export default NewSessionWorkspace;
