from texperiment.backtest.engine import compute_trade_return
from texperiment.backtest.execution_model import can_buy_at_open


def test_compute_trade_return_net_cost():
    result = compute_trade_return(100, 110, round_trip_cost=0.002)
    assert round(result["gross_return"], 4) == 0.1
    assert round(result["net_return"], 4) == 0.098


def test_cannot_buy_limit_up():
    ok, reason = can_buy_at_open({"open": 10, "is_limit_up": True})
    assert ok is False
    assert reason == "invalid_limit_up_cannot_buy"
