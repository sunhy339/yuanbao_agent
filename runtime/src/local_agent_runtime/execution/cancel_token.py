"""CancelToken — cooperative cancellation with parent-child propagation.

Mirrors haha-cc's AbortController pattern (src/utils/abortController.ts)
adapted for Python's synchronous/threaded runtime:

- Parent cancel propagates to all children (one-way)
- Child cancel does NOT affect parent
- Reason区分: user_interrupt / sibling_failure / hook_prevent / stream_fallback
- Supports asyncio.wait-style cooperative cancellation via wait_cancelled()
"""
from __future__ import annotations

import asyncio
import threading
import weakref
from typing import Any


_CANCEL_REASONS = frozenset({
    "user_interrupt",
    "sibling_failure",
    "hook_prevent",
    "stream_fallback",
    "timeout",
    "shutdown",
})


class CancelToken:
    """Cooperative cancellation token with parent-child propagation.

    Usage:
        root = CancelToken()               # created by TaskLifecycle
        child = root.create_child()         # per tool batch
        grandchild = child.create_child()   # per tool

        root.cancel("user_interrupt")       # propagates to child + grandchild

        if child.cancelled:
            ...                             # fast check

        await child.wait_cancelled()        # async wait
    """

    def __init__(
        self,
        parent: CancelToken | None = None,
        reason_filter: set[str] | None = None,
    ) -> None:
        self._event = threading.Event()
        self._reason: str | None = None
        self._reason_filter = reason_filter
        self._children: list[weakref.ref[CancelToken]] = []
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_waiters: list[asyncio.Future[str]] = []

        if parent is not None:
            parent._add_child(self)

    # ── Public API ──────────────────────────────────────────────

    def cancel(self, reason: str = "user_interrupt") -> None:
        """Cancel this token and propagate to all children.

        Args:
            reason: One of _CANCEL_REASONS. Used to distinguish interrupt
                    source (user ESC, sibling failure, hook prevent, etc.).
        """
        if self._event.is_set():
            return  # already cancelled, idempotent

        self._reason = reason
        self._event.set()

        # Propagate to children
        with self._lock:
            alive_children: list[weakref.ref[CancelToken]] = []
            for child_ref in self._children:
                child = child_ref()
                if child is not None:
                    # If child has a reason filter, only propagate matching reasons
                    if child._reason_filter is None or reason in child._reason_filter:
                        child.cancel(reason)
                    alive_children.append(child_ref)
            self._children = alive_children

        # Wake async waiters
        self._wake_async_waiters()

    @property
    def cancelled(self) -> bool:
        """Fast non-blocking check: has this token been cancelled?"""
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        """The cancellation reason, or None if not cancelled."""
        return self._reason

    def create_child(
        self, reason_filter: set[str] | None = None
    ) -> CancelToken:
        """Create a child token that cancels when this parent cancels.

        Args:
            reason_filter: If set, this child only cancels when the parent's
                           cancel reason is in this set. Useful for sibling
                           abort controllers that only react to "sibling_failure".
        """
        return CancelToken(parent=self, reason_filter=reason_filter)

    def check(self) -> None:
        """Raise RuntimeError if cancelled. Convenience for guard checks."""
        if self._event.is_set():
            raise RuntimeError(
                f"Operation cancelled: {self._reason or 'unknown'}"
            )

    async def wait_cancelled(self, timeout: float | None = None) -> str:
        """Async wait until cancelled. Returns the cancellation reason.

        Args:
            timeout: Maximum seconds to wait. None = wait forever.

        Returns:
            The cancellation reason string.

        Raises:
            TimeoutError: If timeout elapsed without cancellation.
        """
        if self._event.is_set():
            return self._reason or "unknown"

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        with self._lock:
            self._loop = loop
            self._async_waiters.append(future)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError("wait_cancelled timed out")

    # ── Internal ────────────────────────────────────────────────

    def _add_child(self, child: CancelToken) -> None:
        """Register a child token. Uses weakref to avoid preventing GC."""
        # Fast path: parent already cancelled
        if self._event.is_set():
            if child._reason_filter is None or self._reason in child._reason_filter:
                child.cancel(self._reason or "unknown")
            return

        with self._lock:
            self._children.append(weakref.ref(child))

    def _wake_async_waiters(self) -> None:
        """Resolve all pending async futures."""
        with self._lock:
            loop = self._loop
            waiters = self._async_waiters
            self._async_waiters = []

        if loop is None or loop.is_closed():
            return

        reason = self._reason or "unknown"
        for future in waiters:
            if not future.done():
                try:
                    loop.call_soon_threadsafe(future.set_result, reason)
                except RuntimeError:
                    pass  # loop closed
