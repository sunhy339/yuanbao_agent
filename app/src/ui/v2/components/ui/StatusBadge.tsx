import type { ReactNode } from "react";
import type { AsyncStatus, TestableProps, UiTone } from "../../types";
import "./ui.css";

export interface StatusBadgeProps extends TestableProps {
  label: string;
  tone?: UiTone;
  status?: AsyncStatus;
  pulse?: boolean;
  icon?: ReactNode;
  compact?: boolean;
}

export function StatusBadge({
  label,
  tone = "neutral",
  status = "idle",
  pulse = false,
  icon,
  compact = false,
  testId,
}: StatusBadgeProps) {
  return (
    <span
      className="yb-status-badge"
      data-tone={tone}
      data-status={status}
      data-pulse={pulse}
      data-compact={compact}
      data-testid={testId}
    >
      {icon ? <span className="yb-status-icon">{icon}</span> : null}
      {label}
    </span>
  );
}
