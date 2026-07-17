from pathlib import Path

import pandas as pd
import pytest

from texperiment.audit.manifest import build_audit_manifest, verify_audit_manifest
from texperiment.audit.rebuilder import audit_trade, summarize_audit
from texperiment.audit.sampler import AUDIT_PLAN_VERSION, select_audit_sample


def _trade(i: int, *, status: str = "valid_trade", reason: str = "stop_loss", net_return: float = -0.01):
    return {
        "trade_id": f"t-{i:03d}",
        "signal_id": f"s-{i:03d}",
        "setup_id": "STOCK_RS_PULLBACK_v1",
        "code": f"{i % 10:06d}.SZ",
        "signal_date": "2026-01-02",
        "pullback_date": "2026-01-01",
        "trigger_date": "2026-01-02",
        "entry_date": "2026-01-03" if status == "valid_trade" else None,
        "entry_price": 10.0 if status == "valid_trade" else None,
        "stop_price": 9.0,
        "target_price": 12.0 if status == "valid_trade" else None,
        "exit_date": "2026-01-07" if status == "valid_trade" else None,
        "exit_price": 9.0 if status == "valid_trade" else None,
        "exit_reason": reason if status == "valid_trade" else None,
        "gross_return": -0.1 if status == "valid_trade" else None,
        "net_return": net_return if status == "valid_trade" else None,
        "r_multiple": -1.0 if status == "valid_trade" else None,
        "holding_days": 5 if reason == "time_stop_no_upside_progress" else 2,
        "status": status,
        "invalid_reason": None if status == "valid_trade" else "invalid_no_next_open",
    }


def _sample_fixture() -> pd.DataFrame:
    rows = []
    i = 0
    for reason, count in [("stop_loss", 20), ("target_2r", 15), ("time_stop_no_upside_progress", 15), ("max_holding_exit", 12)]:
        for _ in range(count):
            rows.append(_trade(i, reason=reason, net_return=(i - 30) / 1000))
            i += 1
    for _ in range(8):
        rows.append(_trade(i, status="invalid_trade"))
        i += 1
    return pd.DataFrame(rows)


def test_frozen_sampler_is_exact_mutually_exclusive_and_deterministic():
    trades = _sample_fixture()
    first = select_audit_sample(trades)
    second = select_audit_sample(trades)

    assert AUDIT_PLAN_VERSION == "AUDIT_STOCK_RS_PULLBACK_v1_PLAN_v1"
    assert len(first) == first["source_trade_row"].nunique() == 50
    assert first["trade_id"].tolist() == second["trade_id"].tolist()
    assert first["audit_category"].value_counts().to_dict() == {
        "stop_loss": 12,
        "target_2r": 10,
        "time_stop_no_upside_progress": 10,
        "max_holding_exit": 8,
        "invalid_trade": 5,
        "extreme_gain": 3,
        "extreme_loss": 2,
    }


def test_extremes_are_removed_before_random_strata_and_ties_are_stable():
    trades = _sample_fixture()
    trades.loc[0:4, "net_return"] = 0.5
    selected = select_audit_sample(trades)
    extremes = selected.loc[selected["audit_category"] == "extreme_gain"]
    random_rows = selected.loc[~selected["audit_category"].str.startswith("extreme")]

    assert extremes["trade_id"].tolist() == ["t-000", "t-001", "t-002"]
    assert set(extremes["source_trade_row"]).isdisjoint(random_rows["source_trade_row"])


def test_manifest_detects_changed_input(tmp_path):
    root = tmp_path
    (root / "configs").mkdir()
    (root / "src" / "texperiment" / "backtest").mkdir(parents=True)
    (root / "uv.lock").write_text("lock", encoding="utf-8")
    table = root / "trades.csv"
    pd.DataFrame([{"trade_id": "t1", "code": "000001.SZ", "status": "valid_trade"}]).to_csv(table, index=False)
    manifest = build_audit_manifest(
        root,
        {"trades.csv": {"key_fields": ("trade_id",), "critical_fields": ("trade_id", "code", "status")}},
    )
    verify_audit_manifest(root, manifest)
    table.write_text("trade_id,code,status\nt2,000001.SZ,valid_trade\n", encoding="utf-8")

    with pytest.raises(ValueError, match="audit inputs changed"):
        verify_audit_manifest(root, manifest)


def test_qfq_only_execution_realism_is_blocking_not_evaluable():
    trade = _trade(1)
    signal = {"signal_id": trade["signal_id"]}
    dates = pd.date_range("2025-10-01", periods=100, freq="D")
    bars = pd.DataFrame({
        "date": dates,
        "code": trade["code"],
        "open": 10.0,
        "high": 10.5,
        "low": 9.5,
        "close": 10.0,
        "volume": 1000,
        "adj_type": "qfq",
        "adj_factor": pd.NA,
        "is_suspended": False,
        "is_limit_up": False,
    })
    trade.update({
        "signal_date": str(dates[-6].date()),
        "pullback_date": str(dates[-7].date()),
        "trigger_date": str(dates[-6].date()),
        "entry_date": str(dates[-5].date()),
        "exit_date": str(dates[-4].date()),
        "exit_price": 9.0,
        "gross_return": -0.1,
        "net_return": -0.102,
    })
    indicators = bars.copy()
    indicators["ma20"] = 10.0
    indicators["ma60"] = 10.0
    indicators["ret20"] = 0.0
    indicators["high_10d"] = 10.5
    indicators["vol_ma5"] = 1000.0
    universe = bars[["date", "code"]].copy()
    universe["is_tradable_universe"] = True

    details = audit_trade(trade, signal=signal, daily_bars=bars, indicators=indicators, universe=universe)

    realism = details.loc[details["check_id"] == "EXECUTION_REALISM"].iloc[0]
    assert realism["verdict"] == "NOT_EVALUABLE_EXECUTION_REALISM"
    assert bool(realism["blocking"]) is True


def test_final_decision_requires_manual_review_and_never_changes_permissions():
    details = pd.DataFrame([{
        "severity": "CRITICAL",
        "verdict": "PASS",
        "blocking": True,
        "reviewer": "",
    }])
    with pytest.raises(ValueError, match="manual review"):
        summarize_audit(details, sample_count=50, manual_review_complete=False)
