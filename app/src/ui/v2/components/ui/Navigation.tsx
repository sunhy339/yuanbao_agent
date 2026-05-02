import type { ReactNode } from "react";
import "./ui.css";

export interface SegmentedItem<T extends string = string> {
  label: string;
  value: T;
  icon?: ReactNode;
  disabled?: boolean;
}

export interface SegmentedControlProps<T extends string = string> {
  value: T;
  items: SegmentedItem<T>[];
  onChange: (value: T) => void;
}

export function SegmentedControl<T extends string = string>({ value, items, onChange }: SegmentedControlProps<T>) {
  return (
    <div className="yb-segmented" role="tablist">
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          role="tab"
          aria-selected={item.value === value}
          disabled={item.disabled}
          onClick={() => onChange(item.value)}
        >
          {item.icon ? <span>{item.icon}</span> : null}
          {item.label}
        </button>
      ))}
    </div>
  );
}

export interface TabItem<T extends string = string> {
  label: string;
  value: T;
  badge?: string | number;
}

export interface TabsProps<T extends string = string> {
  value: T;
  items: TabItem<T>[];
  onChange: (value: T) => void;
}

export function Tabs<T extends string = string>({ value, items, onChange }: TabsProps<T>) {
  return (
    <div className="yb-tabs" role="tablist">
      {items.map((item) => (
        <button key={item.value} type="button" role="tab" aria-selected={item.value === value} onClick={() => onChange(item.value)}>
          {item.label}
          {item.badge !== undefined ? <span>{item.badge}</span> : null}
        </button>
      ))}
    </div>
  );
}
