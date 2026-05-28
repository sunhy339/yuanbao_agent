import { useCallback, useMemo, useState } from "react";
import { CheckCircle2, Folder, GitBranch, Settings2 } from "lucide-react";
import { CleanComposer, type CleanComposerProps } from "../composer/CleanComposer";
import { basename } from "../shared/text";

export interface CleanNewSessionWorkspaceProps {
  workspacePath: string;
  hostStatusText: string;
  sessionTitle?: string;
  modelLabel?: string;
  modelOptions?: Array<{ id: string; label: string; subtitle?: string }>;
  selectedModelId?: string;
  workspaceBusy?: boolean;
  sessionBusy?: boolean;
  onSelectModel?(modelId: string): void;
  onSessionTitleChange?(title: string): void;
  onWorkspacePathChange?(path: string): void;
  onOpenWorkspace?(): void | Promise<void>;
  onCreateSession?(): void | Promise<void>;
  composer?: Omit<CleanComposerProps, "providerLabel" | "cwdLabel" | "modelOptions" | "selectedModelId" | "onSelectModel" | "variant">;
}

export function CleanNewSessionWorkspace({
  workspacePath,
  hostStatusText,
  sessionTitle = "New Session",
  modelLabel = "未配置模型",
  modelOptions = [],
  selectedModelId,
  workspaceBusy = false,
  sessionBusy = false,
  onSelectModel,
  onSessionTitleChange,
  onWorkspacePathChange,
  onOpenWorkspace,
  onCreateSession,
  composer,
}: CleanNewSessionWorkspaceProps) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const selectedModel = useMemo(
    () => modelOptions.find((option) => option.id === selectedModelId) ?? modelOptions[0],
    [modelOptions, selectedModelId],
  );

  const browseWorkspace = useCallback(async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const selected = await open({ directory: true, multiple: false, title: "选择工作区文件夹" });
      const selectedPath = Array.isArray(selected) ? selected[0] : selected;
      if (typeof selectedPath === "string" && selectedPath.trim()) {
        onWorkspacePathChange?.(selectedPath);
      }
    } catch {
      // The shell owns persistent error/toast handling; keep this action quiet.
    }
  }, [onWorkspacePathChange]);

  return (
    <main className="hc-new-session" aria-labelledby="hc-new-session-title">
      <section className="hc-new-hero">
        <div className="hc-logo-mark" aria-hidden="true">Y</div>
        <h1 id="hc-new-session-title">新建会话</h1>
        <p>开始一个新的编码会话。模型会围绕当前项目读文件、运行命令并整理改动。</p>
      </section>

      <section className="hc-new-composer" data-has-composer={composer ? "true" : "false"}>
        {composer ? (
          <CleanComposer
            {...composer}
            providerLabel={selectedModel?.label ?? modelLabel}
            cwdLabel={workspacePath}
            modelOptions={modelOptions}
            selectedModelId={selectedModelId}
            onSelectModel={onSelectModel}
            variant="new"
          />
        ) : null}
        <div className="hc-project-row">
          <button type="button" onClick={browseWorkspace} disabled={!onWorkspacePathChange || workspaceBusy || sessionBusy}>
            <Folder size={17} />
            <span>{workspacePath ? basename(workspacePath) : "选择项目目录"}</span>
          </button>
          <span><GitBranch size={15} />main</span>
          <span><CheckCircle2 size={15} />{hostStatusText}</span>
          <button type="button" className="hc-new-settings-button" onClick={() => setSettingsOpen((open) => !open)}>
            <Settings2 size={15} />
            会话设置
          </button>
        </div>
      </section>

      {settingsOpen ? (
        <section className="hc-new-settings">
          <label>
            <span>会话标题</span>
            <input
              value={sessionTitle}
              disabled={!onSessionTitleChange || sessionBusy}
              onChange={(event) => onSessionTitleChange?.(event.currentTarget.value)}
            />
          </label>
          <label>
            <span>当前模型</span>
            <select
              value={selectedModelId ?? modelOptions[0]?.id ?? ""}
              disabled={!modelOptions.length || !onSelectModel || sessionBusy}
              onChange={(event) => onSelectModel?.(event.currentTarget.value)}
            >
              {modelOptions.length ? (
                modelOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)
              ) : (
                <option value="">{modelLabel}</option>
              )}
            </select>
          </label>
          <label className="hc-new-workspace-field">
            <span>工作区文件夹</span>
            <input
              value={workspacePath}
              disabled={workspaceBusy || sessionBusy}
              onChange={(event) => onWorkspacePathChange?.(event.currentTarget.value)}
            />
          </label>
          <div className="hc-new-actions">
            <button type="button" onClick={() => void onOpenWorkspace?.()} disabled={!onOpenWorkspace || workspaceBusy || sessionBusy || !workspacePath.trim()}>
              应用工作区
            </button>
            <button type="button" onClick={() => void onCreateSession?.()} disabled={!onCreateSession || sessionBusy || !sessionTitle.trim()}>
              创建会话
            </button>
          </div>
        </section>
      ) : null}
    </main>
  );
}
