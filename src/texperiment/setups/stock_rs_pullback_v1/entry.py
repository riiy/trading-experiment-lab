from __future__ import annotations

from texperiment.backtest.execution_model import can_buy_at_open


def next_open_entry_price(next_bar: dict) -> float | None:
    allowed, _ = can_buy_at_open(next_bar)
    if not allowed:
        return None
    price = next_bar.get("raw_open")
    return float(price) if price is not None else None
