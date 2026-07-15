from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Trade:
    signal_id: str
    code: str
    entry_price: float
    stop_price: float
    target_price: float
    status: str = "valid_trade"
    invalid_reason: str | None = None
