import { formatStatusLabel } from "../../../copy";
import {
  type SettingsProvider,
  type SettingsProviderFeedback,
  type SettingsProviderTestResult,
} from "./settingsTypes";
import { formatProviderModels, formatProviderSuccessDetail } from "./providerUtils";

export function ProvidersPanel({
  providers,
  activeProvider,
  activeProviderId,
  selectedProviderId,
  providerFeedback,
  onSelectProvider,
  onAddProvider,
  onEditProvider,
  onTestProvider,
  onSaveProvider,
  providerBusy,
  providerTestBusy,
}: {
  providers: SettingsProvider[];
  activeProvider?: SettingsProvider;
  activeProviderId?: string;
  selectedProviderId: string;
  providerFeedback?: SettingsProviderFeedback | null;
  onSelectProvider: (providerId: string) => void;
  onAddProvider: () => void;
  onEditProvider: () => void;
  onTestProvider: () => void;
  onSaveProvider: () => void;
  providerBusy: boolean;
  providerTestBusy: boolean;
}) {
  return (
    <div className="settings-panel settings-panel-providers">
      <header className="settings-panel-header">
        <div>
          <p className="settings-kicker">模型供应商</p>
          <h2>供应商控制台</h2>
          <p>管理 API 地址、模型映射、连接检查和当前运行时供应商。</p>
        </div>
        <button className="settings-primary-action" type="button" onClick={onAddProvider} aria-label="添加供应商">
          添加供应商
        </button>
      </header>

      <div className="settings-provider-grid">
        <div className="settings-provider-list" aria-label="供应商列表">
          {providers.map((provider) => (
            <button
              key={provider.id}
              type="button"
              className={provider.id === selectedProviderId ? "settings-provider-item is-active" : "settings-provider-item"}
              onClick={() => onSelectProvider(provider.id)}
              aria-current={provider.id === selectedProviderId ? "page" : undefined}
              aria-label={`选择供应商 ${provider.name}`}
            >
              <span className="settings-provider-dot" aria-hidden="true" />
              <span className="settings-provider-copy">
                <strong>{provider.name}</strong>
                <small>
                  {provider.endpoint}
                  {provider.models?.[0] ? ` / ${provider.models[0]}` : ""}
                </small>
                {provider.note ? <small>{provider.note}</small> : null}
                {provider.lastTest ? <small>{`最近测试：${formatStatusLabel(provider.lastTest.status)}`}</small> : null}
                {provider.lastTest?.ok && formatProviderSuccessDetail(provider.lastTest) ? (
                  <small>{formatProviderSuccessDetail(provider.lastTest)}</small>
                ) : null}
              </span>
              {provider.id === activeProviderId ? (
                <em className="settings-provider-badge">当前</em>
              ) : provider.status ? (
                <em>{formatStatusLabel(provider.status)}</em>
              ) : null}
            </button>
          ))}
        </div>

        <article className="settings-provider-detail" aria-label="已选供应商详情">
          <div>
            <p className="settings-kicker">已选供应商</p>
            <h3>{activeProvider?.name ?? "未选择供应商"}</h3>
          </div>
          {activeProvider ? (
            <ProviderStateSummary
              provider={activeProvider}
              isActive={activeProvider.id === activeProviderId}
              feedback={
                providerFeedback && (!providerFeedback.providerId || providerFeedback.providerId === activeProvider.id)
                  ? providerFeedback
                  : null
              }
            />
          ) : null}
          <dl>
            <div>
              <dt>接口地址</dt>
              <dd>{activeProvider?.endpoint ?? "未配置"}</dd>
            </div>
            <div>
              <dt>模型映射</dt>
              <dd>{formatProviderModels(activeProvider)}</dd>
            </div>
            <div>
              <dt>密钥状态</dt>
              <dd>{activeProvider?.apiKeyMasked ?? "由运行时环境变量提供。"}</dd>
            </div>
            {activeProvider?.lastTest ? <ProviderTestSummaryRows result={activeProvider.lastTest} /> : null}
          </dl>
          <div className="settings-provider-actions">
            <button type="button" className="settings-secondary-action" onClick={onEditProvider}>编辑</button>
            <button type="button" className="settings-secondary-action" onClick={onTestProvider} disabled={providerTestBusy}>
              {providerTestBusy ? "测试中..." : "测试连接"}
            </button>
            <button type="button" className="settings-primary-action" onClick={onSaveProvider} disabled={providerBusy}>
              {providerBusy ? "保存中..." : "保存"}
            </button>
          </div>
        </article>
      </div>
    </div>
  );
}

function ProviderStateSummary({
  provider,
  isActive,
  feedback,
}: {
  provider: SettingsProvider;
  isActive: boolean;
  feedback?: SettingsProviderFeedback | null;
}) {
  const currentModel = provider.modelMapping?.main || provider.models?.[0] || "未配置";
  const lastTest = provider.lastTest;
  const connectionLabel = lastTest ? (lastTest.ok ? "测试通过" : "测试失败") : "尚未测试";
  const connectionDetail = lastTest
    ? lastTest.ok
      ? formatProviderSuccessDetail(lastTest) || lastTest.message
      : lastTest.errorSummary || lastTest.message
    : "开始正式任务前，请先运行测试连接验证该供应商。";

  return (
    <div className="settings-provider-state" aria-label="供应商状态摘要">
      <div>
        <span className={isActive ? "settings-status-pill is-active" : "settings-status-pill"}>
          {isActive ? "当前供应商" : "仅已选中"}
        </span>
        <strong>{currentModel}</strong>
        <small>当前模型</small>
      </div>
      <div>
        <span className={lastTest?.ok ? "settings-status-pill is-pass" : lastTest ? "settings-status-pill is-fail" : "settings-status-pill"}>
          {connectionLabel}
        </span>
        <strong>{connectionDetail}</strong>
        <small>连接状态</small>
      </div>
      {feedback ? (
        <div className={`settings-provider-feedback is-${feedback.tone}`} role="status">
          <strong>{feedback.title}</strong>
          <span>{feedback.message}</span>
          {feedback.detail ? <small>{feedback.detail}</small> : null}
        </div>
      ) : null}
    </div>
  );
}

function ProviderTestSummaryRows({ result }: { result: SettingsProviderTestResult }) {
  const successDetail = formatProviderSuccessDetail(result);
  const failureDetail = result.errorSummary ?? result.message;

  return (
    <>
      <div>
        <dt>最近测试</dt>
        <dd>{`最近测试：${formatStatusLabel(result.status)}`}</dd>
      </div>
      {result.ok ? (
        <div>
          <dt>模型 / 结束</dt>
          <dd>{successDetail || result.message}</dd>
        </div>
      ) : (
        <div>
          <dt>失败原因</dt>
          <dd>{failureDetail}</dd>
        </div>
      )}
      {result.checkedAt ? (
        <div>
          <dt>检查时间</dt>
          <dd>{new Date(result.checkedAt).toLocaleString()}</dd>
        </div>
      ) : null}
    </>
  );
}
