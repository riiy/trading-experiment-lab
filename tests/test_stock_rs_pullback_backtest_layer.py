from __future__ import annotations

import pandas as pd
import pytest

from texperiment.backtest.engine import (
    run_stock_rs_pullback_backtest,
    run_stock_rs_pullback_backtest_from_parquet,
    summarize_backtest_trades,
)


def _signal(**extra):
    base = {
        "signal_id": "s1",
        "setup_id": "STOCK_RS_PULLBACK_v1",
        "code": "000001.SZ",
        "name": "测试股",
        "signal_date": "2026-01-02",
        "pullback_date": "2026-01-01",
        "trigger_date": "2026-01-02",
        "status": "triggered_entry_next_open",
        "entry_execution": "next_day_open",
        "pullback_high": 10.5,
        "pullback_low": 9.5,
        "stop_price": 9.5,
    }
    base.update(extra)
    return base


def _bar(date: str, open_: float, high: float, low: float, close: float, **extra):
    base = {
        "date": date,
        "code": "000001.SZ",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "raw_open": open_,
        "raw_high": high,
        "raw_low": low,
        "raw_close": close,
        "adj_open": open_,
        "adj_high": high,
        "adj_low": low,
        "adj_close": close,
        "adj_factor": 1.0,
        "adj_offset": 0.0,
        "is_suspended": False,
        "can_buy_at_open": "TRUE",
        "can_sell_at_open": "TRUE",
        "can_sell_intraday": "TRUE",
        "can_sell_at_close": "TRUE",
        "one_price_limit_up": "FALSE",
        "one_price_limit_down": "FALSE",
        "limit_rule_status": "KNOWN_LIMIT",
        "is_limit_up": False,
        "is_limit_down": False,
    }
    base.update(extra)
    return base


def test_backtest_next_open_entry_and_target_2r_exit_with_cost():
    signals = pd.DataFrame([_signal()])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6),
        _bar("2026-01-03", 10.0, 10.6, 9.8, 10.3),  # entry, no same-day exit allowed
        _bar("2026-01-04", 10.4, 11.1, 10.2, 10.8),
    ])

    out = run_stock_rs_pullback_backtest(signals, daily)

    assert len(out) == 1
    trade = out.iloc[0]
    assert trade["status"] == "valid_trade"
    assert trade["entry_date"] == "2026-01-03"
    assert trade["entry_price"] == 10.0
    assert trade["stop_price"] == 9.5
    assert trade["target_price"] == 11.0
    assert trade["exit_date"] == "2026-01-04"
    assert trade["exit_reason"] == "target_2r"
    assert trade["exit_price"] == 11.0
    assert trade["r_multiple"] == pytest.approx(2.0)
    assert trade["gross_return"] == pytest.approx(0.10)
    assert trade["net_return"] == pytest.approx(0.098)


def test_backtest_stop_loss_uses_structure_stop():
    signals = pd.DataFrame([_signal(stop_price=9.5, pullback_low=9.5)])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6),
        _bar("2026-01-03", 10.0, 10.4, 9.7, 10.1),
        _bar("2026-01-04", 10.0, 10.1, 9.4, 9.6),
    ])

    out = run_stock_rs_pullback_backtest(signals, daily)
    trade = out.iloc[0]

    assert trade["status"] == "valid_trade"
    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_price"] == 9.5
    assert trade["r_multiple"] == pytest.approx(-1.0)


def test_backtest_uses_open_for_gap_through_stop():
    signals = pd.DataFrame([_signal(stop_price=9.5, pullback_low=9.5)])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6),
        _bar("2026-01-03", 10.0, 10.2, 9.8, 10.0),
        _bar("2026-01-04", 9.0, 9.4, 8.8, 9.1),
    ])

    trade = run_stock_rs_pullback_backtest(signals, daily).iloc[0]

    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_price"] == 9.0


def test_backtest_uses_open_for_gap_through_target():
    signals = pd.DataFrame([_signal()])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6),
        _bar("2026-01-03", 10.0, 10.6, 9.8, 10.3),
        _bar("2026-01-04", 11.5, 11.8, 11.4, 11.6),
    ])

    trade = run_stock_rs_pullback_backtest(signals, daily).iloc[0]

    assert trade["exit_reason"] == "target_2r"
    assert trade["exit_price"] == 11.5


