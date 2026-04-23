import { useEffect, useState, type FormEvent } from "react";
import "./settings.css";

type SettingsSection =
  | "providers"
  | "permissions"
  | "general"
  | "im"
  | "agents"
  | "skills"
  | "computer"
  | "about";

export interface SettingsProvider {
  id: string;
  name: string;
  endpoint: string;
  note?: string;
  models?: string[];
  status?: string;
}

export interface SettingsProviderPayload {
  name: string;
  note: string;
  endpoint: string;
  apiKey: string;
  modelMapping: string;
  testConnection: string;
  jsonConfig: string;
}

export interface SettingsWorkspaceProps {
  providers?: SettingsProvider[];
  activeProviderId?: string;
  onSelectProvider?: (providerId: string) => void;
  onAddProvider?: (payload: SettingsProviderPayload) => void | Promise<void>;
  onTestProvider?: (providerId?: string) => void | Promise<void>;
  onSaveProvider?: (providerId?: string) => void | Promise<void>;
  providerBusy?: boolean;
  providerTestBusy?: boolean;
  permissionMode?: string;
  onPermissionModeChange?: (mode: string) => void;
}

const sections: Array<{ id: SettingsSection; label: string; eyebrow: string }> = [
  { id: "providers", label: "服务商", eyebrow: "API" },
  { id: "permissions", label: "权限", eyebrow: "Mode" },
  { id: "general", label: "通用", eyebrow: "Desk" },
  { id: "im", label: "IM 接入", eyebrow: "Bridge" },
  { id: "agents", label: "Agents", eyebrow: "Roster" },
  { id: "skills", label: "技能", eyebrow: "Library" },
  { id: "computer", label: "Computer Use", eyebrow: "Control" },
  { id: "about", label: "关于", eyebrow: "Build" },
];

const fallbackProviders: SettingsProvider[] = [
  {
    id: "deepseek",
    name: "DeepSeek",
    endpoint: "https://api.deepseek.com",
    note: "主力推理与代码模型",
    models: ["deepseek-chat", "deepseek-reasoner"],
    status: "已启用",
  },
  {
    id: "kimi",
    name: "Kimi",
    endpoint: "https://api.moonshot.cn/v1",
    note: "长上下文阅读备用",
    models: ["moonshot-v1-128k"],
    status: "待验证",
  },
  {
    id: "zhipu",
    name: "Zhipu GLM",
    endpoint: "https://open.bigmodel.cn/api/paas/v4",
    note: "中文任务与工具调用",
    models: ["glm-4-plus", "glm-4-air"],
    status: "已配置",
  },
];

const presets = ["DeepSeek", "Zhipu GLM", "Kimi", "MiniMax", "Custom"];
const permissionModes = [
  {
    id: "ask",
    title: "访问权限",
    text: "每次访问文件、网络或系统资源前先询问。",
  },
  {
    id: "edits",
    title: "接受编辑",
    text: "允许自动写入已授权工作区内的文件修改。",
  },
  {
    id: "plan",
    title: "计划模式",
    text: "先生成计划并等待确认，再执行任何改动。",
  },
  {
    id: "skip",
    title: "跳过全部",
    text: "对可信任务使用宽松策略，减少中断。",
  },
];

