from __future__ import annotations

TERMINAL_STATUSES = {"FAILED_ARCHIVED", "EDGE_NOT_TRADABLE", "STOP_FEASIBILITY_FAILED"}


def is_archived(status: str) -> bool:
    return status in TERMINAL_STATUSES
