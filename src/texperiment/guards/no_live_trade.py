from __future__ import annotations

from texperiment.exceptions import PermissionDenied


def block_live_trade() -> None:
    raise PermissionDenied("Live trading is forbidden in this research project")
