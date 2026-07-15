import pandas as pd

from texperiment.setups.stock_rs_pullback_v1.rules import passes_pullback_filter, passes_strength_filter
from texperiment.setups.stock_rs_pullback_v1.signal import generate_candidate_signals


def valid_row():
    return {
        "date": "2026-01-10",
        "code": "000001.SZ",
        "open": 10,
        "high": 11,
        "low": 9.8,
        "close": 10.8,
        "ma20": 10,
        "ma60": 9,
        "excess_ret20": 0.06,
        "made_20d_high_recent": True,
        "drawdown_from_10d_high": 0.05,
        "volume": 90,
        "vol_ma5": 100,
        "breakout_body_midpoint": 10.2,
    }


def test_strength_and_pullback_rules_pass():
    row = valid_row()
    assert passes_strength_filter(row)
    assert passes_pullback_filter(row)


def test_generate_candidate_signal():
    df = pd.DataFrame([valid_row()])
    signals = generate_candidate_signals(df)
    assert len(signals) == 1
    assert signals[0].code == "000001.SZ"
