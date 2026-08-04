from __future__ import annotations

import pandas as pd
import pytest

from texperiment.account.daily_equity import DailyEquityConfig, run_daily_account_equity, transaction_costs
from texperiment.setups.volatility_contraction_breakout_v1.backtest import run_volatility_contraction_breakout_backtest
from texperiment.setups.volatility_contraction_breakout_v1.rules import (
    build_volatility_contraction_breakout_signals,
    write_volatility_contraction_breakout_signals_from_parquet,
)
from texperiment.setups.volatility_contraction_breakout_v1.universe import write_volatility_contraction_breakout_universe_from_parquet
from texperiment.setups.volatility_contraction_breakout_v1.validation import benchmark_cagr, build_final_validation_artifacts


def _bar(date, code="600000.SH", *, open_=10, high=10.2, low=9.8, close=10, volume=100, buy=True, sell=True):
    return {"date": date, "code": code, "raw_open": open_, "raw_high": high, "raw_low": low, "raw_close": close,
            "adj_open": open_, "adj_high": high, "adj_low": low, "adj_close": close, "adj_factor": 1.0, "adj_offset": 0.0,
            "volume": volume, "is_suspended": False, "can_buy_at_open": buy, "can_sell_at_open": sell,
            "can_sell_intraday": sell, "can_sell_at_close": sell}


def test_costs_apply_minimum_commission_stamp_duty_and_slippage():
    cfg = DailyEquityConfig()
    buy = transaction_costs(side="buy", price=10, shares=100, cfg=cfg)
    sell = transaction_costs(side="sell", price=10, shares=100, cfg=cfg)
    assert buy["fill_price"] == pytest.approx(10.005)
    assert buy["commission"] == 5.0
    assert sell["fill_price"] == pytest.approx(9.995)
    assert sell["commission"] == 5.0
    assert sell["stamp_duty"] == pytest.approx(0.49975)


def test_daily_equity_marks_cash_position_and_costs_once():
    bars = pd.DataFrame([_bar("2022-07-18"), _bar("2022-07-19", close=10), _bar("2022-07-20", open_=11, high=11, low=11, close=11)])
    trades = pd.DataFrame([{"trade_id": "t1", "code": "600000.SH", "entry_date": "2022-07-19", "exit_date": "2022-07-20", "entry_price": 10, "stop_price": 9, "exit_price": 11, "status": "valid_trade"}])
    result = run_daily_account_equity(trades, bars, start_date="2022-07-18", end_date="2022-07-20")
    curve, ledger = result["equity_curve"], result["ledger"]
    assert curve.iloc[0]["equity"] == 30000
    assert curve.iloc[1]["position_market_value"] == 5000
    assert ledger.iloc[0]["buy_commission"] == 5
    assert ledger.iloc[0]["sell_commission"] == 5
    assert ledger.iloc[0]["stamp_duty"] == pytest.approx(2.748625)
    assert curve.iloc[-1]["equity"] == pytest.approx(30482.001375)


def test_signal_requires_contraction_breakout_and_volume_ratio():
    dates = pd.bdate_range("2021-01-01", periods=100)
    rows = []
    for i, date in enumerate(dates):
        high, low, close, volume = 10.0, 8.0, 9.0, 100.0
        if i >= 90:
            high, low, close, volume = 10.0, 9.8, 9.9, 100.0
        if i == 99:
            high, low, close, volume = 10.3, 9.8, 10.2, 200.0
        rows.append({"date": date, "code": "600000.SH", "high": high, "low": low, "close": close, "volume": volume, "adj_type": "qfq"})
    signals = build_volatility_contraction_breakout_signals(pd.DataFrame(rows))
    assert len(signals) == 1
    assert signals.iloc[0]["status"] == "triggered_entry_next_open"


def test_streamed_signals_match_in_memory_signals(tmp_path):
    dates = pd.bdate_range("2021-01-01", periods=100)
    rows = [{"date": date, "code": "600000.SH", "adj_high": 10.0 if i < 99 else 10.3, "adj_low": 8.0 if i < 90 else 9.8, "adj_close": 9.0 if i < 90 else (9.9 if i < 99 else 10.2), "volume": 100.0 if i < 99 else 200.0} for i, date in enumerate(dates)]
    daily, universe, output = tmp_path / "daily.parquet", tmp_path / "universe.parquet", tmp_path / "signals.parquet"
    pd.DataFrame(rows).to_parquet(daily, index=False)
    pd.DataFrame({"date": dates, "code": "600000.SH", "is_tradable_universe": True}).to_parquet(universe, index=False)
    assert write_volatility_contraction_breakout_signals_from_parquet(daily, universe, output, batch_size=25) == 1
    assert pd.read_parquet(output)["signal_date"].tolist() == [str(dates[-1].date())]


