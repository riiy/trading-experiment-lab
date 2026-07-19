from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from texperiment.cli import build_parser, cmd_freeze_stock_rs_pullback_recalculation, cmd_run_stock_rs_pullback_recalculation
from texperiment.full_recalculation.formal_cli import (
    ArchiveVerifiedDeltaStage,
    _authorized_runtime_view,
    _formal_stages,
)
from texperiment.full_recalculation.formal_manifest import FormalManifestSpec, build_formal_manifest_v2, write_formal_manifest_atomic
from texperiment.full_recalculation.manifest_canonicalization import manifest_self_sha256, verify_manifest_self_hash
from texperiment.full_recalculation.manifest_validation import validate_formal_manifest_v2
from texperiment.full_recalculation.stages import StageId


def test_builder_emits_complete_self_hashed_v2_manifest_without_opening_comparisons(tmp_path, monkeypatch):
    root, spec = _fixture(tmp_path)
    _identity(monkeypatch)

    manifest = build_formal_manifest_v2(root, spec)

    verify_manifest_self_hash(manifest)
    assert manifest["manifest"]["schema"] == "FULL_PIPELINE_RECALCULATION_MANIFEST_V2"
    assert manifest["repository"]["audited_engine_commit"].startswith("a68770e")
    assert manifest["repository"]["engine_audit_record_commit"].startswith("bce5ab7")
    assert set(manifest["audited_engine"]["files"]) == {
        "runner_sha256", "upstream_sha256", "downstream_sha256", "contract_sha256", "schema_sha256"
    }
    assert manifest["repository"]["runtime_head_commit"] == "c" * 40
    assert manifest["audited_engine"]["implementation_commit"].startswith("a68770e")
    assert manifest["audited_engine"]["audit_record_commit"].startswith("bce5ab7")
    assert manifest["audited_manifest_tool"]["implementation_commit"] == "d" * 40
    assert manifest["audited_manifest_tool"]["audit_record_commit"] == "e" * 40
    assert manifest["authorization_snapshot"] == {
        "manifest_freeze_authorized": True,
        "formal_recalculation_run_authorized": False,
        "account_simulation_allowed": False,
        "ticket_generation_allowed": False,
        "trading_allowed": False,
    }
    assert manifest["run_capabilities"]["strategy_validation_classification_output"] is True
    assert all(value is False for value in manifest["permissions"].values())
    assert manifest["publication"]["fsync_required"] is True
    assert manifest["publication"]["completion_record_required"] is True
    assert manifest["publication"]["artifact_hash_chain_required"] is True
    assert manifest["comparison_inputs"]["allowed_consumers"] == ["DELTA_AND_DECISION"]
    assert not (root / "data/signals/STOCK_RS_PULLBACK_v1_signals.csv").exists()
    assert manifest["integrity"]["manifest_self_sha256"] == manifest_self_sha256(manifest)


def test_same_explicit_inputs_and_time_produce_same_canonical_manifest(tmp_path, monkeypatch):
    root, spec = _fixture(tmp_path)
    _identity(monkeypatch)

    first = build_formal_manifest_v2(root, spec)
    second = build_formal_manifest_v2(root, spec)

    assert first == second


def test_formal_validator_accepts_complete_manifest(tmp_path, monkeypatch):
    root, spec = _fixture(tmp_path)
    _identity(monkeypatch)
    manifest = build_formal_manifest_v2(root, spec)

    validate_formal_manifest_v2(
        root,
        manifest,
        require_clean_repository=False,
        require_manifest_tool_audited=False,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["strategy"].update({"rules_changed": True}),
        lambda value: value["publication"].pop("fsync_required"),
        lambda value: value["authorization_snapshot"].update({"formal_recalculation_run_authorized": True}),
        lambda value: value["run_capabilities"].update({"trading_output": True}),
        lambda value: value["comparison_inputs"].update({"allowed_consumers": ["INPUT_SNAPSHOT"]}),
    ],
)
def test_any_manifest_field_mutation_breaks_self_hash(tmp_path, monkeypatch, mutation):
    root, spec = _fixture(tmp_path)
    _identity(monkeypatch)
    manifest = build_formal_manifest_v2(root, spec)
    mutation(manifest)

    with pytest.raises(ValueError, match="self hash"):
        validate_formal_manifest_v2(
            root,
            manifest,
            require_clean_repository=False,
            require_manifest_tool_audited=False,
        )


def test_validator_rejects_v1_or_replay_manifest(tmp_path, monkeypatch):
    root, spec = _fixture(tmp_path)
    _identity(monkeypatch)
    manifest = build_formal_manifest_v2(root, spec)
    manifest["manifest"]["schema"] = "STOCK_RS_PULLBACK_v1_RECALCULATION_MANIFEST_v1"
    manifest["manifest"]["run_type"] = "SIGNAL_EXECUTION_REPLAY"
    manifest["integrity"]["manifest_self_sha256"] = manifest_self_sha256(manifest)

    with pytest.raises(ValueError, match="not V2"):
        validate_formal_manifest_v2(
            root,
            manifest,
            require_clean_repository=False,
            require_manifest_tool_audited=False,
        )