def test_backtest_maps_structural_stop_across_factor_change():
    signals = pd.DataFrame([_signal(stop_price=9.0, pullback_low=9.0)])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6),
        _bar("2026-01-03", 10.0, 10.3, 9.7, 10.1),
        _bar(
            "2026-01-04",
            20.0,
            20.2,
            17.8,
            18.2,
            raw_open=20.0,
            raw_high=20.2,
            raw_low=17.8,
            raw_close=18.2,
            adj_open=10.0,
            adj_high=10.1,
            adj_low=8.9,
            adj_close=9.1,
            adj_factor=0.5,
        ),
    ])

    trade = run_stock_rs_pullback_backtest(signals, daily).iloc[0]

    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_price"] == 18.0
    assert trade["exit_adjusted_price"] == 9.0
    assert trade["gross_return"] == pytest.approx(-0.1)
    assert trade["r_multiple"] == pytest.approx(-1.0)


def test_backtest_maps_structural_levels_with_affine_offset():
    signals = pd.DataFrame([_signal(stop_price=5.5, pullback_low=5.5)])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.0, 11.0, 9.0, 10.5, adj_open=6.0, adj_high=6.5, adj_low=5.5, adj_close=6.25, adj_factor=0.5, adj_offset=1.0),
        _bar("2026-01-03", 10.0, 11.0, 9.5, 10.5, adj_open=6.0, adj_high=6.5, adj_low=5.75, adj_close=6.25, adj_factor=0.5, adj_offset=1.0),
        _bar("2026-01-04", 11.0, 12.0, 10.8, 12.0, adj_open=6.5, adj_high=7.0, adj_low=6.4, adj_close=7.0, adj_factor=0.5, adj_offset=1.0),
    ])

    trade = run_stock_rs_pullback_backtest(signals, daily).iloc[0]

    assert trade["entry_price"] == 10.0
    assert trade["stop_price"] == 9.0
    assert trade["target_price"] == 12.0
    assert trade["exit_price"] == 12.0
    assert trade["exit_adjusted_price"] == 7.0
    assert trade["r_multiple"] == pytest.approx(2.0)


def test_backtest_defers_stop_while_one_price_limit_down():
    signals = pd.DataFrame([_signal(stop_price=9.5, pullback_low=9.5)])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6),
        _bar("2026-01-03", 10.0, 10.2, 9.8, 10.0),
        _bar(
            "2026-01-04",
            9.0,
            9.0,
            9.0,
            9.0,
            can_sell_at_open="FALSE",
            can_sell_intraday="FALSE",
            can_sell_at_close="FALSE",
            one_price_limit_down="TRUE",
        ),
        _bar("2026-01-05", 8.8, 9.1, 8.7, 9.0),
    ])

    trade = run_stock_rs_pullback_backtest(signals, daily).iloc[0]

    assert trade["exit_date"] == "2026-01-05"
    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_price"] == 8.8


def test_backtest_defers_time_exit_from_suspended_d5_to_next_open():
    signals = pd.DataFrame([_signal(stop_price=9.0, pullback_low=9.0)])
    rows = [_bar("2026-01-02", 10.4, 10.7, 10.2, 10.6)]
    for offset in range(6):
        date = pd.Timestamp("2026-01-03") + pd.Timedelta(days=offset)
        rows.append(_bar(date.strftime("%Y-%m-%d"), 10.0, 10.5, 9.7, 10.1))
    rows[5]["is_suspended"] = True
    rows[5]["can_sell_at_open"] = "FALSE"

    trade = run_stock_rs_pullback_backtest(signals, pd.DataFrame(rows)).iloc[0]

    assert trade["exit_date"] == "2026-01-08"
    assert trade["exit_reason"] == "time_stop_no_upside_progress"
    assert trade["exit_price"] == 10.0
    assert trade["holding_days"] == 6


