import { formatStatusLabel } from "../../../copy";
import { type SettingsComputerUseConfig } from "./settingsTypes";

export function ComputerUsePanel({ value, onChange, onRecheckComputerUse }: { value: SettingsComputerUseConfig; onChange: (next: SettingsComputerUseConfig) => void; onRecheckComputerUse?: () => void | Promise<void> }) {
  const toggles: Array<{ key: keyof SettingsComputerUseConfig; label: string; text: string }> = [
    { key: "screenshot", label: "屏幕观察", text: "允许截图用于视觉任务上下文。" },
    { key: "browserAutomation", label: "浏览器自动化", text: "允许打开并控制浏览器会话。" },
    { key: "clipboardAccess", label: "剪贴板访问", text: "允许读取和写入剪贴板。" },
    { key: "systemKeyCombos", label: "系统快捷键", text: "允许使用系统级键盘组合。" },
    { key: "sensitiveActionConfirm", label: "敏感动作确认", text: "破坏性或外部动作前需要确认。" },
  ];

  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">电脑操作</p>
          <h2>电脑操作</h2>
          <p>控制本地桌面能力，例如截图、浏览器自动化、剪贴板访问和高风险确认。</p>
        </div>
      </header>
      <div className="settings-form-stack">
        {toggles.map((toggle) => (
          <label key={toggle.key} className="settings-toggle-card">
            <input type="checkbox" checked={Boolean(value[toggle.key])} onChange={(event) => onChange({ ...value, [toggle.key]: event.currentTarget.checked })} />
            <span>
              <strong>{toggle.label}</strong>
              <small>{toggle.text}</small>
            </span>
          </label>
        ))}
        <div className="settings-inline-actions">
          <span>状态：{value.status ? formatStatusLabel(value.status) : "未检查"}</span>
          <button type="button" className="settings-secondary-action" onClick={onRecheckComputerUse} disabled={!onRecheckComputerUse} aria-label="重新检查">重新检查</button>
          {!onRecheckComputerUse ? <small className="settings-action-note">桌面权限重新检查尚未实现。</small> : null}
        </div>
      </div>
    </div>
  );
}
