/**
 * Shared dependency interface for custom hooks.
 * Each hook receives these callbacks from the parent App component.
 */
export interface HookDeps {
  addToast: (kind: "success" | "error" | "info", message: string) => void;
  toastError: (reason: unknown) => void;
  setError: (error: string | null) => void;
}

export function getErrorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
