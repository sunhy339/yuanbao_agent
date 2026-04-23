import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";

type Tone = "neutral" | "success" | "warning" | "danger";
type ButtonVariant = "default" | "primary" | "ghost";
type LedgerDensity = "default" | "compact";

function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}

export interface WorkbenchPageHeaderProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
}

export function WorkbenchPageHeader({ eyebrow, title, description, className, ...props }: WorkbenchPageHeaderProps) {
  return (
    <header className={cx("wb-page-header", className)} {...props}>
      {eyebrow ? <p className="wb-page-header__eyebrow">{eyebrow}</p> : null}
      <h1 className="wb-page-header__title">{title}</h1>
      {description ? <p className="wb-page-header__description">{description}</p> : null}
    </header>
  );
}

export interface WorkbenchLedgerCardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  heading?: ReactNode;
  density?: LedgerDensity;
}

export function WorkbenchLedgerCard({
  heading,
  density = "default",
  className,
  children,
  ...props
}: WorkbenchLedgerCardProps) {
  return (
    <section className={cx("wb-ledger-card", className)} data-density={density} {...props}>
      {heading ? <h2 className="wb-ledger-card__title">{heading}</h2> : null}
      <div className="wb-ledger-card__body">{children}</div>
    </section>
  );
}

export interface WorkbenchStatusPillProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

export function WorkbenchStatusPill({ tone = "neutral", className, ...props }: WorkbenchStatusPillProps) {
  return <span className={cx("wb-status-pill", className)} data-tone={tone} {...props} />;
}

export interface WorkbenchDeskButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

export function WorkbenchDeskButton({ variant = "default", className, type = "button", ...props }: WorkbenchDeskButtonProps) {
  return <button className={cx("wb-desk-button", className)} data-variant={variant} type={type} {...props} />;
}

export function WorkbenchSegmentedControl({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx("wb-segmented-control", className)} {...props} />;
}

export interface WorkbenchFormRowProps extends HTMLAttributes<HTMLLabelElement> {
  label: ReactNode;
  hint?: ReactNode;
}

export function WorkbenchFormRow({ label, hint, className, children, ...props }: WorkbenchFormRowProps) {
  return (
    <label className={cx("wb-form-row", className)} {...props}>
      <span>{label}</span>
      {children}
      {hint ? <span className="wb-form-row__hint">{hint}</span> : null}
    </label>
  );
}

export interface WorkbenchEmptyStateProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  heading?: ReactNode;
}

export function WorkbenchEmptyState({ heading, className, children, ...props }: WorkbenchEmptyStateProps) {
  return (
    <div className={cx("wb-empty-state", className)} {...props}>
      <div>
        {heading ? <strong>{heading}</strong> : null}
        {children}
      </div>
    </div>
  );
}

export function WorkbenchTerminalPanel({ className, ...props }: HTMLAttributes<HTMLPreElement>) {
  return <pre className={cx("wb-terminal-panel", className)} {...props} />;
}
