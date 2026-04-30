import { useCallback, useRef } from "react";

interface ComposerDockProps {
  promptValue: string;
  onPromptChange: (value: string) => void;
  onSubmitPrompt: () => void;
  disabled: boolean;
  sending?: boolean;
  providerLabel: string;
  cwdLabel: string;
  hidden?: boolean;
}

const COMMAND_LABEL = "新指令";
const COMMAND_PLACEHOLDER = "写下要交给本地代理的下一步...";
const SUBMIT_LABEL = "送出";
const SENDING_LABEL = "发送中...";

export function ComposerDock({
  promptValue,
  onPromptChange,
  onSubmitPrompt,
  disabled,
  sending,
  providerLabel,
  cwdLabel,
  hidden,
}: ComposerDockProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const autoResize = useCallback((target: HTMLTextAreaElement) => {
    target.style.height = "auto";
    target.style.height = `${Math.min(target.scrollHeight, 200)}px`;
  }, []);

  return (
    <form
      className={hidden ? "composer-dock composer-dock-hidden" : "composer-dock"}
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
          ref={textareaRef}
          aria-label="Task prompt"
          value={promptValue}
          onChange={(event) => {
            onPromptChange(event.target.value);
            autoResize(event.target);
          }}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !disabled && promptValue.trim()) {
              event.preventDefault();
              onSubmitPrompt();
            }
          }}
          placeholder={COMMAND_PLACEHOLDER}
          disabled={disabled}
          rows={1}
        />
      </label>
      <button type="submit" className="composer-run" disabled={disabled || !promptValue.trim()} data-sending={sending ? "true" : undefined}>
        {sending ? SENDING_LABEL : SUBMIT_LABEL}
      </button>
    </form>
  );
}
