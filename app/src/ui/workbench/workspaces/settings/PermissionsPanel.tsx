import { useMemo, useState } from "react";
import { permissionModes } from "./settingsTypes";
import type { SettingsPermissionRule } from "./settingsTypes";

interface PermissionsPanelProps {
  selectedMode: string;
  rules?: SettingsPermissionRule[];
  busyRuleId?: string | null;
  onSelectMode: (mode: string) => void;
  onClearRule?: (capability: string) => void | Promise<void>;
}

export function PermissionsPanel({
  selectedMode,
  rules = [],
  busyRuleId = null,
  onSelectMode,
  onClearRule,
}: PermissionsPanelProps) {
  const [ruleQuery, setRuleQuery] = useState("");
  const [ruleFilter, setRuleFilter] = useState<"all" | "allow" | "ask" | "deny">("all");
  const visibleRules = useMemo(() => {
    const query = ruleQuery.trim().toLowerCase();
    return rules.filter((rule) => {
      const mode = String(rule.mode).toLowerCase();
      const modeLabel = rule.modeLabel.toLowerCase();
      if (ruleFilter !== "all" && !mode.includes(ruleFilter) && !modeLabel.includes(ruleFilter)) {
        return false;
      }
      if (!query) return true;
      return [
        rule.capability,
        rule.label,
        rule.modeLabel,
        rule.scope,
        rule.description ?? "",
      ].some((value) => value.toLowerCase().includes(query));
    });
  }, [ruleFilter, ruleQuery, rules]);

  function selectMode(mode: string) {
    if (
      mode === "skip" &&
      selectedMode !== "skip" &&
      !window.confirm("切换到自主执行会减少审批提示，仅建议在受控环境中使用。继续切换？")
    ) {
      return;
    }
    onSelectMode(mode);
  }

  function clearRule(rule: SettingsPermissionRule) {
    if (window.confirm(`恢复“${rule.label}”的默认权限规则？`)) {
      void onClearRule?.(rule.capability);
    }
  }

  return (
    <div className="settings-panel settings-narrow-panel">
      <header className="settings-panel-header settings-panel-header-plain">
        <div>
          <p className="settings-kicker">权限模式</p>
          <h2>权限模式</h2>
          <p>选择运行时在编辑、命令和高风险动作前如何请求审批。</p>
        </div>
      </header>
      <div className="settings-card-stack" role="radiogroup" aria-label="权限模式">
        {permissionModes.map((mode) => (
          <label key={mode.id} className={selectedMode === mode.id ? "settings-choice-card is-selected" : "settings-choice-card"}>
            <input type="radio" name="permission-mode" checked={selectedMode === mode.id} onChange={() => selectMode(mode.id)} />
            <span>
              <strong>{mode.title}</strong>
              <small>{mode.text}</small>
            </span>
          </label>
        ))}
      </div>
      <section className="settings-rule-section" aria-labelledby="permission-rules-title">
        <div className="settings-subheader">
          <div>
            <p className="settings-kicker">覆盖规则</p>
            <h3 id="permission-rules-title">始终允许与显式规则</h3>
          </div>
          <span>{visibleRules.length}/{rules.length} 条</span>
        </div>
        {rules.length ? (
          <>
            <div className="settings-rule-tools" aria-label="权限规则筛选">
              <label className="settings-field">
                <span>搜索规则</span>
                <input
                  aria-label="搜索权限规则"
                  value={ruleQuery}
                  placeholder="能力、范围、说明"
                  onChange={(event) => setRuleQuery(event.currentTarget.value)}
                />
              </label>
              <div className="settings-filter-tabs" role="tablist" aria-label="权限规则模式筛选">
                {[
                  { id: "all", label: "全部" },
                  { id: "allow", label: "允许" },
                  { id: "ask", label: "询问" },
                  { id: "deny", label: "拒绝" },
                ].map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    role="tab"
                    aria-selected={ruleFilter === option.id}
                    onClick={() => setRuleFilter(option.id as typeof ruleFilter)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
            {visibleRules.length ? (
              <div className="settings-rule-list">
                {visibleRules.map((rule) => (
                  <article key={rule.capability} className="settings-rule-row">
                    <div>
                      <strong>{rule.label}</strong>
                      <small>{rule.modeLabel} · scope: {rule.scope}</small>
                      {rule.description ? <p>{rule.description}</p> : null}
                    </div>
                    <button
                      type="button"
                      className="settings-secondary-action"
                      disabled={!onClearRule || busyRuleId === rule.capability}
                      onClick={() => clearRule(rule)}
                    >
                      {busyRuleId === rule.capability ? "恢复中" : "恢复默认"}
                    </button>
                  </article>
                ))}
              </div>
            ) : (
              <div className="settings-empty-state settings-rule-empty">
                <div>
                  <span aria-hidden="true">⌕</span>
                  <strong>没有匹配的权限规则</strong>
                  <p>调整搜索词或模式筛选后再试。</p>
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="settings-empty-state settings-rule-empty">
            <div>
              <span aria-hidden="true">✓</span>
              <strong>暂无覆盖规则</strong>
              <p>审批时点击“始终允许”后，会在这里显示并可恢复默认。</p>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
