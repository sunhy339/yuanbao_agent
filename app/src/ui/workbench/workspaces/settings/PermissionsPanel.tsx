import { permissionModes } from "./settingsTypes";

export function PermissionsPanel({ selectedMode, onSelectMode }: { selectedMode: string; onSelectMode: (mode: string) => void }) {
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
            <input type="radio" name="permission-mode" checked={selectedMode === mode.id} onChange={() => onSelectMode(mode.id)} />
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
