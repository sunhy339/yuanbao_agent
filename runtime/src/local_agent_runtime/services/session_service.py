from __future__ import annotations

from typing import Any


class SessionService:
    """Thin wrapper to keep session-specific logic out of the RPC layer."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def create_session(self, workspace_id: str, title: str) -> dict[str, Any]:
        return self._store.create_session(workspace_id=workspace_id, title=title)
