from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from texperiment.audit.comparison_archive import (
    ComparisonArchiveError,
    audit_comparison_archive,
    build_comparison_archive,
    write_comparison_archive_atomic,
)
from texperiment.audit.manifest import sha256_file
from texperiment.full_recalculation.formal_manifest import comparison_references_from_archive


def test_comparison_archive_freezes_missing_metrics_and_audits_them_independently(tmp_path):
    root, history = _fixture(tmp_path)
    archive = build_comparison_archive(root, historical_audit_manifest=history, created_at="2026-08-03T00:00:00+08:00")
    path = root / "diagnostics/comparison_archive.json"
    write_comparison_archive_atomic(archive, path)

    audit = audit_comparison_archive(root, path)

    assert audit["decision"] == "ORIGINAL_METRICS_ARCHIVE_AUDIT_PASSED"
    assert audit["strategy_decision_generated"] is False
    assert audit["overall_checks"]["profit_factor"]["matched"] is True
    assert audit["yearly_checks"]["matched"] is True
    assert audit["signal_trade_lineage"]["matched"] is True
    references = comparison_references_from_archive(root, path)
    assert references["original_metrics"]["expected_sha256"] == sha256_file(root / "data/reports/STOCK_RS_PULLBACK_v1_metrics.json")


def test_comparison_archive_rejects_historical_signal_or_trade_drift(tmp_path):
    root, history = _fixture(tmp_path)
    (root / "data/signals/STOCK_RS_PULLBACK_v1_signals.csv").write_text("signal_id\ns2\n", encoding="utf-8")

    with pytest.raises(ComparisonArchiveError, match="historical artifact hash drift: original_signals"):
        build_comparison_archive(root, historical_audit_manifest=history)


def test_comparison_archive_audit_rejects_metrics_tamper_and_existing_output(tmp_path):
    root, history = _fixture(tmp_path)
    archive = build_comparison_archive(root, historical_audit_manifest=history)
    path = root / "diagnostics/comparison_archive.json"
    write_comparison_archive_atomic(archive, path)
    with pytest.raises(FileExistsError):
        write_comparison_archive_atomic(archive, path)

    metrics = root / "data/reports/STOCK_RS_PULLBACK_v1_metrics.json"
    payload = json.loads(metrics.read_text(encoding="utf-8"))
    payload["overall"]["mean_net_return"] = 99
    metrics.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ComparisonArchiveError, match="comparison artifact hash drift: original_metrics"):
        audit_comparison_archive(root, path)


def test_comparison_archive_audit_rejects_metrics_inconsistent_with_trades(tmp_path):
    root, history = _fixture(tmp_path)
    metrics = root / "data/reports/STOCK_RS_PULLBACK_v1_metrics.json"
    payload = json.loads(metrics.read_text(encoding="utf-8"))
    payload["overall"]["mean_net_return"] = 99
    metrics.write_text(json.dumps(payload), encoding="utf-8")
    archive = build_comparison_archive(root, historical_audit_manifest=history)
    path = root / "diagnostics/comparison_archive.json"
    write_comparison_archive_atomic(archive, path)

    with pytest.raises(ComparisonArchiveError, match="overall values"):
        audit_comparison_archive(root, path)


def _fixture(root: Path) -> tuple[Path, Path]:
    for relative in ("data/signals", "data/trades", "data/reports", "diagnostics"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    signals = pd.DataFrame({"signal_id": ["s1", "s2", "s3"]})
    trades = pd.DataFrame([
        {"signal_id": "s1", "status": "valid_trade", "net_return": 0.10, "exit_date": "2024-01-02"},
        {"signal_id": "s2", "status": "valid_trade", "net_return": -0.04, "exit_date": "2024-01-03"},
        {"signal_id": "s3", "status": "invalid_trade", "net_return": None, "exit_date": None},
    ])
    signals_path = root / "data/signals/STOCK_RS_PULLBACK_v1_signals.csv"
    trades_path = root / "data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv"
    metrics_path = root / "data/reports/STOCK_RS_PULLBACK_v1_metrics.json"
    signals.to_csv(signals_path, index=False)
    trades.to_csv(trades_path, index=False)
    metrics_path.write_text(json.dumps(_metrics()), encoding="utf-8")
    history = root / "diagnostics/historical_audit.json"
    history.write_text(json.dumps({"inputs": [
        {"path": "data/signals/STOCK_RS_PULLBACK_v1_signals.csv", "sha256": sha256_file(signals_path)},
        {"path": "data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv", "sha256": sha256_file(trades_path)},
    ]}), encoding="utf-8")
    return root, history


def _metrics() -> dict:
    return {
        "setup_id": "STOCK_RS_PULLBACK_v1",
        "decision": "FAILED_ARCHIVED",
        "overall": {
            "rows": 3,
            "valid_trades": 2,
            "invalid_trades": 1,
            "mean_net_return": 0.03,
            "median_net_return": 0.03,
            "win_rate": 0.5,
            "profit_factor": 2.5,
            "best_3_removed_mean": 0.0,
            "top3_contribution_sum": 0.06,
            "top3_contribution_ratio": 1.0,
            "net_return_sum": 0.06,
            "max_gain": 0.1,
            "max_loss": -0.04,
        },
        "yearly": [{
            "year": 2024,
            "valid_trades": 2,
            "mean_net_return": 0.03,
            "median_net_return": 0.03,
            "win_rate": 0.5,
            "profit_factor": 2.5,
            "best_3_removed_mean": 0.0,
            "net_return_sum": 0.06,
        }],
    }
