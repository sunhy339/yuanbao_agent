import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { matchCommands, type SlashCommand } from "../../state/slashCommands";

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

const COMMAND_LABEL = "New instruction";
const COMMAND_PLACEHOLDER = "Describe the next step for the local agent...";
const SUBMIT_LABEL = "Send";
const SENDING_LABEL = "Sending...";

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
  const [selectedIndex, setSelectedIndex] = useState(0);

  const autoResize = useCallback((target: HTMLTextAreaElement) => {
    target.style.height = "auto";
    target.style.height = `${Math.min(target.scrollHeight, 200)}px`;
  }, []);

  const matches = useMemo(() => {
    if (!promptValue.startsWith("/") || promptValue.includes(" ")) return [];
    return matchCommands(promptValue.trim());
  }, [promptValue]);

  const showPopup = matches.length > 0;

  // reset selection when matches change
  useEffect(() => {
    setSelectedIndex(0);
  }, [matches.length]);

  const applyCommand = useCallback(
    (cmd: SlashCommand) => {
      onPromptChange(cmd.name + " ");
      textareaRef.current?.focus();
    },
    [onPromptChange],
  );

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
            // ── autocomplete navigation ──
            if (showPopup) {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setSelectedIndex((i) => (i + 1) % matches.length);
                return;
              }
              if (event.key === "ArrowUp") {
                event.preventDefault();
                setSelectedIndex((i) => (i - 1 + matches.length) % matches.length);
                return;
              }
              if (event.key === "Tab" || event.key === "Enter") {
                event.preventDefault();
                applyCommand(matches[selectedIndex]);
                return;
              }
              if (event.key === "Escape") {
                event.preventDefault();
                onPromptChange("");
                return;
              }
            }
            // ── normal submit ──
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !disabled && promptValue.trim()) {
              event.preventDefault();
              onSubmitPrompt();
            }
          }}
          placeholder={COMMAND_PLACEHOLDER}
          disabled={disabled}
          rows={1}
        />
        {showPopup && (
          <div className="slash-popup" role="listbox">
            {matches.map((cmd, idx) => (
              <button
                key={cmd.name}
                type="button"
                role="option"
                aria-selected={idx === selectedIndex}
                className={idx === selectedIndex ? "slash-popup-item slash-popup-active" : "slash-popup-item"}
                onClick={() => applyCommand(cmd)}
                onMouseEnter={() => setSelectedIndex(idx)}
              >
                <span className="slash-popup-name">{cmd.name}</span>
                <span className="slash-popup-desc">{cmd.description}</span>
              </button>
            ))}
          </div>
        )}
      </label>
      <button type="submit" className="composer-run" disabled={disabled || !promptValue.trim()} data-sending={sending ? "true" : undefined}>
        {sending ? SENDING_LABEL : SUBMIT_LABEL}
      </button>
    </form>
  );
}