export function SettingsWorkspace({
  providers = fallbackProviders,
  activeProviderId,
  onSelectProvider,
  onAddProvider,
  onTestProvider,
  onSaveProvider,
  providerBusy = false,
  providerTestBusy = false,
  permissionMode,
  onPermissionModeChange,
}: SettingsWorkspaceProps) {
  const [section, setSection] = useState<SettingsSection>("providers");
  const [selectedProviderId, setSelectedProviderId] = useState(
    activeProviderId ?? providers[0]?.id ?? fallbackProviders[0].id,
  );
  const [selectedPermissionMode, setSelectedPermissionMode] = useState(
    permissionMode ?? permissionModes[0].id,
  );
  const [isProviderModalOpen, setIsProviderModalOpen] = useState(false);

  useEffect(() => {
    if (activeProviderId) {
      setSelectedProviderId(activeProviderId);
    }
  }, [activeProviderId]);

  useEffect(() => {
    if (permissionMode) {
      setSelectedPermissionMode(permissionMode);
    }
  }, [permissionMode]);

  const activeProvider =
    providers.find((provider) => provider.id === selectedProviderId) ??
    providers[0];

  const handleSelectProvider = (providerId: string) => {
    setSelectedProviderId(providerId);
    onSelectProvider?.(providerId);
  };

  const handlePermissionModeChange = (mode: string) => {
    setSelectedPermissionMode(mode);
    onPermissionModeChange?.(mode);
  };

  return (
    <main className="settings-workspace" aria-labelledby="settings-title">
      <aside className="settings-rail" aria-label="设置分区">
        <div className="settings-brand">
          <span className="settings-brand-mark" aria-hidden="true" />
          <div>
            <p>Yuanbao Desk</p>
            <h1 id="settings-title">Settings</h1>
          </div>
        </div>

        <nav className="settings-nav">
          {sections.map((item) => (
            <button
              key={item.id}
              type="button"
              className={section === item.id ? "is-active" : undefined}
              onClick={() => setSection(item.id)}
              aria-current={section === item.id ? "page" : undefined}
              aria-label={item.label}
            >
              <span>{item.label}</span>
              <small>{item.eyebrow}</small>
            </button>
          ))}
        </nav>
      </aside>

      <section className="settings-pane" aria-live="polite">
        {section === "providers" ? (
          <ProvidersPanel
            providers={providers}
            activeProvider={activeProvider}
            selectedProviderId={selectedProviderId}
            onSelectProvider={handleSelectProvider}
            onAddProvider={() => setIsProviderModalOpen(true)}
            onTestProvider={() => void onTestProvider?.(selectedProviderId)}
            onSaveProvider={() => void onSaveProvider?.(selectedProviderId)}
            providerBusy={providerBusy}
            providerTestBusy={providerTestBusy}
          />
        ) : null}
        {section === "permissions" ? (
          <PermissionsPanel
            selectedMode={selectedPermissionMode}
            onSelectMode={handlePermissionModeChange}
          />
        ) : null}
        {section === "general" ? <GeneralPanel /> : null}
        {section === "im" ? (
          <LowDensityPanel
            title="IM 接入"
            description="连接企业微信、飞书或自建 IM 网关，让会话在桌面与消息渠道之间保持同一套上下文。"
            rows={["Webhook 网关未绑定", "消息签名待配置", "默认回复策略：人工确认"]}
          />
        ) : null}
        {section === "agents" ? (
          <LowDensityPanel
            title="Agents"
            description="管理常驻代理、角色提示词与工作目录。当前由父级会话工作区接入运行时。"
            rows={["默认 Agent：桌面助理", "隔离目录：当前工作区", "工具授权：继承权限模式"]}
          />
        ) : null}
        {section === "skills" ? <SkillsPanel /> : null}
        {section === "computer" ? (
          <LowDensityPanel
            title="Computer Use"
            description="为需要屏幕读取、浏览器操作和桌面控制的任务预留控制面。"
            rows={["屏幕观察：关闭", "浏览器自动化：按需启动", "敏感操作：始终确认"]}
          />
        ) : null}
        {section === "about" ? (
          <LowDensityPanel
            title="关于"
            description="本地智能代理桌面，面向多会话编排、计划任务和可控工具执行。"
            rows={["版本：0.1.0", "界面：民国桌面主题", "运行环境：Tauri + React"]}
          />
        ) : null}
      </section>

      {isProviderModalOpen ? (
        <ProviderModal
          onClose={() => setIsProviderModalOpen(false)}
          onAddProvider={onAddProvider}
          providerBusy={providerBusy}
        />
      ) : null}
    </main>
  );
}

