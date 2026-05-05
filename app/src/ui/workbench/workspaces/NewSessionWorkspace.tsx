import { useCallback, useState } from "react";
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
    title: "代码改动",
    description: "完成范围明确的改动，运行检查，并总结变更文件。",
  },
  {
    id: "debug",
    title: "问题排查",
    description: "定位失败行为、收集证据，然后修复并验证。",
  },
  {
    id: "planning",
    title: "拆解计划",
    description: "把较大的产品目标拆成可交给智能体执行的步骤。",
  },
];

export function NewSessionWorkspace({
  workspacePath,
  hostStatusText,
  sessionTitle = "新的元宝会话",
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
}: NewSessionWorkspaceProps) {
  const [browseError, setBrowseError] = useState<string | null>(null);
  const resolvedModelId = selectedModelId ?? modelOptions[0]?.id ?? "";
  const modelSelectOptions = modelOptions.length
    ? modelOptions.map((option) => ({ label: option.label, value: option.id }))
    : [{ label: modelLabel, value: "" }];
  const startupChecks = [
    {
      id: "runtime",
      label: "运行时主机",
      status: hostStatusText,
      tone: "success" as const,
    },
    {
      id: "provider",
      label: "模型供应商",
      status: modelOptions.length ? modelLabel : "请先在设置中配置",
      tone: modelOptions.length ? ("info" as const) : ("warning" as const),
    },
    {
      id: "workspace",
      label: "工作区根目录",
      status: workspacePath.trim() ? "就绪" : "缺失",
      tone: workspacePath.trim() ? ("success" as const) : ("warning" as const),
    },
  ];

  const handleBrowseFolder = useCallback(async () => {
    setBrowseError(null);
    try {
      const selected = await open({
        directory: true,
        multiple: false,
        title: "选择工作区文件夹",
      });
      const selectedPath = Array.isArray(selected) ? selected[0] : selected;
      if (typeof selectedPath === "string" && selectedPath.trim()) {
        onWorkspacePathChange?.(selectedPath);
      }
    } catch (reason) {
      setBrowseError(
        reason instanceof Error && reason.message
          ? `无法打开系统文件夹选择器：${reason.message}`
          : "无法打开系统文件夹选择器，请直接粘贴路径后点击“应用工作区”。",
      );
    }
  }, [onWorkspacePathChange]);

  return (
    <main className="new-session-workspace" aria-labelledby="new-session-title">
      <section className="new-session-hero" aria-label="新建会话">
        <div className="new-session-left-rail">
          <div className="new-session-copy-block">
            <p className="new-session-kicker">Yuanbao Agent</p>
            <h1 id="new-session-title">工作区启动器</h1>
            <p className="new-session-copy">
              选择工作区，确认运行时配置，然后启动一个可直接输入指令的会话。
            </p>
            <div className="new-session-signal-row" aria-label="会话状态">
              <StatusBadge label={hostStatusText} tone="success" pulse />
              <StatusBadge label={modelLabel} tone={modelOptions.length ? "info" : "warning"} />
            </div>
          </div>

          <Panel className="new-session-check-panel" eyebrow="启动检查" title="准备状态">
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
            eyebrow="启动控制"
            title="会话配置"
            description="先准备好工作区通道，再把任务交给智能体执行。"
          >
            <form
              className="new-session-form"
              onSubmit={(event) => {
                event.preventDefault();
                void onCreateSession?.();
              }}
            >
              <TextField
            aria-label="会话标题"
                label="会话标题"
                helperText="用于侧边栏和标签标题。"
                value={sessionTitle}
                onChange={(value) => onSessionTitleChange?.(value)}
                disabled={!onSessionTitleChange || sessionBusy}
              />

              <SelectField
            aria-label="选择模型"
                label="当前模型"
                helperText={modelOptions.length ? "切换当前模型供应商配置。" : "请先在设置中配置模型供应商。"}
                value={resolvedModelId}
                options={modelSelectOptions}
                disabled={!modelOptions.length || !onSelectModel || sessionBusy}
                onChange={(value) => onSelectModel?.(value)}
              />

              <div className="new-session-workspace-row">
                <TextField
            aria-label="工作区文件夹"
                  label="工作区文件夹"
                  helperText="命令、改动和搜索都会使用这个根目录。"
                  value={workspacePath}
                  onChange={(value) => onWorkspacePathChange?.(value)}
                  disabled={workspaceBusy || sessionBusy}
                />
                <div className="new-session-folder-actions">
                  <Button
                    type="button"
                    variant="secondary"
                    aria-label="浏览"
                    disabled={!onWorkspacePathChange || workspaceBusy || sessionBusy}
                    disabledReason={!onWorkspacePathChange ? "当前视图无法更新工作区路径。" : undefined}
                    onClick={handleBrowseFolder}
                    title="浏览文件夹"
                  >
                    浏览
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
            aria-label="应用工作区"
                    loading={workspaceBusy}
                    disabled={!onOpenWorkspace || sessionBusy || !workspacePath.trim()}
                    onClick={() => {
                      void onOpenWorkspace?.();
                    }}
                  >
                    应用工作区
                  </Button>
                  {browseError ? (
                    <p className="new-session-browse-error" role="alert">
                      {browseError}
                    </p>
                  ) : null}
                </div>
              </div>

              <div className="new-session-action-bar">
                <Button
                  type="submit"
                  variant="primary"
            aria-label="创建会话"
                  size="lg"
                  loading={sessionBusy}
                  disabled={!onCreateSession || !sessionTitle.trim()}
                >
                  创建会话
                </Button>
                <p>也可以直接在底部输入区写指令，系统会自动创建会话并开始执行。</p>
              </div>
            </form>
          </Panel>

          <Panel className="new-session-template-panel" eyebrow="会话模板" title="启动方式">
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
