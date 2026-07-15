from __future__ import annotations

from texperiment.backtest.cost import apply_round_trip_cost


def compute_trade_return(entry_price: float, exit_price: float, *, round_trip_cost: float = 0.002) -> dict:
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    gross = exit_price / entry_price - 1
    net = apply_round_trip_cost(gross, round_trip_cost)
    return {"gross_return": gross, "net_return": net}
