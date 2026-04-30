import { useEffect, useState } from "react";

export interface ToastEntry {
  id: string;
  kind: "success" | "error" | "info";
  message: string;
}

let toastCounter = 0;

export function createToast(kind: ToastEntry["kind"], message: string): ToastEntry {
  return { id: `toast_${++toastCounter}`, kind, message };
}

export function ToastContainer({
  toasts,
  onDismiss,
}: {
  toasts: ToastEntry[];
  onDismiss: (id: string) => void;
}) {
  return (
    <div className="toast-container" aria-live="polite">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: ToastEntry;
  onDismiss: (id: string) => void;
}) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    requestAnimationFrame(() => setVisible(true));
    const timer = setTimeout(() => {
      setVisible(false);
      setTimeout(() => onDismiss(toast.id), 200);
    }, 3000);
    return () => clearTimeout(timer);
  }, [toast.id, onDismiss]);

  return (
    <div
      className={`toast-item toast-${toast.kind} ${visible ? "toast-visible" : ""}`}
      role="status"
    >
      <span>{toast.message}</span>
      <button
        type="button"
        className="toast-dismiss"
        aria-label="Dismiss"
        onClick={() => {
          setVisible(false);
          setTimeout(() => onDismiss(toast.id), 200);
        }}
      >
        &times;
      </button>
    </div>
  );
}
