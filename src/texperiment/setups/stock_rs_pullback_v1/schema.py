from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    """A STOCK_RS_PULLBACK_v1 signal.

    ``candidate`` rows represent eligible pullback days. ``triggered`` rows
    represent completed signals after price reclaims the pullback-day high.
    Backtest execution is still next-day open and is handled downstream.
    """

    signal_id: str
    code: str
    signal_date: str
    pullback_high: float
    pullback_low: float
    stop_price: float
    status: str = "candidate"
    setup_id: str = "STOCK_RS_PULLBACK_v1"
    name: str | None = None
    pullback_date: str | None = None
    trigger_date: str | None = None
    trigger_close: float | None = None
    days_to_trigger: int | None = None
    entry_execution: str = "next_day_open"
    invalid_reason: str | None = None


SIGNAL_OUTPUT_COLUMNS = [
    "signal_id",
    "setup_id",
    "code",
    "name",
    "signal_date",
    "pullback_date",
    "trigger_date",
    "status",
    "entry_execution",
    "pullback_high",
    "pullback_low",
    "stop_price",
    "trigger_close",
    "days_to_trigger",
    "excess_ret20_at_pullback",
    "drawdown_from_10d_high_at_pullback",
    "volume_ratio_to_ma5_at_pullback",
    "is_tradable_universe_at_pullback",
    "is_tradable_universe_at_trigger",
    "invalid_reason",
]
