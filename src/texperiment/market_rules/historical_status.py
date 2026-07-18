from __future__ import annotations

from enum import StrEnum
from typing import Any


class HistoricalSTStatus(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


def normalize_historical_st_status(value: Any) -> HistoricalSTStatus:
    if isinstance(value, bool):
        return HistoricalSTStatus.TRUE if value else HistoricalSTStatus.FALSE
    normalized = str(value).strip().upper()
    if normalized in {"TRUE", "1", "ST", "*ST"}:
        return HistoricalSTStatus.TRUE
    if normalized in {"FALSE", "0", "NORMAL", "NON_ST"}:
        return HistoricalSTStatus.FALSE
    return HistoricalSTStatus.UNKNOWN