def test_scheduled_d5_close_at_limit_down_defers_to_next_open():
    signals = pd.DataFrame([_signal(stop_price=8.0, pullback_low=8.0)])
    rows = [_bar("2026-01-02", 10.4, 10.7, 10.2, 10.6)]
    for offset in range(6):
        date = pd.Timestamp("2026-01-03") + pd.Timedelta(days=offset)
        rows.append(_bar(date.strftime("%Y-%m-%d"), 10.0, 10.8, 9.0, 10.0))
    rows[5].update(
        raw_close=9.0,
        adj_close=9.0,
        close=9.0,
        close_at_limit_down="TRUE",
        can_sell_at_close="FALSE",
        scheduled_close_fill_status="ASSUMED_UNFILLED_CONSERVATIVE",
    )
    rows[6].update(raw_open=8.8, adj_open=8.8, open=8.8)

    trade = run_stock_rs_pullback_backtest(signals, pd.DataFrame(rows)).iloc[0]

    assert trade["exit_date"] == "2026-01-08"
    assert trade["exit_price"] == 8.8
    assert trade["exit_reason"] == "time_stop_no_upside_progress"
    assert trade["holding_days"] == 6


def test_scheduled_exit_continues_through_next_day_one_price_limit_down():
    signals = pd.DataFrame([_signal(stop_price=8.0, pullback_low=8.0)])
    rows = [_bar("2026-01-02", 10.4, 10.7, 10.2, 10.6)]
    for offset in range(7):
        date = pd.Timestamp("2026-01-03") + pd.Timedelta(days=offset)
        rows.append(_bar(date.strftime("%Y-%m-%d"), 10.0, 10.8, 9.0, 10.0))
    rows[5].update(can_sell_at_close="FALSE", close_at_limit_down="TRUE")
    rows[6].update(
        raw_open=9.0,
        raw_high=9.0,
        raw_low=9.0,
        raw_close=9.0,
        adj_open=9.0,
        adj_high=9.0,
        adj_low=9.0,
        adj_close=9.0,
        open=9.0,
        high=9.0,
        low=9.0,
        close=9.0,
        can_sell_at_open="FALSE",
        can_sell_intraday="FALSE",
        can_sell_at_close="FALSE",
        one_price_limit_down="TRUE",
    )
    rows[7].update(raw_open=8.7, adj_open=8.7, open=8.7)

    trade = run_stock_rs_pullback_backtest(signals, pd.DataFrame(rows)).iloc[0]

    assert trade["exit_date"] == "2026-01-09"
    assert trade["exit_price"] == 8.7
    assert trade["holding_days"] == 7


def test_backtest_ignores_candidate_audit_rows():
    signals = pd.DataFrame([_signal(status="candidate_pending_reclaim")])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6),
        _bar("2026-01-03", 10.0, 10.6, 9.8, 10.3),
    ])

    out = run_stock_rs_pullback_backtest(signals, daily)

    assert out.empty


def test_streaming_backtest_matches_dataframe_path(tmp_path):
    signals = pd.DataFrame([_signal()])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6),
        _bar("2026-01-03", 10.0, 10.6, 9.8, 10.3),
        _bar("2026-01-04", 10.4, 11.1, 10.2, 10.8),
    ])
    daily_path = tmp_path / "daily.parquet"
    daily.to_parquet(daily_path, index=False)

    expected = run_stock_rs_pullback_backtest(signals, daily)
    actual = run_stock_rs_pullback_backtest_from_parquet(signals, daily_path, batch_size=2)

    pd.testing.assert_frame_equal(expected, actual, check_dtype=False)


def test_streaming_backtest_preserves_missing_code_invalid_trade(tmp_path):
    signals = pd.DataFrame([_signal(code="000002.SZ")])
    daily = pd.DataFrame([_bar("2026-01-02", 10.4, 10.7, 10.2, 10.6)])
    daily_path = tmp_path / "daily.parquet"
    daily.to_parquet(daily_path, index=False)

    out = run_stock_rs_pullback_backtest_from_parquet(signals, daily_path, batch_size=1)

    assert out.loc[0, "status"] == "invalid_trade"
    assert out.loc[0, "invalid_reason"] == "invalid_no_next_open"


def test_streaming_backtest_is_batch_size_invariant_across_code_blocks(tmp_path):
    signals = pd.DataFrame([_signal(code="920000.BJ"), _signal(code="600000.SH", signal_id="s2")])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6, code="920000.BJ"),
        _bar("2026-01-03", 10.0, 10.6, 9.8, 10.3, code="920000.BJ"),
        _bar("2026-01-04", 10.4, 11.1, 10.2, 10.8, code="920000.BJ"),
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6, code="600000.SH"),
        _bar("2026-01-03", 10.0, 10.6, 9.8, 10.3, code="600000.SH"),
        _bar("2026-01-04", 10.4, 11.1, 10.2, 10.8, code="600000.SH"),
    ])
    daily_path = tmp_path / "daily.parquet"
    daily.to_parquet(daily_path, index=False)

    small = run_stock_rs_pullback_backtest_from_parquet(signals, daily_path, batch_size=3)
    large = run_stock_rs_pullback_backtest_from_parquet(signals, daily_path, batch_size=100)

    pd.testing.assert_frame_equal(small, large, check_dtype=False)


