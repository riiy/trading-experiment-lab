from __future__ import annotations

import math

import pandas as pd
import pytest

from texperiment.indicators.a_share import AShareIndicatorConfig, build_a_share_indicators


def _stock_rows(code: str, start_close: float, days: int = 65):
    rows = []
    dates = pd.date_range("2026-01-01", periods=days, freq="D")
    for i, d in enumerate(dates):
        close = start_close + i
        rows.append({
            "date": d,
            "code": code,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000 + i * 1000,
            "amount": 100_000_000,
        })
    return rows


def _benchmark_rows(code: str = "000300.SH", start_close: float = 1000, days: int = 65):
    rows = []
    dates = pd.date_range("2026-01-01", periods=days, freq="D")
    for i, d in enumerate(dates):
        close = start_close + i * 2
        rows.append({
            "date": d,
            "code": code,
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": 10_000_000,
        })
    return rows


def test_build_a_share_indicators_core_columns_and_values():
    daily = pd.DataFrame(_stock_rows("000001.SZ", 10))
    benchmark = pd.DataFrame(_benchmark_rows())

    out = build_a_share_indicators(daily, benchmark_bars=benchmark)
    last = out.loc[out["code"] == "000001.SZ"].iloc[-1]

    closes = daily["close"]
    highs = daily["high"]
    volumes = daily["volume"]
    bench_closes = benchmark["close"]

    assert last["ma20"] == pytest.approx(closes.tail(20).mean())
    assert last["ma60"] == pytest.approx(closes.tail(60).mean())
    assert last["ret20"] == pytest.approx(closes.iloc[-1] / closes.iloc[-21] - 1)
    assert last["benchmark_ret20"] == pytest.approx(bench_closes.iloc[-1] / bench_closes.iloc[-21] - 1)
    assert last["excess_ret20"] == pytest.approx(last["ret20"] - last["benchmark_ret20"])
    assert last["relative_strength_20d"] == pytest.approx(last["excess_ret20"])
    assert last["high_10d"] == pytest.approx(highs.tail(10).max())
    assert last["drawdown_from_10d_high"] == pytest.approx(1 - last["close"] / last["high_10d"])
    assert last["vol_ma5"] == pytest.approx(volumes.tail(5).mean())
    assert bool(last["has_complete_indicator_window"]) is True


def test_indicator_windows_are_nan_until_enough_history():
    daily = pd.DataFrame(_stock_rows("000001.SZ", 10, days=30))
    benchmark = pd.DataFrame(_benchmark_rows(days=30))
    out = build_a_share_indicators(daily, benchmark_bars=benchmark)

    early = out.iloc[10]
    assert math.isnan(early["ma20"])
    assert math.isnan(early["ma60"])
    assert bool(early["has_complete_indicator_window"]) is False


def test_missing_benchmark_code_raises_clear_error():
    daily = pd.DataFrame(_stock_rows("000001.SZ", 10))
    benchmark = pd.DataFrame(_benchmark_rows(code="399300.SZ"))

    with pytest.raises(ValueError, match="benchmark code not found"):
        build_a_share_indicators(daily, benchmark_bars=benchmark, config=AShareIndicatorConfig(benchmark_code="000300.SH"))


def test_benchmark_can_be_in_same_input_table():
    daily = pd.DataFrame(_stock_rows("000001.SZ", 10) + _benchmark_rows())
    out = build_a_share_indicators(daily)

    assert "benchmark_ret20" in out.columns
    assert out.loc[out["code"] == "000001.SZ", "benchmark_ret20"].notna().sum() > 0
