import type { ReactNode } from "react";
import "./ui.css";

export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="yb-empty">
      <strong>{title}</strong>
      {description ? <small>{description}</small> : null}
      {action}
    </div>
  );
}

export function ErrorState({ title, message, onRetry }: { title: string; message: string; onRetry?: () => void }) {
  return (
    <div className="yb-error-state" role="alert">
      <strong>{title}</strong>
      <small>{message}</small>
      {onRetry ? <button type="button" onClick={onRetry}>Retry</button> : null}
    </div>
  );
}

export function Skeleton({ variant = "line" }: { variant?: "line" | "block" | "avatar" | "card" }) {
  return <span className="yb-skeleton" data-variant={variant} aria-hidden="true" />;
}
