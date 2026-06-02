import { formatStatusLabel } from "../../../copy";
import { type SettingsComputerUseCapability, type SettingsComputerUseConfig } from "./settingsTypes";

const fallbackCapabilities: SettingsComputerUseCapability[] = [
  {
    id: "permission-audit",
    label: "权限审计",
    state: "ready",
    detail: "computer_use 会先走运行时审批，再执行 inspect、截图或桌面动作。",
  },
  {
    id: "screen-observation",
    label: "屏幕观察",
    state: "ready",
    detail: "截图 action 已接入运行时，执行时会尝试抓取屏幕并返回预览。",
  },
  {
    id: "desktop-actions",
    label: "桌面动作",
    state: "partial",
    detail: "坐标点击、键入、按键和滚动已进入 executor 管线；可用性取决于宿主桌面后端。",
  },
  {
    id: "browser-dom",
    label: "浏览器 DOM 控制",
    state: "partial",
    detail: "Playwright page-like executor 协议已就绪，可用于 browser inspect/screenshot 和 selector 操作；仍需宿主注入或启用 session。",
  },
];

function formatCheckedAt(value?: number): string {
  if (!value) {
    return "未检查";
  }
  return new Date(value).toLocaleTimeString("zh-CN", { hour12: false });
}

function formatCapabilityState(state: SettingsComputerUseCapability["state"]): string {
  const labels: Record<string, string> = {
    ready: "已接入",
    partial: "部分接入",
    guarded: "需宿主权限",
    pending: "待接入",
    disabled: "已关闭",
    blocked: "不可用",
  };
  return labels[state] ?? state;
}

export function ComputerUsePanel({ value, onChange, onRecheckComputerUse }: { value: SettingsComputerUseConfig; onChange: (next: SettingsComputerUseConfig) => void; onRecheckComputerUse?: () => void | Promise<void> }) {
  const toggles: Array<{ key: "screenshot" | "browserAutomation" | "clipboardAccess" | "systemKeyCombos" | "sensitiveActionConfirm"; label: string; text: string }> = [
    { key: "screenshot", label: "屏幕观察", text: "允许截图用于视觉任务上下文。" },
    { key: "browserAutomation", label: "浏览器自动化", text: "允许打开并控制浏览器会话。" },
    { key: "clipboardAccess", label: "剪贴板访问", text: "允许读取和写入剪贴板。" },
    { key: "systemKeyCombos", label: "系统快捷键", text: "允许使用系统级键盘组合。" },
    { key: "sensitiveActionConfirm", label: "敏感动作确认", text: "破坏性或外部动作前需要确认。" },
  ];
  const capabilities = value.capabilities?.length ? value.capabilities : fallbackCapabilities;

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
        <div className="settings-computer-capabilities" aria-label="电脑操作能力状态">
          {capabilities.map((capability) => (
            <article key={capability.id} className="settings-computer-capability" data-state={capability.state}>
              <span className={capability.state === "ready" ? "settings-status-pill is-pass" : capability.state === "blocked" ? "settings-status-pill is-fail" : "settings-status-pill"}>
                {formatCapabilityState(capability.state)}
              </span>
              <strong>{capability.label}</strong>
              <small>{capability.detail}</small>
            </article>
          ))}
        </div>
        <div className="settings-inline-actions">
          <span>状态：{value.status ? formatStatusLabel(value.status) : "未检查"} · {formatCheckedAt(value.checkedAt)}</span>
          <button type="button" className="settings-secondary-action" onClick={onRecheckComputerUse} disabled={!onRecheckComputerUse} aria-label="重新检查">重新检查</button>
          {!onRecheckComputerUse ? <small className="settings-action-note">桌面权限重新检查尚未实现。</small> : null}
        </div>
      </div>
    </div>
  );
}
