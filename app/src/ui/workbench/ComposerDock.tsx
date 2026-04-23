interface ComposerDockProps {
  promptValue: string;
  onPromptChange: (value: string) => void;
  onSubmitPrompt: () => void;
  disabled: boolean;
  providerLabel: string;
  cwdLabel: string;
}

const COMMAND_LABEL = "\u65b0\u6307\u4ee4";
const COMMAND_PLACEHOLDER = "\u5199\u4e0b\u8981\u4ea4\u7ed9\u672c\u5730\u4ee3\u7406\u7684\u4e0b\u4e00\u6b65...";
const SUBMIT_LABEL = "\u9001\u51fa";

export function ComposerDock({
  promptValue,
  onPromptChange,
  onSubmitPrompt,
  disabled,
  providerLabel,
  cwdLabel,
}: ComposerDockProps) {
  return (
    <form
      className="composer-dock"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmitPrompt();
      }}
    >
      <div className="composer-meta" aria-label="Composer context">
        <span>{providerLabel}</span>
        <span>{cwdLabel}</span>
      </div>
      <label className="composer-input">
        <span>{COMMAND_LABEL}</span>
        <textarea
          aria-label="Task prompt"
          value={promptValue}
          onChange={(event) => onPromptChange(event.target.value)}
          placeholder={COMMAND_PLACEHOLDER}
          disabled={disabled}
        />
      </label>
      <button type="submit" className="composer-run" disabled={disabled || !promptValue.trim()}>
        {SUBMIT_LABEL}
      </button>
    </form>
  );
}