def test_backtest_uses_next_open_two_atr_stop_and_t_plus_one():
    dates = pd.bdate_range("2022-01-03", periods=4)
    bars = pd.DataFrame([
        _bar(dates[0]), _bar(dates[1], open_=10, high=10.1, low=9.9, close=10),
        _bar(dates[2], open_=9.5, high=9.6, low=8.9, close=9.1), _bar(dates[3]),
    ])
    signals = pd.DataFrame([{"signal_id": "s", "setup_id": "VOLATILITY_CONTRACTION_BREAKOUT_v1", "code": "600000.SH", "signal_date": str(dates[0].date()), "status": "triggered_entry_next_open", "atr10": 0.5}])
    trade = run_volatility_contraction_breakout_backtest(signals, bars).iloc[0]
    assert trade["entry_date"] == str(dates[1].date())
    assert trade["stop_price"] == pytest.approx(9.0)
    assert trade["exit_date"] == str(dates[2].date())
    assert trade["exit_reason"] == "stop_loss"


def test_benchmark_cagr_uses_exact_equity_dates_and_price_index():
    curve = pd.DataFrame([{"date": "2022-07-18", "equity": 30000}, {"date": "2023-07-18", "equity": 33000}])
    benchmark = pd.DataFrame([{"date": "2022-07-18", "code": "000300.SH", "raw_close": 100}, {"date": "2023-07-18", "code": "000300.SH", "raw_close": 110}])
    result = benchmark_cagr(benchmark, curve)
    assert result["return_basis"] == "price_index"
    assert result["benchmark_cagr"] == pytest.approx(0.10, rel=0.002)


def test_final_validation_requires_existing_and_account_gates_together():
    trades = pd.DataFrame([
        {"trade_id": f"t{i}", "code": f"60000{i}.SH", "entry_date": "2023-01-02", "exit_date": "2023-01-03", "net_return": ret, "holding_days": 2, "r_multiple": ret, "exit_reason": "stop_loss", "status": "valid_trade"}
        for i, ret in enumerate([0.10, 0.05, 0.02, -0.01, -0.01])
    ])
    curve = pd.DataFrame([{"date": "2022-07-18", "equity": 30000, "drawdown": 0, "drawdown_pct": 0, "account_frozen": False}, {"date": "2026-07-17", "equity": 42000, "drawdown": 0, "drawdown_pct": 0, "account_frozen": False}])
    benchmark = pd.DataFrame([{"date": "2022-07-18", "code": "000300.SH", "raw_close": 100}, {"date": "2026-07-17", "code": "000300.SH", "raw_close": 110}])
    setup = {"setup_id": "VOLATILITY_CONTRACTION_BREAKOUT_v1", "validation_threshold": {"min_valid_trades": 5, "mean_net_return_gt": 0, "median_net_return_gte": 0, "profit_factor_gt": 1.2, "best_3_removed_mean_gte": -0.02, "top3_contribution_ratio_lte": 2, "min_positive_years_or_regimes": 1}, "benchmark": {"code": "000300.SH"}}
    artifacts = build_final_validation_artifacts(trades, curve, benchmark, setup_config=setup)
    assert artifacts["metrics"]["gates"]["account_cagr"]["passed"] is True
    assert artifacts["metrics"]["gates"]["account_max_drawdown"]["passed"] is True
    assert artifacts["metrics"]["decision"] == "FINAL_VALIDATION_PASSED_RESEARCH_ONLY"


def test_compact_universe_and_execution_ignore_historical_st(tmp_path):
    dates = pd.bdate_range("2022-01-03", periods=20)
    source = pd.DataFrame([{"date": date, "code": "600000.SH", "close": 10, "raw_close": 10, "amount": 400_000_000,
                            "raw_open": 10, "raw_high": 10.2, "raw_low": 9.8, "raw_pre_close": 10,
                            "adj_factor": 1.0, "volume": 1_000_000, "is_suspended": False,
                            "trade_status": "1", "listing_date": dates[0], "listing_days": 200}
                           for date in dates])
    input_path, output_path = tmp_path / "daily.parquet", tmp_path / "universe.parquet"
    source.to_parquet(input_path, index=False)
    rows, eligible = write_volatility_contraction_breakout_universe_from_parquet(
        input_path, output_path, setup_config={"execution": {"historical_st_policy": "IGNORE_HISTORICAL_ST_ORDINARY_LIMITS_V1"}, "universe": {"min_avg_amount_20d": 300_000_000, "max_one_lot_value": 15_000, "lot_size": 100, "exclude_st": False, "exclude_suspended": True, "exclude_limit_up_down": True}}
    )
    assert rows == 20
    assert eligible == 1
    assert pd.read_parquet(output_path).iloc[-1]["is_tradable_universe"]


def test_backtest_rebuilds_fillability_without_historical_st():
    dates = pd.bdate_range("2022-01-03", periods=4)
    bars = pd.DataFrame([
        _bar(dates[0]),
        _bar(dates[1]),
        _bar(dates[2], low=8.9),
        _bar(dates[3]),
    ])
    signals = pd.DataFrame([{"signal_id": "s", "code": "600000.SH", "signal_date": str(dates[0].date()), "status": "triggered_entry_next_open", "atr10": 0.5}])
    trade = run_volatility_contraction_breakout_backtest(
        signals, bars, setup_config={"execution": {"historical_st_policy": "IGNORE_HISTORICAL_ST_ORDINARY_LIMITS_V1"}}
    ).iloc[0]
    assert trade["status"] == "valid_trade"
    assert trade["entry_date"] == str(dates[1].date())
