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
  { id: "dark", label: "Dark", note: "Primary desktop workbench theme" },
  { id: "light", label: "Light", note: "Review and daytime operator mode" },
  { id: "system", label: "System", note: "Follow OS preference" },
];

const languageOptions: Array<{ id: SettingsGeneralConfig["language"]; label: string }> = [
  { id: "en", label: "English" },
  { id: "zh", label: "Chinese" },
  { id: "auto", label: "Auto" },
];

const densityOptions: Array<{ id: SettingsGeneralConfig["density"]; label: string }> = [
  { id: "comfortable", label: "Comfort" },
  { id: "compact", label: "Compact" },
];

const radiusOptions: Array<{ id: SettingsGeneralConfig["radius"]; label: string }> = [
  { id: "sm", label: "Small" },
  { id: "md", label: "Medium" },
  { id: "lg", label: "Large" },
];

const motionOptions: Array<{ id: SettingsGeneralConfig["motion"]; label: string }> = [
  { id: "reduced", label: "Reduced" },
  { id: "subtle", label: "Subtle" },
  { id: "expressive", label: "Expressive" },
];

const accentOptions: Array<{ id: SettingsGeneralConfig["accentColor"]; label: string }> = [
  { id: "cyan", label: "Cyan" },
  { id: "violet", label: "Violet" },
  { id: "green", label: "Green" },
  { id: "amber", label: "Amber" },
  { id: "rose", label: "Rose" },
];

const reasoningOptions: Array<{ id: SettingsGeneralConfig["reasoningEffort"]; label: string }> = [
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium" },
  { id: "high", label: "High" },
  { id: "max", label: "Max" },
];

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
          <p className="yb-kicker">Desk Control</p>
          <h1 id="appearance-title">Appearance</h1>
          <p>Tune the visible workbench theme, language bias, and runtime preflight posture.</p>
        </div>
        <dl>
          <div>
            <dt>Theme</dt>
            <dd>{value.theme}</dd>
          </div>
          <div>
            <dt>Density</dt>
            <dd>{value.density}</dd>
          </div>
          <div>
            <dt>Accent</dt>
            <dd>{value.accentColor}</dd>
          </div>
          <div>
            <dt>Language</dt>
            <dd>{value.language}</dd>
          </div>
          <div>
            <dt>Reasoning</dt>
            <dd>{value.reasoningEffort}</dd>
          </div>
        </dl>
      </section>

      <section className="appearance-grid">
        <section className="appearance-control-panel" aria-label="Appearance controls">
          <header>
            <div>
              <p className="yb-kicker">Theme</p>
              <h2>Workbench skin</h2>
            </div>
            <Button variant="ghost" onClick={onOpenSettings} disabled={!onOpenSettings} disabledReason="Settings are not available">
              Open settings
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
              <p className="yb-kicker">Density</p>
              <h3>Information pace</h3>
            </div>
            <div className="appearance-segmented" role="group" aria-label="Density">
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
              <p className="yb-kicker">Radius</p>
              <h3>Panel geometry</h3>
            </div>
            <div className="appearance-segmented" role="group" aria-label="Radius">
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
              <p className="yb-kicker">Motion</p>
              <h3>Interaction feedback</h3>
            </div>
            <div className="appearance-segmented" role="group" aria-label="Motion">
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
              <p className="yb-kicker">Accent</p>
              <h3>Status colorway</h3>
            </div>
            <div className="appearance-swatch-row" role="group" aria-label="Accent">
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
              <span>Transparency {Math.round(value.transparency * 100)}%</span>
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
              <span>Font scale {Math.round(value.fontScale * 100)}%</span>
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
              <p className="yb-kicker">Language</p>
              <h3>Interface copy</h3>
            </div>
            <div className="appearance-segmented" role="group" aria-label="Language">
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
              <p className="yb-kicker">Reasoning</p>
              <h3>Default effort</h3>
            </div>
            <div className="appearance-segmented" role="group" aria-label="Reasoning effort">
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
            <strong>Web preflight before external context</strong>
            <small>Keep this enabled when tasks rely on current documentation or fast-changing APIs.</small>
          </label>
        </section>

        <aside className="appearance-preview-panel" aria-label="Theme preview">
          <p className="yb-kicker">Preview</p>
          <h2>{workspaceName}</h2>
          <div className="appearance-preview-window" data-preview-theme={value.theme}>
            <header>
              <strong>Yuanbao Workbench</strong>
              <StatusBadge label={providerLabel} tone="success" compact />
            </header>
            <div className="appearance-preview-body">
              <div>
                <span>Runtime</span>
                <strong>Ready</strong>
              </div>
              <div>
                <span>Queue</span>
                <strong>Clear</strong>
              </div>
              <div>
                <span>Trace</span>
                <strong>Standby</strong>
              </div>
            </div>
            <footer>
              <span>Command lane</span>
              <Button size="sm" variant="primary" disabled disabledReason="Preview only">Send</Button>
            </footer>
          </div>
          <p className="appearance-preview-note">
            The live shell follows this setting immediately; deeper runtime persistence is handled by the Settings save path.
          </p>
        </aside>
      </section>
    </main>
  );
}