function ProvidersPanel({
  providers,
  activeProvider,
  selectedProviderId,
  onSelectProvider,
  onAddProvider,
  onTestProvider,
  onSaveProvider,
  providerBusy,
  providerTestBusy,
}: {
  providers: SettingsProvider[];
  activeProvider?: SettingsProvider;
  selectedProviderId: string;
  onSelectProvider: (providerId: string) => void;
  onAddProvider: () => void;
  onTestProvider: () => void;
  onSaveProvider: () => void;
  providerBusy: boolean;
  providerTestBusy: boolean;
}) {
  return (
    <div className="settings-panel settings-panel-providers">
      <header className="settings-panel-header">
        <div>
          <p className="settings-kicker">Service Providers</p>
          <h2>服务商设置</h2>
          <p>
            配置模型服务商、接口地址与模型映射。默认使用本地演示服务商，接入运行时后可由父级传入配置。
          </p>
        </div>
        <button className="settings-primary-action" type="button" onClick={onAddProvider}>
          添加服务商
        </button>
      </header>

      <div className="settings-provider-layout">
        <div className="settings-provider-list" aria-label="服务商列表">
          {providers.map((provider) => (
            <button
              key={provider.id}
              type="button"
              className={
                provider.id === selectedProviderId
                  ? "settings-provider-item is-active"
                  : "settings-provider-item"
              }
              onClick={() => onSelectProvider(provider.id)}
              aria-current={provider.id === selectedProviderId ? "page" : undefined}
            >
              <span>
                <strong>{provider.name}</strong>
                <small>{provider.note ?? "未填写备注"}</small>
              </span>
              <em>{provider.status ?? "未启用"}</em>
            </button>
          ))}
        </div>

        <article className="settings-provider-detail">
          <p className="settings-kicker">Selected</p>
          <h3>{activeProvider?.name ?? "未选择服务商"}</h3>
          <dl>
            <div>
              <dt>接口地址</dt>
              <dd>{activeProvider?.endpoint ?? "未配置"}</dd>
            </div>
            <div>
              <dt>模型映射</dt>
              <dd>{activeProvider?.models?.join(" / ") ?? "待设置"}</dd>
            </div>
          </dl>
          <div className="settings-provider-actions">
            <button
              type="button"
              className="settings-secondary-action"
              onClick={onTestProvider}
              disabled={providerTestBusy}
            >
              {providerTestBusy ? "测试中..." : "测试连接"}
            </button>
            <button
              type="button"
              className="settings-primary-action"
              onClick={onSaveProvider}
              disabled={providerBusy}
            >
              {providerBusy ? "保存中..." : "保存配置"}
            </button>
          </div>
        </article>
      </div>
    </div>
  );
}

