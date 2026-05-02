import type { CSSProperties, ReactNode } from "react";
import "./tokens.css";

export type ThemeMode = "dark" | "light" | "system";
export type DensityMode = "comfortable" | "compact";
export type RadiusMode = "sm" | "md" | "lg";
export type MotionMode = "reduced" | "subtle" | "expressive";
export type AccentColor = "cyan" | "violet" | "green" | "amber" | "rose";

interface ThemeProviderProps {
  children: ReactNode;
  theme?: ThemeMode;
  density?: DensityMode;
  radius?: RadiusMode;
  motion?: MotionMode;
  accentColor?: AccentColor;
  transparency?: number;
  fontScale?: number;
}

function resolveTheme(theme: ThemeMode): "dark" | "light" {
  if (theme !== "system") {
    return theme;
  }
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: light)").matches) {
    return "light";
  }
  return "dark";
}

function clamp(value: number | undefined, min: number, max: number, fallback: number) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, value));
}

export function ThemeProvider({
  children,
  theme = "dark",
  density = "comfortable",
  radius = "md",
  motion = "subtle",
  accentColor = "cyan",
  transparency,
  fontScale,
}: ThemeProviderProps) {
  const themeVars = {
    "--yb-transparency": String(clamp(transparency, 0.58, 0.96, 0.78)),
    "--yb-font-scale": String(clamp(fontScale, 0.92, 1.12, 1)),
  } as CSSProperties;

  return (
    <div
      className="yb-v2"
      data-theme={resolveTheme(theme)}
      data-density={density}
      data-radius={radius}
      data-motion={motion}
      data-accent={accentColor}
      style={themeVars}
    >
      {children}
    </div>
  );
}
