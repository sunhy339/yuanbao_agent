import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import type { DisableableProps, TestableProps } from "../../types";
import "./ui.css";

interface FieldChromeProps extends DisableableProps, TestableProps {
  label?: string;
  helperText?: string;
  errorText?: string;
  children: ReactNode;
}

function FieldChrome({ label, helperText, errorText, children, disabledReason }: FieldChromeProps) {
  return (
    <label className="yb-field">
      {label ? <span>{label}</span> : null}
      {children}
      {errorText ? <small data-tone="danger">{errorText}</small> : helperText || disabledReason ? <small>{disabledReason ?? helperText}</small> : null}
    </label>
  );
}

export interface TextFieldProps
  extends DisableableProps,
    TestableProps,
    Omit<InputHTMLAttributes<HTMLInputElement>, "onChange" | "value" | "disabled" | "prefix"> {
  label?: string;
  value: string;
  helperText?: string;
  errorText?: string;
  prefix?: ReactNode;
  suffix?: ReactNode;
  onChange: (value: string) => void;
}

export function TextField({ label, value, helperText, errorText, prefix, suffix, onChange, disabled, disabledReason, testId, ...props }: TextFieldProps) {
  return (
    <FieldChrome label={label} helperText={helperText} errorText={errorText} disabledReason={disabledReason}>
      <span className="yb-input-shell">
        {prefix ? <span className="yb-input-affix">{prefix}</span> : null}
        <input {...props} value={value} disabled={disabled} data-testid={testId} onChange={(event) => onChange(event.currentTarget.value)} />
        {suffix ? <span className="yb-input-affix">{suffix}</span> : null}
      </span>
    </FieldChrome>
  );
}

export interface TextAreaFieldProps
  extends DisableableProps,
    TestableProps,
    Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "onChange" | "value" | "disabled"> {
  label?: string;
  value: string;
  helperText?: string;
  errorText?: string;
  onChange: (value: string) => void;
}

export function TextAreaField({ label, value, helperText, errorText, onChange, disabled, disabledReason, testId, ...props }: TextAreaFieldProps) {
  return (
    <FieldChrome label={label} helperText={helperText} errorText={errorText} disabledReason={disabledReason}>
      <textarea {...props} value={value} disabled={disabled} data-testid={testId} onChange={(event) => onChange(event.currentTarget.value)} />
    </FieldChrome>
  );
}

export interface SelectOption<T extends string = string> {
  label: string;
  value: T;
  description?: string;
  disabled?: boolean;
}

export interface SelectFieldProps<T extends string = string>
  extends DisableableProps,
    TestableProps,
    Omit<SelectHTMLAttributes<HTMLSelectElement>, "onChange" | "value" | "disabled"> {
  label?: string;
  value: T;
  options: SelectOption<T>[];
  helperText?: string;
  errorText?: string;
  onChange: (value: T) => void;
}

export function SelectField<T extends string = string>({ label, value, options, helperText, errorText, onChange, disabled, disabledReason, testId, ...props }: SelectFieldProps<T>) {
  return (
    <FieldChrome label={label} helperText={helperText} errorText={errorText} disabledReason={disabledReason}>
      <select {...props} value={value} disabled={disabled} data-testid={testId} onChange={(event) => onChange(event.currentTarget.value as T)}>
        {options.map((option) => (
          <option key={option.value} value={option.value} disabled={option.disabled}>
            {option.label}
          </option>
        ))}
      </select>
    </FieldChrome>
  );
}

export interface SwitchFieldProps extends DisableableProps, TestableProps {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

export function SwitchField({ label, description, checked, onChange, disabled, disabledReason, testId }: SwitchFieldProps) {
  return (
    <label className="yb-switch" title={disabledReason}>
      <input type="checkbox" checked={checked} disabled={disabled} data-testid={testId} onChange={(event) => onChange(event.currentTarget.checked)} />
      <span aria-hidden="true" />
      <strong>{label}</strong>
      {description ? <small>{description}</small> : null}
    </label>
  );
}