function PermissionsPanel({
  selectedMode,
  onSelectMode,
}: {
  selectedMode: string;
  onSelectMode: (mode: string) => void;
}) {
  return (
    <div className="settings-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">Guard Rails</p>
          <h2>权限模式</h2>
          <p>选择代理执行任务时的默认确认粒度。模式会影响文件编辑、命令执行与外部访问。</p>
        </div>
      </header>

      <div className="settings-permission-stack" role="radiogroup" aria-label="权限模式">
        {permissionModes.map((mode) => (
          <label
            key={mode.id}
            className={
              selectedMode === mode.id
                ? "settings-permission-option is-selected"
                : "settings-permission-option"
            }
          >
            <input
              type="radio"
              name="permission-mode"
              checked={selectedMode === mode.id}
              onChange={() => onSelectMode(mode.id)}
            />
            <span>
              <strong>{mode.title}</strong>
              <small>{mode.text}</small>
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}

function GeneralPanel() {
  return (
    <div className="settings-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">General</p>
          <h2>通用设置</h2>
          <p>调整桌面显示、语言与联网读取策略。</p>
        </div>
      </header>

      <div className="settings-control-groups">
        <ControlGroup title="主题" options={["暖纸", "深木", "跟随系统"]} />
        <ControlGroup title="语言" options={["简体中文", "English", "自动"]} />
        <ControlGroup title="Reasoning" options={["标准", "深度", "按任务判断"]} />
        <ControlGroup title="WebFetch" options={["询问", "允许可信域", "关闭"]} />
      </div>
    </div>
  );
}

function ControlGroup({ title, options }: { title: string; options: string[] }) {
  return (
    <fieldset className="settings-control-group">
      <legend>{title}</legend>
      {options.map((option, index) => (
        <label key={option}>
          <input type="radio" name={title} defaultChecked={index === 0} />
          <span>{option}</span>
        </label>
      ))}
    </fieldset>
  );
}

function SkillsPanel() {
  return (
    <div className="settings-panel settings-empty-panel">
      <div className="settings-empty-state" role="status">
        <p className="settings-kicker">Skill Library</p>
        <h2>技能</h2>
        <p>技能库尚未接入。这里将展示可安装、已启用和需要更新的本地技能。</p>
        <span aria-hidden="true">空</span>
      </div>
    </div>
  );
}

function LowDensityPanel({
  title,
  description,
  rows,
}: {
  title: string;
  description: string;
  rows: string[];
}) {
  return (
    <div className="settings-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">Workspace</p>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </header>
      <ul className="settings-quiet-list">
        {rows.map((row) => (
          <li key={row}>{row}</li>
        ))}
      </ul>
    </div>
  );
}

function ProviderModal({
  onClose,
  onAddProvider,
  providerBusy,
}: {
  onClose: () => void;
  onAddProvider?: (payload: SettingsProviderPayload) => void | Promise<void>;
  providerBusy: boolean;
}) {
  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const payload: SettingsProviderPayload = {
      name: String(formData.get("name") ?? ""),
      note: String(formData.get("note") ?? ""),
      endpoint: String(formData.get("endpoint") ?? ""),
      apiKey: String(formData.get("apiKey") ?? ""),
      modelMapping: String(formData.get("modelMapping") ?? ""),
      testConnection: String(formData.get("testConnection") ?? ""),
      jsonConfig: String(formData.get("jsonConfig") ?? ""),
    };

    await onAddProvider?.(payload);
    onClose();
  };

  return (
    <div className="settings-modal-backdrop">
      <section
        className="settings-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-provider-modal-title"
      >
        <header className="settings-modal-header">
          <div>
            <p className="settings-kicker">Provider Registry</p>
            <h2 id="settings-provider-modal-title">添加服务商</h2>
          </div>
          <button
            className="settings-icon-button"
            type="button"
            onClick={onClose}
            aria-label="关闭添加服务商"
          >
            ×
          </button>
        </header>

        <div className="settings-preset-row" aria-label="服务商预设">
          {presets.map((preset) => (
            <button key={preset} type="button">
              {preset}
            </button>
          ))}
        </div>

        <form className="settings-provider-form" onSubmit={handleSubmit}>
          <label>
            <span>名称</span>
            <input name="name" type="text" placeholder="例如 DeepSeek 工作号" />
          </label>
          <label>
            <span>备注</span>
            <input name="note" type="text" placeholder="使用场景或额度说明" />
          </label>
          <label>
            <span>接口地址</span>
            <input name="endpoint" type="url" placeholder="https://api.example.com/v1" />
          </label>
          <label>
            <span>API 密钥</span>
            <input name="apiKey" type="password" placeholder="sk-..." />
          </label>
          <label>
            <span>model mapping</span>
            <textarea
              name="modelMapping"
              rows={3}
              placeholder={'chat=deepseek-chat\nreasoner=deepseek-reasoner'}
            />
          </label>
          <label>
            <span>test connection</span>
            <input
              name="testConnection"
              type="text"
              placeholder="GET /models 或自定义探测模型"
            />
          </label>
          <label className="settings-form-wide">
            <span>JSON config</span>
            <textarea
              name="jsonConfig"
              rows={5}
              placeholder={'{\n  "timeout": 30000,\n  "stream": true\n}'}
            />
          </label>

          <footer className="settings-modal-actions">
            <button type="button" className="settings-secondary-action" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="settings-primary-action" disabled={providerBusy}>
              {providerBusy ? "添加中..." : "添加"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

export default SettingsWorkspace;
