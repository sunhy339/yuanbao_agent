import { useState, type FormEvent } from "react";
import { isProviderApiFormatSupported } from "@shared";
import {
  type SettingsProvider,
  type SettingsProviderTestResult,
  type SettingsProviderPayload,
  type ProviderPresetId,
  providerPresets,
  providerApiFormatOptions,
} from "./settingsTypes";
import {
  type ProviderFormDraft,
  createProviderDraft,
  toProviderPayload,
  parseProviderConfigText,
  isDirectApiKey,
  formatProviderTestResult,
  buildProviderJson,
} from "./providerUtils";

export function ProviderModal({
  mode,
  provider,
  onClose,
  onAddProvider,
  onEditProvider,
  onTestProviderConfig,
  providerBusy,
  providerTestBusy,
}: {
  mode: "add" | "edit";
  provider?: SettingsProvider;
  onClose: () => void;
  onAddProvider?: (payload: SettingsProviderPayload) => void | Promise<void>;
  onEditProvider?: (providerId: string, payload: SettingsProviderPayload) => void | Promise<void>;
  onTestProviderConfig?: (payload: SettingsProviderPayload) => void | SettingsProviderTestResult | Promise<void | SettingsProviderTestResult>;
  providerBusy: boolean;
  providerTestBusy: boolean;
}) {
  const [draft, setDraft] = useState(() => createProviderDraft(provider));
  const [testResult, setTestResult] = useState<SettingsProviderTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const title = mode === "edit" ? "编辑供应商" : "添加供应商";
  const parsedConfig = parseProviderConfigText(draft.jsonConfig);
  const detectedApiKeyEnvVarName =
    !draft.apiKey.trim() || !isDirectApiKey(draft.apiKey)
      ? parsedConfig.apiKeyEnvVarName
      : undefined;
  const draftError = validateProviderDraft(draft);

  const updateDraft = (patch: Partial<ProviderFormDraft>) => {
    setTestResult(null);
    setTestError(null);
    setDraft((current) => ({ ...current, ...patch }));
  };

  const applyPreset = (presetId: ProviderPresetId) => {
    const preset = providerPresets.find((item) => item.id === presetId);
    if (!preset) {
      return;
    }
    updateDraft({
      preset: preset.id,
      name: preset.id === "custom" ? draft.name : preset.label,
      endpoint: preset.endpoint,
      apiFormat: preset.apiFormat,
      mainModel: preset.mainModel,
      haikuModel: preset.haikuModel,
      sonnetModel: preset.sonnetModel,
      opusModel: preset.opusModel,
    });
  };

  const handleTestProvider = async () => {
    setTestResult(null);
    setTestError(null);
    if (draftError) {
      setTestError(draftError);
      return;
    }
    try {
      const result = await onTestProviderConfig?.(toProviderPayload(draft));
      if (result) {
        setTestResult(result);
      }
    } catch (reason) {
      setTestError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (draftError) {
      setTestError(draftError);
      return;
    }
    const payload = toProviderPayload(draft);
    if (mode === "edit" && provider) {
      await onEditProvider?.(provider.id, payload);
    } else {
      await onAddProvider?.(payload);
    }
    onClose();
  };

  return (
    <div className="settings-modal-backdrop" role="presentation">
      <form className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="provider-modal-title" onSubmit={handleSubmit}>
        <header className="settings-modal-header">
          <div>
            <p className="settings-kicker">供应商</p>
            <h2 id="provider-modal-title">{title}</h2>
          </div>
          <button type="button" className="settings-icon-button" onClick={onClose} aria-label="关闭">x</button>
        </header>

        <div className="settings-provider-form">
          <div className="settings-preset-row" aria-label="供应商预设">
            {providerPresets.map((preset) => (
              <button
                key={preset.id}
                type="button"
                className={draft.preset === preset.id ? "is-active" : undefined}
                onClick={() => applyPreset(preset.id)}
              >
                {preset.label}
              </button>
            ))}
          </div>

          <label className="settings-field" htmlFor="provider-name">
            <span>名称 *</span>
            <input id="provider-name" value={draft.name} onChange={(event) => updateDraft({ name: event.currentTarget.value })} required />
          </label>
          <label className="settings-field" htmlFor="provider-note">
            <span>备注</span>
            <input id="provider-note" value={draft.note} onChange={(event) => updateDraft({ note: event.currentTarget.value })} placeholder="可选备注..." />
          </label>
          <label className="settings-field" htmlFor="provider-endpoint">
            <span>接口地址</span>
            <input id="provider-endpoint" value={draft.endpoint} onChange={(event) => updateDraft({ endpoint: event.currentTarget.value })} placeholder="https://api.example.com/v1" />
          </label>
          <label className="settings-field" htmlFor="provider-api-format">
            <span>API 格式</span>
            <select id="provider-api-format" value={draft.apiFormat} onChange={(event) => updateDraft({ apiFormat: event.currentTarget.value as typeof draft.apiFormat })}>
              {providerApiFormatOptions.map((option) => (
                <option
                  key={option.value}
                  value={option.value}
                  disabled={!isProviderApiFormatSupported(option.value) && draft.apiFormat !== option.value}
                >
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="settings-field" htmlFor="provider-api-key">
            <span>API 密钥</span>
            <input id="provider-api-key" type="password" value={draft.apiKey} onChange={(event) => updateDraft({ apiKey: event.currentTarget.value })} placeholder="sk-..." />
          </label>

          <fieldset className="settings-model-grid">
            <legend>模型映射</legend>
            <label className="settings-field" htmlFor="provider-main-model">
              <span>主模型 *</span>
              <input id="provider-main-model" value={draft.mainModel} onChange={(event) => updateDraft({ mainModel: event.currentTarget.value })} required />
            </label>
            <label className="settings-field" htmlFor="provider-haiku-model">
              <span>Haiku 模型</span>
              <input id="provider-haiku-model" value={draft.haikuModel} onChange={(event) => updateDraft({ haikuModel: event.currentTarget.value })} />
            </label>
            <label className="settings-field" htmlFor="provider-sonnet-model">
              <span>Sonnet 模型</span>
              <input id="provider-sonnet-model" value={draft.sonnetModel} onChange={(event) => updateDraft({ sonnetModel: event.currentTarget.value })} />
            </label>
            <label className="settings-field" htmlFor="provider-opus-model">
              <span>Opus 模型</span>
              <input id="provider-opus-model" value={draft.opusModel} onChange={(event) => updateDraft({ opusModel: event.currentTarget.value })} />
            </label>
          </fieldset>

          <label className="settings-field settings-form-wide" htmlFor="provider-json">
            <span>设置 JSON / 环境变量</span>
            <textarea id="provider-json" value={draft.jsonConfig} onChange={(event) => updateDraft({ jsonConfig: event.currentTarget.value })} rows={8} placeholder={buildProviderJson(draft)} />
          </label>

          {detectedApiKeyEnvVarName ? (
            <p className="settings-provider-feedback is-info">检测到环境变量：{detectedApiKeyEnvVarName}</p>
          ) : null}
          {draftError ? <p className="settings-provider-feedback is-danger">{draftError}</p> : null}
          {testResult ? <pre className="settings-json-box">{formatProviderTestResult(testResult)}</pre> : null}
          {testError ? <p className="settings-provider-feedback is-danger">{testError}</p> : null}
        </div>

        <footer className="settings-modal-footer">
          <p className="settings-modal-hint">
            {draftError ? "修正上方提示后即可继续。" : "保存前可以先测试连接，确认模型映射和密钥可用。"}
          </p>
          <button type="button" className="settings-secondary-action" onClick={onClose}>取消</button>
          <button type="button" className="settings-secondary-action" onClick={handleTestProvider} disabled={providerTestBusy || Boolean(draftError)} aria-label="测试连接">{providerTestBusy ? "测试中..." : "测试连接"}</button>
          <button type="submit" className="settings-primary-action" disabled={providerBusy || Boolean(draftError)} aria-label={mode === "edit" ? "保存" : "添加"}>{mode === "edit" ? "保存" : "添加"}</button>
        </footer>
      </form>
    </div>
  );
}

function validateProviderDraft(draft: ProviderFormDraft): string | null {
  if (!draft.name.trim()) {
    return "请输入供应商名称。";
  }
  if (draft.endpoint.trim() && !/^https?:\/\//i.test(draft.endpoint.trim())) {
    return "接口地址应以 http:// 或 https:// 开头。";
  }
  if (!draft.mainModel.trim()) {
    return "请输入主模型。";
  }
  const configText = draft.jsonConfig.trim();
  if (/^[{[]/.test(configText)) {
    try {
      JSON.parse(configText);
    } catch {
      return "设置 JSON 格式无效。";
    }
  }
  return null;
}
