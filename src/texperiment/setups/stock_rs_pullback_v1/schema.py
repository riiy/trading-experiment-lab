from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    signal_id: str
    code: str
    signal_date: str
    pullback_high: float
    pullback_low: float
    stop_price: float
    status: str = "candidate"