def test_builder_rejects_dirty_repository_and_engine_drift(tmp_path, monkeypatch):
    root, spec = _fixture(tmp_path)
    monkeypatch.setattr("texperiment.full_recalculation.formal_manifest.repository_state", lambda root: ("c" * 40, True))
    monkeypatch.setattr("texperiment.full_recalculation.formal_manifest.audited_engine_hashes", lambda root: {"runner": "a"})
    monkeypatch.setattr("texperiment.full_recalculation.formal_manifest.current_engine_hashes", lambda root: {"runner": "a"})
    with pytest.raises(ValueError, match="clean"):
        build_formal_manifest_v2(root, spec)

    monkeypatch.setattr("texperiment.full_recalculation.formal_manifest.repository_state", lambda root: ("c" * 40, False))
    monkeypatch.setattr("texperiment.full_recalculation.formal_manifest.current_engine_hashes", lambda root: {"runner": "b"})
    with pytest.raises(ValueError, match="AUDITED_ENGINE_DRIFT"):
        build_formal_manifest_v2(root, spec)


def test_output_conflict_and_atomic_write_failure_leave_no_manifest(tmp_path, monkeypatch):
    root, spec = _fixture(tmp_path)
    _identity(monkeypatch)
    manifest = build_formal_manifest_v2(root, spec)
    output = root / "manifest.json"
    monkeypatch.setattr("texperiment.full_recalculation.formal_manifest.os.replace", lambda source, target: (_ for _ in ()).throw(OSError("move failed")))

    with pytest.raises(OSError, match="move failed"):
        write_formal_manifest_atomic(manifest, output)
    assert not output.exists()
    assert not list(root.glob(".manifest.json.*.tmp"))

    spec.final_root.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        build_formal_manifest_v2(root, spec)


def test_cli_routes_are_explicit_and_legacy_commands_refuse_formal_path():
    parser = build_parser()
    freeze = parser.parse_args(["freeze-stock-rs-pullback-recalculation-v2", "--run-id", "run-1"])
    validate = parser.parse_args(["validate-stock-rs-pullback-recalculation-manifest-v2", "--manifest", "m.json"])
    run = parser.parse_args(["run-stock-rs-pullback-recalculation-v2", "--manifest", "m.json"])
    assert freeze.func.__name__.endswith("recalculation_v2")
    assert validate.func.__name__.endswith("manifest_v2")
    assert run.func.__name__.endswith("recalculation_v2")
    with pytest.raises(SystemExit, match="SIGNAL_EXECUTION_REPLAY"):
        cmd_freeze_stock_rs_pullback_recalculation(None)
    with pytest.raises(SystemExit, match="SIGNAL_EXECUTION_REPLAY"):
        cmd_run_stock_rs_pullback_recalculation(None)


def test_formal_stage_factory_binds_all_eight_real_stages(tmp_path):
    stages = _formal_stages(tmp_path, {"comparison_inputs": {"archive_manifest": {"path": "a", "expected_sha256": "b"}}})
    assert tuple(stages) == tuple(StageId)
    assert isinstance(stages[StageId.DELTA_AND_DECISION], ArchiveVerifiedDeltaStage)


def test_external_authorization_adapter_does_not_mutate_frozen_manifest(tmp_path, monkeypatch):
    root, spec = _fixture(tmp_path)
    _identity(monkeypatch)
    manifest = build_formal_manifest_v2(root, spec)
    frozen_hash = manifest["integrity"]["manifest_self_sha256"]

    runtime = _authorized_runtime_view(manifest)

    assert manifest["permissions"]["full_recalculation_allowed"] is False
    assert manifest["authorization_snapshot"]["formal_recalculation_run_authorized"] is False
    assert manifest["integrity"]["manifest_self_sha256"] == frozen_hash
    verify_manifest_self_hash(manifest)
    assert runtime["permissions"]["full_recalculation_allowed"] is True
    assert runtime["permissions"]["strategy_validation_decision_allowed"] is False


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("authorization_snapshot", "formal_recalculation_run_authorized", True),
        ("authorization_snapshot", "trading_allowed", True),
        ("run_capabilities", "strategy_validation_classification_output", False),
        ("run_capabilities", "account_simulation_output", True),
        ("publication", "atomic_rename_required", False),
        ("publication", "fsync_required", False),
        ("publication", "completion_record_required", False),
        ("publication", "artifact_hash_chain_required", False),
    ],
)
def test_validator_rejects_unsafe_authorization_capabilities_and_publication(
    tmp_path, monkeypatch, section, field, value
):
    root, spec = _fixture(tmp_path)
    _identity(monkeypatch)
    manifest = build_formal_manifest_v2(root, spec)
    manifest[section][field] = value
    manifest["integrity"]["manifest_self_sha256"] = manifest_self_sha256(manifest)

    with pytest.raises(ValueError):
        validate_formal_manifest_v2(
            root,
            manifest,
            require_clean_repository=False,
            require_manifest_tool_audited=False,
        )


