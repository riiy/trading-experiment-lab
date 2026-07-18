from texperiment.backtest.engine import compute_trade_return
from texperiment.backtest.execution_model import can_buy_at_open


def test_compute_trade_return_net_cost():
    result = compute_trade_return(100, 110, round_trip_cost=0.002)
    assert round(result["gross_return"], 4) == 0.1
    assert round(result["net_return"], 4) == 0.098


def test_cannot_buy_one_price_limit_up():
    ok, reason = can_buy_at_open({
        "raw_open": 10,
        "raw_high": 10,
        "raw_low": 10,
        "raw_close": 10,
        "adj_open": 10,
        "adj_high": 10,
        "adj_low": 10,
        "adj_close": 10,
        "adj_factor": 1,
        "is_suspended": False,
        "can_buy_at_open": "FALSE",
        "one_price_limit_up": "TRUE",
    })
    assert ok is False
    assert reason == "invalid_limit_up_cannot_buy"


def test_close_limit_flag_does_not_control_open_fillability():
    ok, reason = can_buy_at_open({
        "raw_open": 10,
        "raw_high": 11,
        "raw_low": 9.8,
        "raw_close": 11,
        "adj_open": 10,
        "adj_high": 11,
        "adj_low": 9.8,
        "adj_close": 11,
        "adj_factor": 1,
        "is_suspended": False,
        "can_buy_at_open": "TRUE",
        "is_limit_up": True,
        "close_at_limit_up": "TRUE",
        "one_price_limit_up": "FALSE",
    })
    assert ok is True
    assert reason is None


def test_unknown_open_fillability_fails_closed():
    ok, reason = can_buy_at_open({
        "raw_open": 10,
        "raw_high": 10.5,
        "raw_low": 9.5,
        "raw_close": 10,
        "adj_open": 10,
        "adj_high": 10.5,
        "adj_low": 9.5,
        "adj_close": 10,
        "adj_factor": 1,
        "is_suspended": False,
        "can_buy_at_open": "UNKNOWN",
    })
    assert ok is False
    assert reason == "invalid_open_fillability_unknown"


def test_inconsistent_price_layers_fail_closed():
    ok, reason = can_buy_at_open({
        "raw_open": 10,
        "raw_high": 11,
        "raw_low": 9,
        "raw_close": 10,
        "adj_open": 5,
        "adj_high": 5.5,
        "adj_low": 4.5,
        "adj_close": 5,
        "adj_factor": 1,
        "is_suspended": False,
        "can_buy_at_open": "TRUE",
    })
    assert ok is False
    assert reason == "invalid_inconsistent_price_layers"