def test_backtest_time_stop_no_upside_progress_exits_d5_close():
    signals = pd.DataFrame([_signal(stop_price=9.0, pullback_low=9.0)])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6),
        _bar("2026-01-03", 10.0, 10.2, 9.7, 10.1),
        _bar("2026-01-04", 10.1, 10.4, 9.8, 10.2),
        _bar("2026-01-05", 10.2, 10.6, 9.9, 10.3),
        _bar("2026-01-06", 10.3, 10.7, 10.0, 10.4),
        _bar("2026-01-07", 10.4, 10.8, 10.1, 10.5),  # below +1R progress price=11
        _bar("2026-01-08", 10.5, 12.1, 10.2, 12.0),
    ])

    out = run_stock_rs_pullback_backtest(signals, daily)
    trade = out.iloc[0]

    assert trade["status"] == "valid_trade"
    assert trade["exit_date"] == "2026-01-07"
    assert trade["exit_reason"] == "time_stop_no_upside_progress"
    assert trade["holding_days"] == 5


def test_backtest_max_holding_exit_at_d10_close():
    signals = pd.DataFrame([_signal(stop_price=9.0, pullback_low=9.0)])
    rows = [_bar("2026-01-02", 10.4, 10.7, 10.2, 10.6)]
    for i in range(10):
        day = pd.Timestamp("2026-01-03") + pd.Timedelta(days=i)
        rows.append(_bar(day.strftime("%Y-%m-%d"), 10.0 + i * 0.05, 11.1, 9.6, 10.2 + i * 0.05))
    daily = pd.DataFrame(rows)

    out = run_stock_rs_pullback_backtest(signals, daily)
    trade = out.iloc[0]

    assert trade["status"] == "valid_trade"
    assert trade["exit_reason"] == "max_holding_exit"
    assert trade["holding_days"] == 10
    assert trade["exit_date"] == "2026-01-12"


def test_backtest_does_not_reject_normal_open_from_close_limit_flag():
    signals = pd.DataFrame([_signal()])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6),
        _bar("2026-01-03", 10.0, 11.0, 9.8, 11.0, is_limit_up=True, close_at_limit_up="TRUE"),
        _bar("2026-01-04", 10.4, 11.1, 10.2, 10.8),
    ])

    out = run_stock_rs_pullback_backtest(signals, daily)
    trade = out.iloc[0]

    assert trade["status"] == "valid_trade"
    assert trade["entry_price"] == 10.0


def test_backtest_rejects_explicit_one_price_limit_up():
    signals = pd.DataFrame([_signal()])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6),
        _bar(
            "2026-01-03",
            11.0,
            11.0,
            11.0,
            11.0,
            can_buy_at_open="FALSE",
            one_price_limit_up="TRUE",
            is_limit_up=True,
        ),
    ])

    trade = run_stock_rs_pullback_backtest(signals, daily).iloc[0]

    assert trade["status"] == "invalid_trade"
    assert trade["invalid_reason"] == "invalid_limit_up_cannot_buy"


def test_backtest_summary_counts_valid_and_invalid():
    signals = pd.DataFrame([_signal(), _signal(signal_id="s2", code="000002.SZ")])
    daily = pd.DataFrame([
        _bar("2026-01-02", 10.4, 10.7, 10.2, 10.6),
        _bar("2026-01-03", 10.0, 10.6, 9.8, 10.3),
        _bar("2026-01-04", 10.4, 11.1, 10.2, 10.8),
    ])

    trades = run_stock_rs_pullback_backtest(signals, daily)
    summary = summarize_backtest_trades(trades)

    assert summary["rows"] == 2
    assert summary["valid_trades"] == 1
    assert summary["invalid_trades"] == 1
    assert summary["invalid_reason_counts"]["invalid_no_next_open"] == 1
