import type { ReactNode } from "react";
import type { TestableProps, UiTone } from "../../types";
import "./ui.css";

export interface PanelProps extends TestableProps {
  title?: string;
  subtitle?: string;
  description?: string;
  eyebrow?: string;
  action?: ReactNode;
  tone?: UiTone;
  interactive?: boolean;
  selected?: boolean;
  className?: string;
  children: ReactNode;
}

export function Panel({
  title,
  subtitle,
  description,
  eyebrow,
  action,
  tone = "neutral",
  interactive = false,
  selected = false,
  className,
  children,
  testId,
}: PanelProps) {
  const resolvedSubtitle = subtitle ?? description;

  return (
    <section
      className={["yb-panel", className].filter(Boolean).join(" ")}
      data-tone={tone}
      data-interactive={interactive}
      data-selected={selected}
      data-testid={testId}
    >
      {title || resolvedSubtitle || eyebrow || action ? (
        <header className="yb-panel-header">
          <div>
            {eyebrow ? <p className="yb-kicker">{eyebrow}</p> : null}
            {title ? <h2>{title}</h2> : null}
            {resolvedSubtitle ? <p>{resolvedSubtitle}</p> : null}
          </div>
          {action ? <div className="yb-panel-action">{action}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}
