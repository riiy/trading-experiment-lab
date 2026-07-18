from __future__ import annotations

from texperiment.exceptions import PermissionDenied

TERMINAL_STATUSES = {
    "ARCHIVED_NON_TRADABLE",
    "FAILED_ARCHIVED",
    "EDGE_NOT_TRADABLE",
    "STOP_FEASIBILITY_FAILED",
}


def is_archived(status: str) -> bool:
    return status in TERMINAL_STATUSES


def assert_setup_not_archived(status: str, *, action: str) -> None:
    if is_archived(status):
        raise PermissionDenied(f"{action} blocked for terminal setup status: {status}")
