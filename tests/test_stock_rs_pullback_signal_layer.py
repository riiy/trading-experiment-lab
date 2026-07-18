from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from texperiment.cli import main
from texperiment.setups.stock_rs_pullback_v1.signal import (
    build_stock_rs_pullback_signals,
    generate_triggered_signals,
    validate_universe_coverage,
)


def _row(day: int, *, close: float, high: float, low: float, drawdown: float, volume: int = 80):
    return {
        "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=day),
        "code": "000001.SZ",
        "name": "测试股",
        "open": close - 0.2,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "ma20": 100,
        "ma60": 90,
        "excess_ret20": 0.08,
        "drawdown_from_10d_high": drawdown,
        "vol_ma5": 100,
        "volume_ratio_to_ma5": volume / 100,
        "made_20d_high_recent": True,
        "has_complete_indicator_window": True,
    }


def test_generate_triggered_signal_after_pullback_high_reclaim():
    df = pd.DataFrame([
        _row(0, close=106, high=110, low=105, drawdown=0.00, volume=120),
        _row(1, close=104, high=108, low=103, drawdown=0.05, volume=80),  # pullback
        _row(2, close=107, high=108, low=105, drawdown=0.02, volume=90),
        _row(3, close=109, high=110, low=106, drawdown=0.01, volume=95),  # reclaim 108
    ])

    rows = generate_triggered_signals(df)

    triggered = [r for r in rows if r["status"] == "triggered_entry_next_open"]
    assert len(triggered) == 1
    signal = triggered[0]
    assert signal["code"] == "000001.SZ"
    assert signal["pullback_date"] == "2026-01-02"
    assert signal["trigger_date"] == "2026-01-04"
    assert signal["pullback_high"] == 108
    assert signal["stop_price"] == 103
    assert signal["days_to_trigger"] == 2


def test_build_signals_joins_universe_and_rejects_non_executable_trigger():
    df = pd.DataFrame([
        _row(0, close=106, high=110, low=105, drawdown=0.00, volume=120),
        _row(1, close=104, high=108, low=103, drawdown=0.05, volume=80),
        _row(2, close=109, high=110, low=106, drawdown=0.01, volume=95),
    ])
    universe = pd.DataFrame([
        {"date": pd.Timestamp("2026-01-01"), "code": "000001.SZ", "is_tradable_universe": True},
        {"date": pd.Timestamp("2026-01-02"), "code": "000001.SZ", "is_tradable_universe": True},
        {"date": pd.Timestamp("2026-01-03"), "code": "000001.SZ", "is_tradable_universe": False},
    ])

    out = build_stock_rs_pullback_signals(df, universe=universe)

    assert out.empty


def test_include_candidates_reports_pending_and_expired_reclaim():
    rows = [_row(0, close=106, high=110, low=105, drawdown=0.00, volume=120)]
    rows.append(_row(1, close=104, high=108, low=103, drawdown=0.05, volume=80))
    rows.extend(_row(day, close=106, high=108, low=104, drawdown=0.01) for day in range(2, 8))

    out = build_stock_rs_pullback_signals(pd.DataFrame(rows), include_candidates=True)

    assert set(out["status"]) == {
        "candidate_pending_reclaim",
        "candidate_expired_no_reclaim",
    }


def test_candidate_expires_when_strength_is_lost():
    first = _row(0, close=106, high=110, low=105, drawdown=0.00, volume=120)
    pullback = _row(1, close=104, high=108, low=103, drawdown=0.05, volume=80)
    strength_lost = _row(2, close=103, high=105, low=101, drawdown=0.06, volume=80)
    strength_lost["excess_ret20"] = 0.01

    out = build_stock_rs_pullback_signals(
        pd.DataFrame([first, pullback, strength_lost]),
        include_candidates=True,
    )

    assert set(out["status"]) == {
        "candidate_pending_reclaim",
        "candidate_expired_strength_lost",
    }


def test_require_universe_rejects_partial_date_coverage():
    indicators = pd.DataFrame([_row(0, close=106, high=110, low=105, drawdown=0.00)])
    universe = pd.DataFrame([{
        "date": pd.Timestamp("2026-01-02"),
        "code": "000001.SZ",
        "is_tradable_universe": True,
    }])

    with pytest.raises(ValueError, match="does not cover"):
        validate_universe_coverage(indicators, universe)


def test_signal_cli_cannot_overwrite_original_after_authorization(tmp_path):
    indicators = pd.DataFrame([
        _row(0, close=106, high=110, low=105, drawdown=0.00, volume=120),
        _row(1, close=104, high=108, low=103, drawdown=0.05, volume=80),
        _row(2, close=109, high=110, low=106, drawdown=0.01, volume=95),
    ])
    universe = pd.DataFrame([
        {"date": row["date"], "code": row["code"], "is_tradable_universe": True}
        for row in indicators.to_dict("records")
    ])
    indicator_path = tmp_path / "indicators.parquet"
    universe_path = tmp_path / "universe.parquet"
    output_path = tmp_path / "signals.csv"
    indicators.to_parquet(indicator_path, index=False)
    universe.to_parquet(universe_path, index=False)

    with pytest.raises(SystemExit, match="must use STOCK_RS_PULLBACK_v1_RECALCULATED"):
        main([
            "--root", str(Path(__file__).resolve().parents[1]),
            "generate-stock-rs-pullback-signals",
            "--indicator-input", str(indicator_path),
            "--universe-input", str(universe_path),
            "--output", str(output_path),
            "--require-universe",
        ])

    assert not output_path.exists()
