from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from texperiment.cli import main
from texperiment.metrics.industry import attach_latest_industry, by_industry
from texperiment.metrics.validation import build_validation_artifacts, write_validation_outputs


def _trade(code: str, exit_date: str, net_return: float, *, status: str = "valid_trade", industry: str | None = None):
    row = {
        "trade_id": f"t-{code}-{exit_date}",
        "signal_id": f"s-{code}-{exit_date}",
        "setup_id": "STOCK_RS_PULLBACK_v1",
        "code": code,
        "name": code,
        "signal_date": "2026-01-01",
        "pullback_date": "2026-01-01",
        "trigger_date": "2026-01-02",
        "entry_date": "2026-01-03",
        "entry_price": 10,
        "stop_price": 9,
        "target_price": 12,
        "exit_date": exit_date,
        "exit_price": 10 * (1 + net_return),
        "exit_reason": "max_holding_exit",
        "gross_return": net_return + 0.002,
        "net_return": net_return,
        "r_multiple": net_return * 10,
        "holding_days": 5,
        "round_trip_cost": 0.002,
        "status": status,
        "invalid_reason": None if status == "valid_trade" else "invalid_no_next_open",
    }
    if industry is not None:
        row["industry"] = industry
    return row


def test_build_validation_artifacts_core_metrics_and_gates():
    trades = pd.DataFrame([
        _trade("000001.SZ", "2026-01-10", 0.10),
        _trade("000002.SZ", "2026-01-11", 0.05),
        _trade("000003.SZ", "2026-01-12", 0.02),
        _trade("000004.SZ", "2026-01-13", -0.03),
        _trade("000005.SZ", "2026-01-14", -0.01),
        _trade("000006.SZ", "2026-01-15", 0.00, status="invalid_trade"),
    ])
    setup = {
        "setup_id": "STOCK_RS_PULLBACK_v1",
        "validation_threshold": {
            "min_valid_trades": 5,
            "mean_net_return_gt": 0,
            "median_net_return_gte": 0,
            "profit_factor_gt": 1.20,
            "best_3_removed_mean_gte": -0.05,
            "top3_contribution_ratio_lte": 4.0,
            "min_positive_years_or_regimes": 1,
        },
    }

    artifacts = build_validation_artifacts(trades, setup_config=setup)
    metrics = artifacts["metrics"]
    overall = metrics["overall"]

    assert overall["rows"] == 6
    assert overall["valid_trades"] == 5
    assert overall["invalid_trades"] == 1
    assert overall["mean_net_return"] == pytest_approx(0.026)
    assert overall["median_net_return"] == pytest_approx(0.02)
    assert overall["profit_factor"] == pytest_approx(4.25)
    assert overall["best_3_removed_mean"] == pytest_approx(-0.02)
    assert overall["top3_contribution_ratio"] == pytest_approx(0.17 / 0.13)
    assert metrics["gates"]["min_valid_trades"]["passed"] is True
    assert metrics["decision"] == "VALIDATION_PASSED_NEEDS_ACCOUNT_SIMULATION"
    assert metrics["industry_analysis"]["status"] == "NOT_EVALUABLE"
    assert metrics["industry_analysis"]["impact_on_final_decision"] == "none"
    assert "行业集中度" in artifacts["report_markdown"]


def test_industry_metrics_attach_latest_metadata():
    trades = pd.DataFrame([
        _trade("000001.SZ", "2026-01-10", 0.03),
        _trade("000002.SZ", "2026-01-11", -0.01),
        _trade("000003.SZ", "2026-01-12", 0.02),
    ])
    metadata = pd.DataFrame([
        {"date": "2026-01-01", "code": "000001.SZ", "industry": "银行"},
        {"date": "2026-01-02", "code": "000001.SZ", "industry": "银行"},
        {"date": "2026-01-01", "code": "000002.SZ", "industry": "银行"},
        {"date": "2026-01-01", "code": "000003.SZ", "industry": "电子"},
    ])
    enriched = attach_latest_industry(trades, metadata)
    out = by_industry(enriched)

    bank = out.loc[out["industry"] == "银行"].iloc[0]
    assert bank["valid_trades"] == 2
    assert bank["trade_share"] == pytest_approx(2 / 3)


def test_industry_metadata_does_not_use_future_or_blank_labels():
    trades = pd.DataFrame([
        _trade("000001.SZ", "2026-01-10", 0.03),
        _trade("000002.SZ", "2026-01-11", -0.01),
    ])
    metadata = pd.DataFrame([
        {"date": "2026-01-02", "code": "000001.SZ", "industry": "  "},
        {"date": "2026-01-01", "code": "000002.SZ", "industry": "电子"},
        {"date": "2026-02-01", "code": "000001.SZ", "industry": "未来行业"},
    ])

    enriched = attach_latest_industry(trades, metadata)

    assert enriched.loc[0, "industry"] == "UNKNOWN"
    assert enriched.loc[1, "industry"] == "电子"


def test_write_validation_outputs(tmp_path):
    trades = pd.DataFrame([_trade("000001.SZ", "2026-01-10", 0.03)])
    artifacts = build_validation_artifacts(trades, setup_config={"setup_id": "STOCK_RS_PULLBACK_v1"})
    paths = write_validation_outputs(
        artifacts,
        metrics_path=tmp_path / "metrics.json",
        report_path=tmp_path / "report.md",
        yearly_path=tmp_path / "yearly.csv",
        industry_path=tmp_path / "industry.csv",
    )
    assert paths["metrics"].exists()
    assert paths["report"].exists()
    assert paths["yearly"].exists()
    assert paths["industry"].exists()
    loaded = json.loads(paths["metrics"].read_text(encoding="utf-8"))
    assert loaded["setup_id"] == "STOCK_RS_PULLBACK_v1"


def test_validation_report_cli_writes_all_outputs(tmp_path):
    trades = pd.DataFrame([_trade("000001.SZ", "2026-01-10", 0.03)])
    trade_path = tmp_path / "trades.csv"
    metadata_path = tmp_path / "metadata.parquet"
    trades.to_csv(trade_path, index=False)
    pd.DataFrame([{
        "date": "2026-01-01",
        "code": "000001.SZ",
        "industry": "银行",
    }]).to_parquet(metadata_path, index=False)
    metrics_path = tmp_path / "metrics.json"
    report_path = tmp_path / "report.md"
    yearly_path = tmp_path / "yearly.csv"
    industry_path = tmp_path / "industry.csv"

    rc = main([
        "--root", str(Path(__file__).resolve().parents[1]),
        "report-stock-rs-pullback",
        "--trade-input", str(trade_path),
        "--metadata-input", str(metadata_path),
        "--metrics-output", str(metrics_path),
        "--report-output", str(report_path),
        "--yearly-output", str(yearly_path),
        "--industry-output", str(industry_path),
    ])

    assert rc == 0
    assert all(path.exists() for path in [metrics_path, report_path, yearly_path, industry_path])


def pytest_approx(value):
    import pytest

    return pytest.approx(value)
