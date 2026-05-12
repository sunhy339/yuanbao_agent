from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class TaskStateMachine:
    """Validates task status transitions.

    All task status changes should route through ``assert_transition`` so that
    every transition is validated and traceable.
    """

    VALID_TRANSITIONS: dict[str, set[str]] = {
        "queued": {"running", "cancelled"},
        "running": {"completed", "failed", "cancelled", "paused", "waiting_approval"},
        "paused": {"running", "cancelled"},
        "waiting_approval": {"running", "cancelled", "failed", "paused"},
        "planning": {"running", "failed", "cancelled"},
        "verifying": {"running", "failed", "cancelled"},
        "completed": set(),
        "failed": set(),
        "cancelled": set(),
    }

    def assert_transition(
        self,
        current_status: str,
        target_status: str,
        task_id: str,
        *,
        silent: bool = False,
    ) -> None:
        """Validate a task status transition.

        Args:
            current_status: The task's current status.
            target_status: The desired target status.
            task_id: Task identifier (for error messages).
            silent: If True, log warning instead of raising. Used by internal
                methods that may be called from error handlers where the task
                is already terminal.

        Raises:
            ValueError: If the transition is invalid and ``silent`` is False.
        """
        allowed = self.VALID_TRANSITIONS.get(current_status, set())
        if target_status in allowed:
            return
        logger.warning(
            "Illegal task transition: %s -> %s for task %s (allowed: %s)",
            current_status,
            target_status,
            task_id,
            allowed or "none (terminal)",
        )
        if not silent:
            raise ValueError(
                f"Task {task_id} cannot transition from '{current_status}' to '{target_status}'. "
                f"Allowed: {sorted(allowed) or 'none (terminal state)'}"
            )
