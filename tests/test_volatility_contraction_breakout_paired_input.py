import pandas as pd

from texperiment.setups.volatility_contraction_breakout_v1.paired_input import write_vcb_paired_input
from texperiment.setups.volatility_contraction_breakout_v1.development_input import write_development_backtest_bars


def test_vcb_paired_input_joins_layers_and_rebuilds_mapping(tmp_path):
    keys = {"date": pd.to_datetime(["2024-01-02", "2024-01-03"]), "code": ["600000.SH", "600000.SH"]}
    raw = pd.DataFrame({**keys, "open": [10.0, 10.2], "high": [10.4, 10.4], "low": [9.9, 10.0], "close": [10.2, 10.3], "pre_close": [9.8, 10.2], "volume": [1000, 1100], "amount": [10_000, 11_000]})
    qfq = raw.copy()
    for column in ("open", "high", "low", "close", "pre_close"):
        qfq[column] = qfq[column] * 0.5
    raw_path, qfq_path, output = tmp_path / "raw.parquet", tmp_path / "qfq.parquet", tmp_path / "paired.parquet"
    raw.to_parquet(raw_path, index=False); qfq.to_parquet(qfq_path, index=False)

    report = write_vcb_paired_input(raw_path, qfq_path, output, batch_size=1)

    paired = pd.read_parquet(output)
    assert report.rows == 2
    assert report.mapping_unknown_rows == 0
    assert paired["raw_open"].tolist() == [10.0, 10.2]
    assert paired["adj_open"].tolist() == [5.0, 5.1]
    assert paired["adjustment_status"].eq("KNOWN_AFFINE_RAW_QFQ_VALIDATED").all()


def test_development_bars_are_windowed_by_signalled_code(tmp_path):
    dates = pd.date_range("2024-01-01", periods=8, freq="D")
    daily = pd.DataFrame({
        "date": dates.tolist() * 2,
        "code": ["600000.SH"] * 8 + ["000001.SZ"] * 8,
        "name": ["x"] * 16,
        "board": ["MAIN_SH"] * 8 + ["MAIN_SZ"] * 8,
        "listing_date": [pd.Timestamp("2020-01-01")] * 16,
        "listing_trading_day": [1000] * 16,
        "raw_open": [10.0] * 16, "raw_high": [10.2] * 16, "raw_low": [9.8] * 16, "raw_close": [10.0] * 16,
        "raw_pre_close": [10.0] * 16,
        "adj_open": [10.0] * 16, "adj_high": [10.2] * 16, "adj_low": [9.8] * 16, "adj_close": [10.0] * 16,
        "adj_factor": [1.0] * 16, "adj_offset": [0.0] * 16,
        "volume": [1000] * 16, "amount": [10000] * 16, "is_suspended": [False] * 16,
    })
    signals = pd.DataFrame({"signal_id": ["s1"], "code": ["600000.SH"], "signal_date": [dates[3]], "status": ["triggered_entry_next_open"]})
    daily_path, signal_path, output = tmp_path / "daily.parquet", tmp_path / "signals.parquet", tmp_path / "bars.parquet"
    daily.to_parquet(daily_path, index=False); signals.to_parquet(signal_path, index=False)
    report = write_development_backtest_bars(daily_path, signal_path, output, batch_size=4, lookback_days=1, forward_days=2)
    bars = pd.read_parquet(output)
    assert report["codes"] == 1
    assert set(bars["code"]) == {"600000.SH"}
    assert bars["date"].min() == dates[2]
    assert bars["date"].max() == dates[5]
