from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Trade:
    signal_id: str
    setup_id: str
    code: str
    name: str | None
    signal_date: str
    pullback_date: str
    trigger_date: str
    entry_date: str | None
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    exit_date: str | None
    exit_price: float | None
    exit_reason: str | None
    gross_return: float | None
    net_return: float | None
    r_multiple: float | None
    holding_days: int | None
    status: str = "valid_trade"
    invalid_reason: str | None = None


TRADE_OUTPUT_COLUMNS = [
    "trade_id",
    "signal_id",
    "setup_id",
    "code",
    "name",
    "signal_date",
    "pullback_date",
    "trigger_date",
    "entry_date",
    "entry_price",
    "stop_price",
    "target_price",
    "exit_date",
    "exit_price",
    "exit_reason",
    "gross_return",
    "net_return",
    "r_multiple",
    "holding_days",
    "round_trip_cost",
    "status",
    "invalid_reason",
]


VALID_TRADE_STATUS = "valid_trade"


INVALID_REASONS = {
    "invalid_signal_status",
    "invalid_no_next_open",
    "invalid_limit_up_cannot_buy",
    "invalid_suspended_cannot_buy",
    "invalid_entry_price",
    "invalid_stop_not_below_entry",
    "invalid_missing_price_data",
    "invalid_no_exit_data",
}
