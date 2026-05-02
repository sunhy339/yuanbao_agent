import type { SettingsGeneralConfig } from "../settings/SettingsWorkspace";
import { Button, StatusBadge } from "../../../v2/components/ui";
import "./appearance.css";

export interface AppearanceWorkspaceProps {
  value: SettingsGeneralConfig;
  workspaceName: string;
  providerLabel: string;
  onChange?: (next: SettingsGeneralConfig) => void;
  onOpenSettings?: () => void;
}

const themeOptions: Array<{ id: SettingsGeneralConfig["theme"]; label: string; note: string }> = [
  { id: "dark", label: "深色", note: "主要桌面工作台主题" },
  { id: "light", label: "浅色", note: "适合审阅和白天操作" },
  { id: "system", label: "跟随系统", note: "遵循操作系统偏好" },
];

const languageOptions: Array<{ id: SettingsGeneralConfig["language"]; label: string }> = [
  { id: "en", label: "英文" },
  { id: "zh", label: "中文" },
  { id: "auto", label: "自动" },
];

const densityOptions: Array<{ id: SettingsGeneralConfig["density"]; label: string }> = [
  { id: "comfortable", label: "舒适" },
  { id: "compact", label: "紧凑" },
];

const radiusOptions: Array<{ id: SettingsGeneralConfig["radius"]; label: string }> = [
  { id: "sm", label: "小" },
  { id: "md", label: "中" },
  { id: "lg", label: "大" },
];

const motionOptions: Array<{ id: SettingsGeneralConfig["motion"]; label: string }> = [
  { id: "reduced", label: "减少" },
  { id: "subtle", label: "轻微" },
  { id: "expressive", label: "丰富" },
];

const accentOptions: Array<{ id: SettingsGeneralConfig["accentColor"]; label: string }> = [
  { id: "cyan", label: "青色" },
  { id: "violet", label: "紫色" },
  { id: "green", label: "绿色" },
  { id: "amber", label: "琥珀" },
  { id: "rose", label: "玫瑰" },
];

const reasoningOptions: Array<{ id: SettingsGeneralConfig["reasoningEffort"]; label: string }> = [
  { id: "low", label: "低" },
  { id: "medium", label: "中" },
  { id: "high", label: "高" },
  { id: "max", label: "最大" },
];

function optionLabel<T extends string>(options: Array<{ id: T; label: string }>, value: T) {
  return options.find((option) => option.id === value)?.label ?? value;
}

