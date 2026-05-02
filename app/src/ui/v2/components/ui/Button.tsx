import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { DisableableProps, TestableProps, UiSize, UiTone } from "../../types";
import "./ui.css";

export interface ButtonProps
  extends DisableableProps,
    TestableProps,
    Omit<ButtonHTMLAttributes<HTMLButtonElement>, "disabled"> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: UiSize;
  tone?: UiTone;
  icon?: ReactNode;
  trailingIcon?: ReactNode;
  loading?: boolean;
  pressed?: boolean;
  fullWidth?: boolean;
}

export function Button({
  variant = "secondary",
  size = "md",
  tone = "neutral",
  icon,
  trailingIcon,
  loading = false,
  pressed = false,
  fullWidth = false,
  disabled,
  disabledReason,
  testId,
  children,
  title,
  ...buttonProps
}: ButtonProps) {
  return (
    <button
      {...buttonProps}
      type={buttonProps.type ?? "button"}
      className={[
        "yb-button",
        buttonProps.className,
        fullWidth ? "is-full" : "",
      ].filter(Boolean).join(" ")}
      data-variant={variant}
      data-size={size}
      data-tone={tone}
      data-pressed={pressed}
      data-loading={loading}
      disabled={disabled || loading}
      title={title ?? disabledReason}
      data-testid={testId}
    >
      {loading ? <span className="yb-spinner" aria-hidden="true" /> : icon ? <span className="yb-button-icon">{icon}</span> : null}
      <span className="yb-button-label">{children}</span>
      {trailingIcon ? <span className="yb-button-icon">{trailingIcon}</span> : null}
    </button>
  );
}
