import { useMemo, useState } from "react";
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
  const [providerQuery, setProviderQuery] = useState("");
  const [providerFilter, setProviderFilter] = useState<"all" | "active" | "ready" | "needs_attention">("all");
  const visibleProviders = useMemo(() => {
    const query = providerQuery.trim().toLowerCase();
    return providers.filter((provider) => {
      const isActive = provider.id === activeProviderId;
      const isReady = Boolean(provider.lastTest?.ok || provider.status === "ready");
      const needsAttention = !isReady && !isActive;
      if (providerFilter === "active" && !isActive) return false;
      if (providerFilter === "ready" && !isReady) return false;
      if (providerFilter === "needs_attention" && !needsAttention) return false;
      if (!query) return true;
      return [
        provider.name,
        provider.endpoint,
        provider.note ?? "",
        provider.status ?? "",
        provider.apiFormat ?? "",
        provider.apiKeyMasked ?? "",
        ...(provider.models ?? []),
        provider.modelMapping?.main ?? "",
        provider.modelMapping?.haiku ?? "",
        provider.modelMapping?.sonnet ?? "",
        provider.modelMapping?.opus ?? "",
      ].some((value) => value.toLowerCase().includes(query));
    });
  }, [activeProviderId, providerFilter, providerQuery, providers]);

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
        <div>
          <div className="settings-provider-tools" aria-label="供应商筛选">
            <label className="settings-field">
              <span>搜索</span>
              <input
                aria-label="搜索供应商"
                value={providerQuery}
                placeholder="名称、模型、接口地址"
                onChange={(event) => setProviderQuery(event.currentTarget.value)}
              />
            </label>
            <div className="settings-filter-tabs" role="tablist" aria-label="供应商状态筛选">
              {[
                { id: "all", label: "全部" },
                { id: "active", label: "当前" },
                { id: "ready", label: "可用" },
                { id: "needs_attention", label: "需处理" },
              ].map((option) => (
                <button
                  key={option.id}
                  type="button"
                  role="tab"
                  aria-selected={providerFilter === option.id}
                  onClick={() => setProviderFilter(option.id as typeof providerFilter)}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <small>{visibleProviders.length}/{providers.length}</small>
          </div>
          <div className="settings-provider-list" aria-label="供应商列表">
            {visibleProviders.length ? (
              visibleProviders.map((provider) => (
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
              ))
            ) : (
              <div className="settings-empty-state settings-provider-empty">
                <div>
                  <span aria-hidden="true">⌕</span>
                  <strong>没有匹配的供应商</strong>
                  <p>调整搜索词或状态筛选后再试。</p>
                </div>
              </div>
            )}
          </div>
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
            <button type="button" className="settings-primary-action" onClick={onSaveProvider} disabled={providerBusy || !activeProvider}>
              {providerBusy ? "保存中..." : activeProvider?.id === activeProviderId ? "保存配置" : "设为当前"}
            </button>
            <span className="settings-action-note">
              {activeProvider?.id === activeProviderId ? "会保存当前供应商配置。" : "保存后会把该供应商设为当前运行时默认。"}
            </span>
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
