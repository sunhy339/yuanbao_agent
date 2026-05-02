import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { DisableableProps, TestableProps, UiSize, UiTone } from "../../types";
import "./ui.css";

export interface IconButtonProps
  extends DisableableProps,
    TestableProps,
    Omit<ButtonHTMLAttributes<HTMLButtonElement>, "disabled" | "children"> {
  label: string;
  icon: ReactNode;
  size?: UiSize;
  tone?: UiTone;
  variant?: "ghost" | "soft" | "solid";
  active?: boolean;
  loading?: boolean;
}

export function IconButton({
  label,
  icon,
  size = "md",
  tone = "neutral",
  variant = "ghost",
  active = false,
  loading = false,
  disabled,
  disabledReason,
  testId,
  title,
  ...buttonProps
}: IconButtonProps) {
  return (
    <button
      {...buttonProps}
      type={buttonProps.type ?? "button"}
      className={["yb-icon-button", buttonProps.className].filter(Boolean).join(" ")}
      aria-label={label}
      data-size={size}
      data-tone={tone}
      data-variant={variant}
      data-active={active}
      disabled={disabled || loading}
      title={title ?? disabledReason ?? label}
      data-testid={testId}
    >
      {loading ? <span className="yb-spinner" aria-hidden="true" /> : icon}
    </button>
  );
}
