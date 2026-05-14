import { type SettingsIMConfig } from "./settingsTypes";
import { SegmentedControl } from "./GeneralPanel";

export function IMPanel({ value, onChange, onTestIM }: { value: SettingsIMConfig; onChange: (next: SettingsIMConfig) => void; onTestIM?: () => void | Promise<void> }) {
  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">消息桥接</p>
          <h2>消息桥接</h2>
          <p>连接飞书、企业微信或自定义 Webhook，让外部消息渠道进入会话。</p>
        </div>
      </header>
      <div className="settings-form-stack">
        <label className="settings-toggle-card" htmlFor="im-enabled">
          <input id="im-enabled" type="checkbox" checked={value.enabled} onChange={(event) => onChange({ ...value, enabled: event.currentTarget.checked })} />
          <span>
            <strong>启用消息网关</strong>
            <small>关闭后会保留配置，但停止接收外部消息。</small>
          </span>
        </label>
        <label className="settings-field" htmlFor="im-provider">
          <span>渠道</span>
          <select id="im-provider" value={value.provider} onChange={(event) => onChange({ ...value, provider: event.currentTarget.value })}>
            <option value="feishu">飞书</option>
            <option value="wecom">企业微信</option>
            <option value="custom">自定义网关</option>
          </select>
        </label>
        <label className="settings-field" htmlFor="im-webhook">
          <span>Webhook 地址</span>
          <input id="im-webhook" type="url" value={value.webhookUrl} placeholder="https://example.com/im/webhook" onChange={(event) => onChange({ ...value, webhookUrl: event.currentTarget.value })} />
        </label>
        <SegmentedControl
          label="默认回复模式"
          name="im-reply-mode"
          value={value.defaultReplyMode}
          options={[
            { value: "manual", label: "手动" },
            { value: "auto", label: "自动回复" },
            { value: "silent", label: "静默记录" },
          ]}
          onChange={(defaultReplyMode) => onChange({ ...value, defaultReplyMode: defaultReplyMode as SettingsIMConfig["defaultReplyMode"] })}
        />
        <div className="settings-inline-actions">
          <span>签名密钥：{value.signingSecretSet ? "已配置" : "未配置"}</span>
          <button type="button" className="settings-secondary-action" onClick={onTestIM} disabled={!onTestIM} aria-label="测试 IM 连接">测试消息桥接</button>
          {!onTestIM ? <small className="settings-action-note">当前桌面版本尚未接入运行时消息桥接测试。</small> : null}
        </div>
      </div>
    </div>
  );
}
