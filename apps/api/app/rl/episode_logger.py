from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


_EPISODE_LOGS: list[dict[str, object]] = []


def append_episode_log(event: str, payload: dict[str, Any] | None = None) -> dict[str, object]:
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event,
        "payload": payload or {},
    }
    _EPISODE_LOGS.append(entry)
    return entry


def get_episode_logs(limit: int = 20) -> list[dict[str, object]]:
    return _EPISODE_LOGS[-limit:]
