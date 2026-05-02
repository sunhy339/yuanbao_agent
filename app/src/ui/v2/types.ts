import type { ReactNode } from "react";

export type UiTone = "neutral" | "primary" | "success" | "warning" | "danger" | "info";
export type UiSize = "xs" | "sm" | "md" | "lg";
export type UiDensity = "comfortable" | "compact";
export type AsyncStatus = "idle" | "loading" | "success" | "warning" | "error";

export interface DisableableProps {
  disabled?: boolean;
  disabledReason?: string;
}

export interface TestableProps {
  testId?: string;
}

export interface IconSlotProps {
  icon?: ReactNode;
  trailingIcon?: ReactNode;
}
