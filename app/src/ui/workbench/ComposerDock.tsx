interface ComposerDockProps {
  promptValue: string;
  onPromptChange: (value: string) => void;
  onSubmitPrompt: () => void;
  disabled: boolean;
  providerLabel: string;
  cwdLabel: string;
}

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
        <span>新指令</span>
        <textarea
          aria-label="Task prompt"
          value={promptValue}
          onChange={(event) => onPromptChange(event.target.value)}
          placeholder="写下要交给本地代理的下一步..."
          disabled={disabled}
        />
      </label>
      <button type="submit" className="composer-run" disabled={disabled || !promptValue.trim()}>
        送出
      </button>
    </form>
  );
}
