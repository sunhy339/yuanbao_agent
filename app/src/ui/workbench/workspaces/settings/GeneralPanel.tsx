import {
  type SettingsGeneralConfig,
  type ThemeMode,
  type DensityMode,
  type RadiusMode,
  type MotionMode,
  type AccentColor,
  type LanguageMode,
  type ReasoningEffort,
  fallbackGeneral,
} from "./settingsTypes";

export function GeneralPanel({ value, onChange }: { value: SettingsGeneralConfig; onChange: (next: SettingsGeneralConfig) => void }) {
  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header">
        <div>
          <p className="settings-kicker">通用</p>
          <h2>外观偏好</h2>
          <p>设置工作台主题、密度、强调色、动效、语言、推理力度和 Web 预检行为。</p>
        </div>
        <button type="button" className="settings-secondary-action" onClick={() => onChange(fallbackGeneral)}>
          恢复默认
        </button>
      </header>
      <div className="settings-form-stack">
        <SegmentedControl
          label="主题"
          name="theme"
          value={value.theme}
          options={[
            { value: "light", label: "浅色" },
            { value: "dark", label: "深色" },
            { value: "system", label: "跟随系统" },
          ]}
          onChange={(theme) => onChange({ ...value, theme: theme as ThemeMode })}
        />
        <SegmentedControl
          label="密度"
          name="density"
          value={value.density}
          options={[
            { value: "comfortable", label: "舒适" },
            { value: "compact", label: "紧凑" },
          ]}
          onChange={(density) => onChange({ ...value, density: density as DensityMode })}
        />
        <SegmentedControl
          label="圆角"
          name="radius"
          value={value.radius}
          options={[
            { value: "sm", label: "小" },
            { value: "md", label: "中" },
            { value: "lg", label: "大" },
          ]}
          onChange={(radius) => onChange({ ...value, radius: radius as RadiusMode })}
        />
        <SegmentedControl
          label="动效"
          name="motion"
          value={value.motion}
          options={[
            { value: "reduced", label: "减少" },
            { value: "subtle", label: "轻微" },
            { value: "expressive", label: "丰富" },
          ]}
          onChange={(motion) => onChange({ ...value, motion: motion as MotionMode })}
        />
        <SegmentedControl
          label="强调色"
          name="accent"
          value={value.accentColor}
          options={[
            { value: "cyan", label: "青色" },
            { value: "violet", label: "紫色" },
            { value: "green", label: "绿色" },
            { value: "amber", label: "琥珀" },
            { value: "rose", label: "玫瑰" },
          ]}
          onChange={(accentColor) => onChange({ ...value, accentColor: accentColor as AccentColor })}
        />
        <label className="settings-field" htmlFor="appearance-transparency">
          <span>透明度 {Math.round(value.transparency * 100)}%</span>
          <input
            id="appearance-transparency"
            type="range"
            min="0.58"
            max="0.96"
            step="0.02"
            value={value.transparency}
            onChange={(event) => onChange({ ...value, transparency: Number(event.currentTarget.value) })}
          />
        </label>
        <label className="settings-field" htmlFor="appearance-font-scale">
          <span>字体缩放 {Math.round(value.fontScale * 100)}%</span>
          <input
            id="appearance-font-scale"
            type="range"
            min="0.92"
            max="1.12"
            step="0.02"
            value={value.fontScale}
            onChange={(event) => onChange({ ...value, fontScale: Number(event.currentTarget.value) })}
          />
        </label>
        <SegmentedControl
          label="语言"
          name="language"
          value={value.language}
          options={[
            { value: "en", label: "英文" },
            { value: "zh", label: "中文" },
            { value: "auto", label: "自动" },
          ]}
          onChange={(language) => onChange({ ...value, language: language as LanguageMode })}
        />
        <SegmentedControl
          label="推理力度"
          name="reasoning"
          value={value.reasoningEffort}
          options={[
            { value: "low", label: "低" },
            { value: "medium", label: "中" },
            { value: "high", label: "高" },
            { value: "max", label: "最高" },
          ]}
          onChange={(reasoningEffort) => onChange({ ...value, reasoningEffort: reasoningEffort as ReasoningEffort })}
        />
        <label className="settings-toggle-card" htmlFor="webfetch-preflight">
          <input
            id="webfetch-preflight"
            type="checkbox"
            checked={value.webFetchPreflight}
            onChange={(event) => onChange({ ...value, webFetchPreflight: event.currentTarget.checked })}
          />
          <span>
            <strong>跳过 WebFetch 域名预检</strong>
            <small>为了本地运行时兼容建议保持开启，除非需要上游安全检查。</small>
          </span>
        </label>
      </div>
    </div>
  );
}

export function SegmentedControl({
  label,
  name,
  value,
  options,
  onChange,
}: {
  label: string;
  name: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <fieldset className="settings-segmented">
      <legend>{label}</legend>
      {options.map((option) => (
        <label key={option.value}>
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
          />
          <span>{option.label}</span>
        </label>
      ))}
    </fieldset>
  );
}
