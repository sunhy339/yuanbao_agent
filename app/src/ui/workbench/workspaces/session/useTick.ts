import { useEffect, useState } from "react";

// ── Shared 1-second tick hub ────────────────────────────────────────
// All "elapsed time" components subscribe to a single global interval
// instead of each creating their own, reducing timer overhead.

type TickListener = () => void;
const _tickListeners = new Set<TickListener>();
let _tickTimer: number | undefined;
let _tickRefCount = 0;

function _startGlobalTick() {
  if (_tickTimer !== undefined) return;
  _tickTimer = window.setInterval(() => {
    for (const fn of _tickListeners) fn();
  }, 1000);
}

function _stopGlobalTick() {
  if (_tickTimer !== undefined) {
    window.clearInterval(_tickTimer);
    _tickTimer = undefined;
  }
}

function _subscribeTick(fn: TickListener): () => void {
  _tickListeners.add(fn);
  _tickRefCount++;
  _startGlobalTick();
  return () => {
    _tickListeners.delete(fn);
    _tickRefCount--;
    if (_tickRefCount <= 0) {
      _tickRefCount = 0;
      _stopGlobalTick();
    }
  };
}

/**
 * Returns `Date.now()` and re-renders the component every second while
 * `active` is true.  Shares a single global timer across all callers.
 */
export function useTickWhen(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNow(Date.now());
    const unsub = _subscribeTick(() => setNow(Date.now()));
    return unsub;
  }, [active]);
  return now;
}