export function AppearanceWorkspace({
  value,
  workspaceName,
  providerLabel,
  onChange,
  onOpenSettings,
}: AppearanceWorkspaceProps) {
  const apply = (next: SettingsGeneralConfig) => {
    onChange?.(next);
  };

  return (
    <main className="appearance-workspace" aria-labelledby="appearance-title">
      <section className="appearance-command-strip">
        <div>
          <p className="yb-kicker">桌面控制</p>
          <h1 id="appearance-title">外观</h1>
          <p>调整工作台主题、语言偏好和运行时预检姿态。</p>
        </div>
        <dl>
          <div>
            <dt>主题</dt>
            <dd>{optionLabel(themeOptions, value.theme)}</dd>
          </div>
          <div>
            <dt>密度</dt>
            <dd>{optionLabel(densityOptions, value.density)}</dd>
          </div>
          <div>
            <dt>强调色</dt>
            <dd>{optionLabel(accentOptions, value.accentColor)}</dd>
          </div>
          <div>
            <dt>语言</dt>
            <dd>{optionLabel(languageOptions, value.language)}</dd>
          </div>
          <div>
            <dt>推理</dt>
            <dd>{optionLabel(reasoningOptions, value.reasoningEffort)}</dd>
          </div>
        </dl>
      </section>

      <section className="appearance-grid">
        <section className="appearance-control-panel" aria-label="外观控制">
          <header>
            <div>
              <p className="yb-kicker">主题</p>
              <h2>工作台皮肤</h2>
            </div>
            <Button variant="ghost" onClick={onOpenSettings} disabled={!onOpenSettings} disabledReason="设置不可用">
              打开设置
            </Button>
          </header>

          <div className="appearance-option-grid">
            {themeOptions.map((option) => (
              <button
                key={option.id}
                type="button"
                className="appearance-choice"
                data-selected={value.theme === option.id}
                aria-pressed={value.theme === option.id}
                onClick={() => apply({ ...value, theme: option.id })}
              >
                <strong>{option.label}</strong>
                <span>{option.note}</span>
              </button>
            ))}
          </div>

          <div className="appearance-control-block">
            <div>
              <p className="yb-kicker">密度</p>
              <h3>信息节奏</h3>
            </div>
            <div className="appearance-segmented" role="group" aria-label="密度">
              {densityOptions.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  aria-pressed={value.density === option.id}
                  onClick={() => apply({ ...value, density: option.id })}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="appearance-control-block">
            <div>
              <p className="yb-kicker">圆角</p>
              <h3>面板几何</h3>
            </div>
            <div className="appearance-segmented" role="group" aria-label="圆角">
              {radiusOptions.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  aria-pressed={value.radius === option.id}
                  onClick={() => apply({ ...value, radius: option.id })}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="appearance-control-block">
            <div>
              <p className="yb-kicker">动效</p>
              <h3>交互反馈</h3>
            </div>
            <div className="appearance-segmented" role="group" aria-label="动效">
              {motionOptions.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  aria-pressed={value.motion === option.id}
                  onClick={() => apply({ ...value, motion: option.id })}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="appearance-control-block">
            <div>
              <p className="yb-kicker">强调色</p>
              <h3>状态配色</h3>
            </div>
            <div className="appearance-swatch-row" role="group" aria-label="强调色">
              {accentOptions.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  data-accent={option.id}
                  aria-pressed={value.accentColor === option.id}
                  onClick={() => apply({ ...value, accentColor: option.id })}
                >
                  <span aria-hidden="true" />
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="appearance-slider-grid">
            <label>
              <span>透明度 {Math.round(value.transparency * 100)}%</span>
              <input
                type="range"
                min="0.58"
                max="0.96"
                step="0.02"
                value={value.transparency}
                onChange={(event) => apply({ ...value, transparency: Number(event.currentTarget.value) })}
              />
            </label>
            <label>
              <span>字体缩放 {Math.round(value.fontScale * 100)}%</span>
              <input
                type="range"
                min="0.92"
                max="1.12"
                step="0.02"
                value={value.fontScale}
                onChange={(event) => apply({ ...value, fontScale: Number(event.currentTarget.value) })}
              />
            </label>
          </div>

          <div className="appearance-control-block">
            <div>
              <p className="yb-kicker">语言</p>
              <h3>界面文案</h3>
            </div>
            <div className="appearance-segmented" role="group" aria-label="语言">
              {languageOptions.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  aria-pressed={value.language === option.id}
                  onClick={() => apply({ ...value, language: option.id })}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="appearance-control-block">
            <div>
              <p className="yb-kicker">推理</p>
              <h3>默认强度</h3>
            </div>
            <div className="appearance-segmented" role="group" aria-label="推理强度">
              {reasoningOptions.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  aria-pressed={value.reasoningEffort === option.id}
                  onClick={() => apply({ ...value, reasoningEffort: option.id })}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <label className="appearance-toggle">
            <input
              type="checkbox"
              checked={value.webFetchPreflight}
              onChange={(event) => apply({ ...value, webFetchPreflight: event.target.checked })}
            />
            <span aria-hidden="true" />
            <strong>外部上下文前进行网页预检</strong>
            <small>当任务依赖最新文档或快速变化的 API 时，建议保持启用。</small>
          </label>
        </section>

        <aside className="appearance-preview-panel" aria-label="主题预览">
          <p className="yb-kicker">预览</p>
          <h2>{workspaceName}</h2>
          <div className="appearance-preview-window" data-preview-theme={value.theme}>
            <header>
              <strong>Yuanbao Workbench</strong>
              <StatusBadge label={providerLabel} tone="success" compact />
            </header>
            <div className="appearance-preview-body">
              <div>
                <span>运行时</span>
                <strong>就绪</strong>
              </div>
              <div>
                <span>队列</span>
                <strong>空闲</strong>
              </div>
              <div>
                <span>追踪</span>
                <strong>待命</strong>
              </div>
            </div>
            <footer>
              <span>命令通道</span>
              <Button size="sm" variant="primary" disabled disabledReason="仅预览">发送</Button>
            </footer>
          </div>
          <p className="appearance-preview-note">
            当前外壳会立即跟随这些设置；更深层的运行时持久化由设置页保存路径处理。
          </p>
        </aside>
      </section>
    </main>
  );
}
