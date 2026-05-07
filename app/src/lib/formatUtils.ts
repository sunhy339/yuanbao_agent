/**
 * Shared formatting utilities used across App.tsx and SessionWorkspace.tsx.
 */

const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/**
 * Format a millisecond duration as a human-readable string.
 * Returns `null` when the duration is undefined.
 */
export function formatDuration(durationMs?: number): string | null {
  if (durationMs === undefined) {
    return null;
  }

  if (durationMs < 1000) {
    return `${durationMs}ms`;
  }

  return `${(durationMs / 1000).toFixed(1)}s`;
}

/**
 * Format a millisecond timestamp as a localized date-time string.
 * Returns `null` when the timestamp is undefined.
 */
export function formatTimestamp(timestamp?: number): string | null {
  if (timestamp === undefined) {
    return null;
  }

  return dateTimeFormatter.format(new Date(timestamp));
}

/**
 * Format elapsed milliseconds as "Xh Xm", "Xm Xs", or "Xs".
 */
export function formatElapsedTime(durationMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(durationMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }
  return `${seconds}s`;
}

/**
 * Check whether a task status represents an actively controllable task.
 */
export function isTaskControllable(status?: string): boolean {
  return Boolean(
    status &&
      [
        "running",
        "planning",
        "verifying",
        "waiting_approval",
        "queued",
      ].includes(status),
  );
}