def test_validator_rejects_manifest_tool_audit_identity_tamper(tmp_path, monkeypatch):
    root, spec = _fixture(tmp_path)
    _identity(monkeypatch)
    manifest = build_formal_manifest_v2(root, spec)
    manifest["audited_manifest_tool"]["audit_record_commit"] = "not-a-commit"
    manifest["integrity"]["manifest_self_sha256"] = manifest_self_sha256(manifest)

    with pytest.raises(ValueError, match="audit record"):
        validate_formal_manifest_v2(
            root,
            manifest,
            require_clean_repository=False,
            require_manifest_tool_audited=False,
        )


def _identity(monkeypatch):
    hashes = {
        "runner_sha256": "1" * 64,
        "upstream_sha256": "2" * 64,
        "downstream_sha256": "3" * 64,
        "contract_sha256": "4" * 64,
        "schema_sha256": "5" * 64,
    }
    monkeypatch.setattr("texperiment.full_recalculation.formal_manifest.repository_state", lambda root: ("c" * 40, False))
    monkeypatch.setattr("texperiment.full_recalculation.formal_manifest.audited_engine_hashes", lambda root: hashes)
    monkeypatch.setattr("texperiment.full_recalculation.formal_manifest.current_engine_hashes", lambda root: hashes)
    tool_hashes = {"tool": "6" * 64}
    monkeypatch.setattr("texperiment.full_recalculation.formal_manifest.manifest_tool_hashes_at_commit", lambda root, commit: tool_hashes)
    monkeypatch.setattr("texperiment.full_recalculation.formal_manifest.current_manifest_tool_hashes", lambda root: tool_hashes)
    monkeypatch.setattr("texperiment.full_recalculation.manifest_validation.repository_state", lambda root: ("c" * 40, False))
    monkeypatch.setattr("texperiment.full_recalculation.manifest_validation.current_engine_hashes", lambda root: hashes)
    monkeypatch.setattr("texperiment.full_recalculation.manifest_validation.current_manifest_tool_hashes", lambda root: tool_hashes)


def _fixture(tmp_path):
    root = tmp_path
    inputs = root / "inputs"
    inputs.mkdir()
    dates = pd.date_range("2026-01-01", periods=3)
    bars = pd.DataFrame({
        "date": dates,
        "code": "000001.SZ",
        "open": [10.0, 10.1, 10.2],
        "high": [10.2, 10.3, 10.4],
        "low": [9.9, 10.0, 10.1],
        "close": [10.1, 10.2, 10.3],
    })
    raw = inputs / "raw.parquet"
    qfq = inputs / "qfq.parquet"
    benchmark = inputs / "benchmark.parquet"
    bars.to_parquet(raw, index=False)
    bars.to_parquet(qfq, index=False)
    bars.assign(code="000300.SH").to_parquet(benchmark, index=False)
    setup = inputs / "setup.yaml"
    setup.write_text("setup_id: STOCK_RS_PULLBACK_v1\nvalidation_threshold:\n  min_valid_trades: 80\n", encoding="utf-8")
    cost = inputs / "cost.yaml"
    cost.write_text("round_trip_cost: 0.002\n", encoding="utf-8")
    st = inputs / "st.json"
    st.write_text("{}", encoding="utf-8")
    archive = inputs / "archive.json"
    archive.write_text(json.dumps({"inputs": [
        {"path": "data/signals/STOCK_RS_PULLBACK_v1_signals.csv", "sha256": "a" * 64},
        {"path": "data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv", "sha256": "b" * 64},
        {"path": "data/reports/STOCK_RS_PULLBACK_v1_metrics.json", "sha256": "c" * 64},
    ]}), encoding="utf-8")
    spec = FormalManifestSpec(
        run_id="STOCK_RS_PULLBACK_v1_RECALCULATED_fixture",
        raw_daily=raw,
        qfq_daily=qfq,
        benchmark=benchmark,
        setup_config=setup,
        cost_config=cost,
        st_overrides=st,
        archive_manifest=archive,
        temporary_root=root / "data/recalculations/.tmp/run",
        final_root=root / "data/recalculations/STOCK_RS_PULLBACK_v1_RECALCULATED/run",
        manifest_tool_commit="d" * 40,
        manifest_tool_audit_record_commit="e" * 40,
        created_at="2026-07-19T00:00:00+08:00",
    )
    return root, spec
