from pathlib import Path

import pandas as pd
import pytest

from texperiment.cli import main
from texperiment.account.account_simulator import (
    ACCEPTED_STATUS,
    AccountSimulationConfig,
    build_account_simulation_artifacts,
    run_account_simulation,
    summarize_account_simulation,
)


def _trade(
    trade_id: str,
    entry_date: str,
    exit_date: str,
    *,
    entry_price: float = 50.0,
    stop_price: float = 47.5,
    net_return: float = 0.05,
    status: str = "valid_trade",
):
    return {
        "trade_id": trade_id,
        "signal_id": f"sig-{trade_id}",
        "setup_id": "STOCK_RS_PULLBACK_v1",
        "code": "600000.SH",
        "name": "TEST",
        "signal_date": "2026-01-01",
        "pullback_date": "2026-01-01",
        "trigger_date": "2026-01-01",
        "entry_date": entry_date,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": entry_price + (entry_price - stop_price) * 2,
        "exit_date": exit_date,
        "exit_price": entry_price * (1 + net_return),
        "exit_reason": "max_holding_exit",
        "gross_return": net_return,
        "net_return": net_return,
        "r_multiple": 1.0,
        "holding_days": 5,
        "round_trip_cost": 0.002,
        "status": status,
        "invalid_reason": None if status == "valid_trade" else "invalid_no_exit_data",
    }


def test_account_simulation_accepts_sized_trade():
    trades = pd.DataFrame([_trade("t1", "2026-01-02", "2026-01-08")])
    sim = run_account_simulation(trades)

    row = sim.iloc[0]
    assert row["status"] == ACCEPTED_STATUS
    assert row["shares"] == 200
    assert row["capital_used"] == 10000
    assert row["planned_loss"] == 500
    assert row["pnl"] == 500
    assert row["account_equity"] == 30500


def test_account_simulation_rejects_one_lot_too_expensive():
    trades = pd.DataFrame([
        _trade("t1", "2026-01-02", "2026-01-08", entry_price=200, stop_price=190, net_return=0.02)
    ])
    sim = run_account_simulation(trades)

    row = sim.iloc[0]
    assert row["status"] == "rejected_or_skipped"
    assert row["invalid_reason"] == "invalid_one_lot_too_expensive"


def test_account_simulation_rejects_overlapping_position():
    trades = pd.DataFrame([
        _trade("t1", "2026-01-02", "2026-01-10"),
        _trade("t2", "2026-01-05", "2026-01-12"),
    ])
    sim = run_account_simulation(trades)

    assert sim.iloc[0]["status"] == ACCEPTED_STATUS
    assert sim.iloc[1]["status"] == "rejected_or_skipped"
    assert sim.iloc[1]["invalid_reason"] == "rejected_max_positions"


def test_account_simulation_monthly_loss_budget_blocks_after_limit():
    trades = pd.DataFrame([
        _trade("t1", "2026-01-02", "2026-01-03", net_return=-0.05),
        _trade("t2", "2026-01-04", "2026-01-05", net_return=-0.05),
        _trade("t3", "2026-01-06", "2026-01-07", net_return=-0.05),
        _trade("t4", "2026-01-08", "2026-01-09", net_return=-0.05),
    ])
    sim = run_account_simulation(trades)

    assert list(sim["status"][:3]) == [ACCEPTED_STATUS, ACCEPTED_STATUS, ACCEPTED_STATUS]
    assert sim.iloc[3]["status"] == "rejected_or_skipped"
    assert sim.iloc[3]["invalid_reason"] == "rejected_monthly_loss_limit_reached"


def test_account_simulation_total_drawdown_freezes_account():
    trades = pd.DataFrame([
        _trade("t1", "2026-01-02", "2026-01-03", net_return=-0.05),
        _trade("t2", "2026-01-04", "2026-01-05", net_return=-0.05),
        _trade("t3", "2026-01-06", "2026-01-07", net_return=-0.05),
    ])
    account_config = {
        "account": {"capital_limit": 30000},
        "risk": {
            "max_planned_loss_per_trade": 500,
            "max_monthly_loss": 5000,
            "max_total_drawdown": 1000,
            "max_positions": 1,
        },
    }
    sim = run_account_simulation(trades, account_config=account_config)

    assert sim.iloc[0]["status"] == ACCEPTED_STATUS
    assert sim.iloc[1]["status"] == ACCEPTED_STATUS
    assert sim.iloc[2]["status"] == "rejected_or_skipped"
    assert sim.iloc[2]["invalid_reason"] == "skipped_after_total_drawdown_freeze"


def test_account_simulation_summary_and_artifacts():
    trades = pd.DataFrame([_trade("t1", "2026-01-02", "2026-01-08")])
    artifacts = build_account_simulation_artifacts(trades)
    summary = summarize_account_simulation(artifacts["simulation"])

    assert artifacts["summary"]["decision"] == "ACCOUNT_SIMULATION_PASSED"
    assert summary["accepted_trades"] == 1
    assert "账户仿真报告" in artifacts["report_markdown"]


def test_account_simulation_rejects_unsupported_position_limit():
    trades = pd.DataFrame([_trade("t1", "2026-01-02", "2026-01-08")])
    with pytest.raises(ValueError, match="max_positions=1"):
        run_account_simulation(
            trades,
            account_config={
                "account": {"capital_limit": 30000},
                "risk": {"max_positions": 2},
            },
        )


def test_actual_monthly_loss_freezes_following_trades():
    trades = pd.DataFrame([
        _trade("t1", "2026-01-02", "2026-01-03", net_return=-0.20),
        _trade("t2", "2026-01-04", "2026-01-05", net_return=-0.20),
    ])
    sim = run_account_simulation(trades)

    assert sim.iloc[0]["status"] == ACCEPTED_STATUS
    assert sim.iloc[1]["invalid_reason"] == "rejected_monthly_loss_limit_reached"
    assert summarize_account_simulation(sim)["monthly_limit_breached"] is True


def test_account_simulation_cli_requires_validation_pass(tmp_path):
    trade_path = tmp_path / "trades.csv"
    trades = pd.DataFrame([_trade("t1", "2026-01-02", "2026-01-08")])
    trades.to_csv(trade_path, index=False)
    with pytest.raises(SystemExit, match="account_simulation_allowed=false"):
        main([
            "--root", str(Path(__file__).resolve().parents[1]),
            "account-sim-stock-rs-pullback",
            "--setup", "STOCK_RS_PULLBACK_v1",
            "--trade-input", str(trade_path),
            "--metrics-input", str(tmp_path / "failed.json"),
        ])


def test_force_research_cannot_bypass_archived_setup(tmp_path):
    trade_path = tmp_path / "trades.csv"
    pd.DataFrame([_trade("t1", "2026-01-02", "2026-01-08")]).to_csv(trade_path, index=False)

    with pytest.raises(SystemExit, match="account_simulation_allowed=false"):
        main([
            "--root", str(Path(__file__).resolve().parents[1]),
            "account-sim-stock-rs-pullback",
            "--trade-input", str(trade_path),
            "--force-research",
        ])
