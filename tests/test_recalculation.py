from __future__ import annotations

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from texperiment.recalculation import (
    COST_MODEL_VERSION,
    EXECUTION_MODEL_VERSION,
    INPUT_PATHS,
    PRICE_LIMIT_RULE_VERSION,
    RAW_DIRECTORIES,
    RECALCULATED_ID,
    RECALCULATION_MANIFEST_VERSION,
    ParquetCodeReader,
    _prepare_output_directories,
    _validate_signal_population,
    _validate_manifest_schema,
    build_delta_summary,
    render_delta_report,
)


def test_parquet_code_reader_reads_single_code_row_groups(tmp_path):
    path = tmp_path / "bars.parquet"
    first = pa.Table.from_pandas(pd.DataFrame({"date": pd.to_datetime(["2026-01-01"]), "code": ["000001.SZ"], "close": [10.0]}), preserve_index=False)
    second = pa.Table.from_pandas(pd.DataFrame({"date": pd.to_datetime(["2026-01-01"]), "code": ["600000.SH"], "close": [20.0]}), preserve_index=False)
    with pq.ParquetWriter(path, first.schema) as writer:
        writer.write_table(first)
        writer.write_table(second)

    reader = ParquetCodeReader(path)
    out = reader.read_code("600000.SH", ["date", "code", "close"])

    assert out.to_dict("records") == [{"date": pd.Timestamp("2026-01-01"), "code": "600000.SH", "close": 20.0}]


def test_parquet_code_reader_filters_mixed_code_row_group(tmp_path):
    path = tmp_path / "bars.parquet"
    table = pa.Table.from_pandas(pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-01"]),
        "code": ["000001.SZ", "000001.SZ", "600000.SH"],
        "close": [10.0, 11.0, 20.0],
    }), preserve_index=False)
    pq.write_table(table, path)

    out = ParquetCodeReader(path).read_code("600000.SH", ["date", "code", "close"])

    assert out.to_dict("records") == [{"date": pd.Timestamp("2026-01-01"), "code": "600000.SH", "close": 20.0}]


def test_manifest_schema_rejects_extra_fields():
    manifest = _manifest()
    manifest["unfrozen_override"] = True

    with pytest.raises(ValueError, match="fields mismatch"):
        _validate_manifest_schema(manifest)


def test_manifest_schema_rejects_substituted_input_path():
    manifest = _manifest()
    manifest["inputs"]["signals"]["path"] = "data/signals/substitute.csv"

    with pytest.raises(ValueError, match="input contract invalid"):
        _validate_manifest_schema(manifest)


def test_output_directories_refuse_existing_target(tmp_path):
    existing = tmp_path / "trades"
    existing.mkdir()

    with pytest.raises(FileExistsError, match="refusing overwrite"):
        _prepare_output_directories((existing, tmp_path / "reports"))


def test_signal_population_rejects_duplicate_signal_ids():
    signals = pd.DataFrame({"signal_id": ["s1", "s1"]})
    original = pd.DataFrame({"signal_id": ["s1", "s2"]})

    with pytest.raises(ValueError, match="non-null unique signal_id"):
        _validate_signal_population(signals, original)


def test_signal_population_requires_exact_recalculated_set():
    signals = pd.DataFrame({"signal_id": ["s1", "s2"]})
    original = pd.DataFrame({"signal_id": ["s1", "s2"]})
    recalculated = pd.DataFrame({"signal_id": ["s1", "s3"]})

    with pytest.raises(ValueError, match="one outcome per signal"):
        _validate_signal_population(signals, original, recalculated)


def test_delta_summary_reports_execution_and_metric_changes():
    original = pd.DataFrame([
        {"signal_id": "s1", "status": "invalid_trade", "invalid_reason": "invalid_limit_up_cannot_buy", "exit_reason": None, "net_return": None},
        {"signal_id": "s2", "status": "valid_trade", "invalid_reason": None, "exit_reason": "time_stop_no_upside_progress", "net_return": -0.1},
    ])
    recalculated = pd.DataFrame([
        {"signal_id": "s1", "status": "valid_trade", "invalid_reason": None, "exit_reason": "target_2r", "net_return": 0.1, "holding_days": 3},
        {"signal_id": "s2", "status": "valid_trade", "invalid_reason": None, "exit_reason": "time_stop_no_upside_progress", "net_return": -0.2, "holding_days": 6},
    ])
    original_metrics = {"overall": _overall(1, 1, -0.1), "yearly": [{"year": 2026, "valid_trades": 1}]}
    new_metrics = {"overall": _overall(2, 0, -0.05), "yearly": [{"year": 2026, "valid_trades": 2}]}

    delta = build_delta_summary(original, recalculated, original_metrics, new_metrics)
    report = render_delta_report({**delta, "decision": "CONFIRMED_FAILED_ARCHIVED", "material_blocking_trade_count": 0, "unexpected_invalid_outcomes": 0})

    assert delta["fixed_limit_up_exclusions"] == 1
    assert delta["new_valid_entries"] == 1
    assert delta["scheduled_close_delays"] == {"count": 1, "average_delay_days": 1.0, "max_delay_days": 1}
    assert "Fixed limit-up exclusions: `1`" in report


def test_delta_summary_handles_empty_yearly_metrics():
    trades = pd.DataFrame([{
        "signal_id": "s1", "status": "valid_trade", "invalid_reason": None,
        "exit_reason": "target_2r", "net_return": 0.1, "holding_days": 3,
    }])

    delta = build_delta_summary(trades, trades, {"overall": _overall(1, 0, 0.1)}, {"overall": _overall(1, 0, 0.1)})

    assert delta["yearly"] == []


def _overall(valid: int, invalid: int, mean: float):
    return {
        "valid_trades": valid,
        "invalid_trades": invalid,
        "mean_net_return": mean,
        "median_net_return": mean,
        "profit_factor": 0.8,
        "win_rate": 0.4,
        "best_3_removed_mean": mean,
        "top3_contribution_ratio": "inf",
        "max_gain": 0.1,
        "max_loss": -0.2,
    }


def _manifest():
    return {
        "manifest_version": RECALCULATION_MANIFEST_VERSION,
        "created_at": "2026-07-18T00:00:00+00:00",
        "engine_git_commit": "a" * 40,
        "git_dirty": False,
        "engine_source_sha256": "source",
        "environment": {
            "python": "3.11.0", "implementation": "CPython", "platform": "Linux",
            "uv_lock_sha256": "lock", "pyproject_sha256": "project",
        },
        "inputs": {name: {"path": path, "sha256": name} for name, path in INPUT_PATHS.items()},
        "raw_inputs": {name: {"path": path, "sha256": name} for name, path in RAW_DIRECTORIES.items()},
        "strategy_config_sha256": "setup_config",
        "strategy_rules_frozen": {name: {} for name in ("strength_filter", "pullback_filter", "entry", "exit", "cost")},
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "price_limit_rule_version": PRICE_LIMIT_RULE_VERSION,
        "cost_model_version": COST_MODEL_VERSION,
        "runtime_parameters": {"window_rows": 120},
        "original_audit_commit": "b" * 40,
        "remediation_audit_commit": "c" * 40,
        "remediation_decision": "REMEDIATION_AUDIT_PASSED",
        "allowed_post_freeze_commit_paths": ["diagnostics/STOCK_RS_PULLBACK_v1/recalculation_manifest.json"],
        "full_recalculation_performed": False,
        "output_id": RECALCULATED_ID,
    }
