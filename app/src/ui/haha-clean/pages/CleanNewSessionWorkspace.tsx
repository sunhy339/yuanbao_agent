import { Code2 } from "lucide-react";
import { CleanComposer, type CleanComposerProps } from "../composer/CleanComposer";

export interface CleanNewSessionWorkspaceProps {
  workspacePath: string;
  hostStatusText: string;
  sessionTitle?: string;
  modelLabel?: string;
  modelOptions?: Array<{ id: string; label: string; subtitle?: string }>;
  selectedModelId?: string;
  branchLabel?: string | null;
  worktreeModeLabel?: string;
  permissionLabel?: string;
  workspaceBusy?: boolean;
  sessionBusy?: boolean;
  onSelectModel?(modelId: string): void;
  onSessionTitleChange?(title: string): void;
  onWorkspacePathChange?(path: string): void;
  onCreateSession?(): void | Promise<void>;
  onOpenSettings?(): void;
  composer?: Omit<CleanComposerProps, "providerLabel" | "cwdLabel" | "modelOptions" | "selectedModelId" | "onSelectModel" | "variant">;
}

export function CleanNewSessionWorkspace(props: CleanNewSessionWorkspaceProps) {
  const {
    workspacePath,
    modelLabel = "未配置模型",
    modelOptions = [],
    selectedModelId,
    onSelectModel,
    composer,
  } = props;
  const selectedModel = modelOptions.find((option) => option.id === selectedModelId) ?? modelOptions[0];

  return (
    <main className="hc-new-session" aria-labelledby="hc-new-session-title">
      <section className="hc-new-hero">
        <div className="hc-logo-mark" aria-hidden="true">
          <Code2 size={34} strokeWidth={2.1} />
        </div>
        <h1 id="hc-new-session-title">新建会话</h1>
        <p>开始一个新的编码会话。Yuanbao 已准备好帮你构建、调试和架构你的项目。</p>
      </section>

      {composer ? (
        <section className="hc-new-composer" aria-label="新建会话输入区">
          <CleanComposer
            {...composer}
            providerLabel={selectedModel?.label ?? modelLabel}
            cwdLabel={workspacePath}
            modelOptions={modelOptions}
            selectedModelId={selectedModelId}
            onSelectModel={onSelectModel}
            variant="new"
          />
        </section>
      ) : null}

      {composer ? null : (
        <p className="hc-new-hint">在底部输入任务后会自动创建会话。</p>
      )}
    </main>
  );
}
