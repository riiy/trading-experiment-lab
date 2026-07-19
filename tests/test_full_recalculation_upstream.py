from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest
import yaml

from texperiment.audit.manifest import profile_table, sha256_file
from texperiment.full_recalculation.contract import EXPECTED_STAGES, FORBIDDEN_PIPELINE_INPUTS
from texperiment.full_recalculation.immutability import RecalculationAbort
from texperiment.full_recalculation.downstream import (
    DeltaAndDecisionStage,
    MetricsRebuildStage,
    SignalRebuildStage,
    TradeRebuildStage,
)
from texperiment.full_recalculation.runner import FullPipelineRunner
from texperiment.full_recalculation.stages import StageContext, StageId
from texperiment.full_recalculation.upstream import (
    IndicatorRebuildStage,
    InputSnapshotStage,
    MarketStateRebuildStage,
    UniverseRebuildStage,
)


def test_upstream_pipeline_runs_exact_prefix_and_registers_artifacts(tmp_path):
    fixture = _fixture(tmp_path)

    context, results = _run_upstream(fixture, "run-001")

    assert [result.stage.value for result in results] == list(EXPECTED_STAGES[:4])
    assert set(context.artifacts) >= {
        "snapshot.metadata",
        "market_state.daily",
        "universe.daily",
        "indicators.daily",
        "indicators.eligible_sample",
    }
    assert not context.final_root.exists()


def test_upstream_pipeline_does_not_require_old_strategy_outputs(tmp_path):
    fixture = _fixture(tmp_path)
    for path in FORBIDDEN_PIPELINE_INPUTS:
        assert not (fixture.root / path).exists()

    _, results = _run_upstream(fixture, "run-without-originals")

    assert len(results) == 4


def test_same_inputs_produce_same_upstream_data_hashes(tmp_path):
    fixture = _fixture(tmp_path)

    first, _ = _run_upstream(fixture, "deterministic-1")
    second, _ = _run_upstream(fixture, "deterministic-2")

    for name in ("market_state.daily", "universe.daily", "indicators.daily", "indicators.eligible_sample"):
        assert first.artifacts[name].sha256 == second.artifacts[name].sha256


@pytest.mark.parametrize(
    "column",
    [
        "raw_qfq_ratio",
        "mapping_status",
        "trade_status",
        "is_suspended",
        "limit_up_price",
        "limit_down_price",
        "open_at_limit_up",
        "open_at_limit_down",
        "close_at_limit_up",
        "close_at_limit_down",
        "one_price_limit_up",
        "one_price_limit_down",
        "can_buy_at_open",
        "can_sell_at_open",
        "can_sell_at_close",
        "st_branch_status",
    ],
)
def test_market_state_contains_required_execution_columns(tmp_path, column):
    fixture = _fixture(tmp_path)

    context, _ = _run_upstream(fixture, f"column-{column}")
    market = pd.read_parquet(context.artifacts["market_state.daily"].path)

    assert column in market.columns


def test_raw_qfq_mapping_is_stable_and_known(tmp_path):
    fixture = _fixture(tmp_path)

    context, _ = _run_upstream(fixture, "mapping")
    market = pd.read_parquet(context.artifacts["market_state.daily"].path)

    assert market["raw_qfq_ratio"].eq(0.5).all()
    assert market["mapping_status"].eq("KNOWN_AFFINE_RAW_QFQ_VALIDATED").all()


def test_market_suspension_propagates_to_universe(tmp_path):
    fixture = _fixture(tmp_path, suspended_position=30)

    context, _ = _run_upstream(fixture, "suspension")
    universe = pd.read_parquet(context.artifacts["universe.daily"].path)
    row = universe.iloc[30]

    assert bool(row["pass_suspension"]) is False
    assert bool(row["pass_execution_state"]) is False
    assert bool(row["is_tradable_universe"]) is False


def test_universe_eligibility_propagates_to_indicator_sample(tmp_path):
    normal = _fixture(tmp_path / "normal")
    suspended = _fixture(tmp_path / "suspended", suspended_position=30)

    normal_context, _ = _run_upstream(normal, "sample-normal")
    suspended_context, _ = _run_upstream(suspended, "sample-suspended")
    normal_sample = pd.read_parquet(normal_context.artifacts["indicators.eligible_sample"].path)
    suspended_sample = pd.read_parquet(suspended_context.artifacts["indicators.eligible_sample"].path)

    assert len(suspended_sample) == len(normal_sample) - 1


def test_registered_input_hash_drift_blocks_downstream_stage(tmp_path):
    fixture = _fixture(tmp_path)
    context = _context(fixture, "drift")
    InputSnapshotStage(repository_state=_clean_repository).run(context)
    raw = pd.read_parquet(fixture.raw)
    raw.loc[0, "close"] += 1
    raw.to_parquet(fixture.raw, index=False)

    with pytest.raises(RecalculationAbort) as caught:
        MarketStateRebuildStage().run(context)

    assert caught.value.decision == "RECALCULATION_ABORTED_INPUT_DRIFT"


def test_raw_qfq_key_mismatch_blocks_snapshot(tmp_path):
    fixture = _fixture(tmp_path)
    qfq = pd.read_parquet(fixture.qfq)
    qfq.loc[0, "code"] = "600000.SH"
    qfq.to_parquet(fixture.qfq, index=False)
    fixture.manifest["inputs"]["qfq_daily"] = _market_spec(fixture.qfq, "qfq", "fixture_qfq")

    with pytest.raises(RecalculationAbort) as caught:
        InputSnapshotStage(repository_state=_clean_repository).run(_context(fixture, "key-mismatch"))

    assert caught.value.decision == "RECALCULATION_ABORTED_PIPELINE_CONTRACT_MISMATCH"


def test_dirty_repository_blocks_before_temporary_directory_creation(tmp_path):
    fixture = _fixture(tmp_path)
    context = _context(fixture, "dirty")

    with pytest.raises(RecalculationAbort) as caught:
        InputSnapshotStage(repository_state=lambda root: ("b" * 40, True)).run(context)

    assert caught.value.decision == "RECALCULATION_ABORTED_DIRTY_WORKTREE"
    assert not context.work_root.exists()
    assert not context.final_root.exists()


def test_existing_formal_output_blocks_snapshot(tmp_path):
    fixture = _fixture(tmp_path)
    context = _context(fixture, "existing")
    context.final_root.mkdir(parents=True)

    with pytest.raises(RecalculationAbort) as caught:
        InputSnapshotStage(repository_state=_clean_repository).run(context)

    assert caught.value.decision == "RECALCULATION_ABORTED_OUTPUT_EXISTS"


def test_missing_input_blocks_snapshot(tmp_path):
    fixture = _fixture(tmp_path)
    fixture.benchmark.unlink()

    with pytest.raises(FileNotFoundError):
        InputSnapshotStage(repository_state=_clean_repository).run(_context(fixture, "missing"))


def test_input_source_drift_blocks_snapshot(tmp_path):
    fixture = _fixture(tmp_path)
    fixture.manifest["inputs"]["raw_daily"]["source"] = "wrong_source"

    with pytest.raises(RecalculationAbort) as caught:
        InputSnapshotStage(repository_state=_clean_repository).run(_context(fixture, "source-drift"))

    assert caught.value.decision == "RECALCULATION_ABORTED_INPUT_DRIFT"


def test_each_completed_stage_writes_machine_record(tmp_path):
    fixture = _fixture(tmp_path)

    context, _ = _run_upstream(fixture, "records")

    for name in EXPECTED_STAGES[:4]:
        record = json.loads((context.work_root / name / "stage.json").read_text(encoding="utf-8"))
        assert record["stage"] == name
        assert record["status"] == "PASSED"
        assert record["blocking_errors"] == []


def test_original_inputs_are_not_modified_by_upstream_pipeline(tmp_path):
    fixture = _fixture(tmp_path)
    before = {path: sha256_file(path) for path in (fixture.raw, fixture.qfq, fixture.benchmark)}

    _run_upstream(fixture, "immutable-inputs")

    assert {path: sha256_file(path) for path in before} == before


def test_stage_failure_moves_temporary_work_to_diagnostics(tmp_path):
    fixture = _fixture(tmp_path)
    context = _context(fixture, "quarantine")
    stages = {
        StageId(name): (
            InputSnapshotStage(repository_state=_clean_repository)
            if StageId(name) == StageId.INPUT_SNAPSHOT
            else _FailingStage(StageId(name))
            if StageId(name) == StageId.MARKET_STATE_REBUILD
            else _NeverStage(StageId(name))
        )
        for name in EXPECTED_STAGES
    }

    with pytest.raises(Exception, match="MARKET_STATE_REBUILD"):
        FullPipelineRunner(stages).run_until(context, StageId.INDICATOR_REBUILD)

    assert not context.work_root.exists()
    assert not context.final_root.exists()
    failure = json.loads((context.failure_root / "failure.json").read_text(encoding="utf-8"))
    assert failure["decision"] == "RECALCULATION_ABORTED_STAGE_FAILURE"
    assert failure["strategy_decision_generated"] is False


def test_all_eight_stages_run_without_publishing_formal_output(tmp_path):
    fixture = _fixture(tmp_path)
    _add_archived_comparisons(fixture)

    context, results = _run_all(fixture, "full-development")

    assert [result.stage.value for result in results] == list(EXPECTED_STAGES)
    assert set(context.artifacts) >= {"signals.rebuilt", "trades.rebuilt", "metrics.rebuilt", "delta.preview"}
    preview = json.loads(context.artifacts["delta.preview"].path.read_text(encoding="utf-8"))["decision_preview"]
    assert preview["authoritative"] is False
    assert preview["published"] is False
    assert not context.final_root.exists()


def test_old_signals_change_only_delta_hash_not_new_pipeline_hashes(tmp_path):
    fixture = _fixture(tmp_path)
    archived = _add_archived_comparisons(fixture)
    first, _ = _run_all(fixture, "isolation-first")
    archived["signals"].write_text(
        "signal_id,status,code,pullback_date,trigger_date\nold,triggered_entry_next_open,000001.SZ,2025-01-02,2025-01-03\n",
        encoding="utf-8",
    )
    fixture.manifest["comparison_only_inputs"]["original_signals"]["sha256"] = sha256_file(archived["signals"])

    second, _ = _run_all(fixture, "isolation-second")

    assert first.artifacts["signals.rebuilt"].sha256 == second.artifacts["signals.rebuilt"].sha256
    assert first.artifacts["trades.rebuilt"].sha256 == second.artifacts["trades.rebuilt"].sha256
    assert first.artifacts["delta.preview"].sha256 != second.artifacts["delta.preview"].sha256


def test_archived_comparison_hash_drift_fails_closed_at_delta(tmp_path):
    fixture = _fixture(tmp_path)
    archived = _add_archived_comparisons(fixture)
    context = _context(fixture, "archive-drift")
    stages = _all_stages()
    for stage_id in list(stages)[:-1]:
        stages[stage_id].run(context)
    archived["trades"].write_text("changed", encoding="utf-8")

    with pytest.raises(RecalculationAbort) as caught:
        stages[StageId.DELTA_AND_DECISION].run(context)

    assert caught.value.decision == "RECALCULATION_ABORTED_INPUT_DRIFT"


def test_missing_archived_comparisons_fails_closed_at_delta(tmp_path):
    fixture = _fixture(tmp_path)
    context = _context(fixture, "archive-missing")
    stages = _all_stages()
    for stage_id in list(stages)[:-1]:
        stages[stage_id].run(context)

    with pytest.raises(RecalculationAbort) as caught:
        stages[StageId.DELTA_AND_DECISION].run(context)

    assert caught.value.decision == "RECALCULATION_ABORTED_PIPELINE_CONTRACT_MISMATCH"


@dataclass
class _Fixture:
    root: Path
    raw: Path
    qfq: Path
    benchmark: Path
    setup: Path
    cost: Path
    policy: Path
    manifest: dict


class _NeverStage:
    def __init__(self, stage_id: StageId):
        self.stage_id = stage_id

    def run(self, context):
        raise AssertionError(f"downstream stage ran unexpectedly: {self.stage_id.value}")


class _FailingStage:
    def __init__(self, stage_id: StageId):
        self.stage_id = stage_id

    def run(self, context):
        stage_dir = context.work_root / self.stage_id.value
        stage_dir.mkdir()
        (stage_dir / "partial.txt").write_text("partial", encoding="utf-8")
        raise RuntimeError("forced upstream failure")


def _run_upstream(fixture: _Fixture, run_id: str):
    context = _context(fixture, run_id)
    concrete = {
        StageId.INPUT_SNAPSHOT: InputSnapshotStage(repository_state=_clean_repository, batch_size=17),
        StageId.MARKET_STATE_REBUILD: MarketStateRebuildStage(batch_size=17),
        StageId.UNIVERSE_REBUILD: UniverseRebuildStage(batch_size=17),
        StageId.INDICATOR_REBUILD: IndicatorRebuildStage(batch_size=17),
    }
    stages = {
        StageId(name): concrete.get(StageId(name), _NeverStage(StageId(name)))
        for name in EXPECTED_STAGES
    }
    results = FullPipelineRunner(stages).run_until(context, StageId.INDICATOR_REBUILD)
    return context, results


def _run_all(fixture: _Fixture, run_id: str):
    context = _context(fixture, run_id)
    results = FullPipelineRunner(_all_stages()).run_until(context, StageId.DELTA_AND_DECISION)
    return context, results


def _all_stages():
    return {
        StageId.INPUT_SNAPSHOT: InputSnapshotStage(repository_state=_clean_repository, batch_size=17),
        StageId.MARKET_STATE_REBUILD: MarketStateRebuildStage(batch_size=17),
        StageId.UNIVERSE_REBUILD: UniverseRebuildStage(batch_size=17),
        StageId.INDICATOR_REBUILD: IndicatorRebuildStage(batch_size=17),
        StageId.SIGNAL_REBUILD: SignalRebuildStage(batch_size=17),
        StageId.TRADE_REBUILD: TradeRebuildStage(batch_size=17),
        StageId.METRICS_REBUILD: MetricsRebuildStage(),
        StageId.DELTA_AND_DECISION: DeltaAndDecisionStage(),
    }


def _add_archived_comparisons(fixture: _Fixture) -> dict[str, Path]:
    archive = fixture.root / "archive"
    archive.mkdir()
    signals = archive / "signals.csv"
    trades = archive / "trades.csv"
    metrics = archive / "metrics.json"
    signals.write_text("signal_id,status\n", encoding="utf-8")
    pd.DataFrame(columns=["signal_id", "status", "entry_date", "entry_price", "exit_date", "exit_price", "exit_reason", "holding_days", "net_return"]).to_csv(trades, index=False)
    metrics.write_text(json.dumps({"overall": {}}), encoding="utf-8")
    paths = {"signals": signals, "trades": trades, "metrics": metrics}
    fixture.manifest["comparison_only_inputs"] = {
        f"original_{name}": {"path": str(path), "sha256": sha256_file(path)}
        for name, path in paths.items()
    }
    return paths


def _context(fixture: _Fixture, run_id: str) -> StageContext:
    return StageContext(
        run_id=run_id,
        project_root=fixture.root,
        work_root=fixture.root / "data" / "recalculations" / ".tmp" / run_id,
        final_root=fixture.root / "data" / "recalculations" / "STOCK_RS_PULLBACK_v1_RECALCULATED" / run_id,
        failure_root=fixture.root / "diagnostics" / "recalculation_attempts" / run_id,
        manifest=fixture.manifest,
    )


def _clean_repository(root: Path) -> tuple[str, bool]:
    return "b" * 40, False


def _fixture(root: Path, *, suspended_position: int | None = None) -> _Fixture:
    root.mkdir(parents=True, exist_ok=True)
    inputs = root / "inputs"
    inputs.mkdir()
    dates = pd.bdate_range("2025-01-02", periods=75)
    raw_close = pd.Series([20 + index * 0.05 for index in range(len(dates))], dtype=float)
    raw = pd.DataFrame({
        "date": dates,
        "code": "000001.SZ",
        "name": "fixture",
        "board": "MAIN_SZ",
        "listing_date": pd.Timestamp("2020-01-02"),
        "listing_date_status": "VERIFIED",
        "listing_trading_day": range(1200, 1200 + len(dates)),
        "historical_st_status": "FALSE",
        "open": raw_close - 0.05,
        "high": raw_close + 0.10,
        "low": raw_close - 0.10,
        "close": raw_close,
        "pre_close": raw_close.shift(1),
        "volume": 20_000_000.0,
        "amount": 400_000_000.0,
        "trade_status": "1",
        "is_suspended": False,
        "opening_auction_fill_status": "TRUE",
        "closing_auction_fill_status": "TRUE",
        "adj_type": "none",
        "source": "fixture_raw",
    })
    if suspended_position is not None:
        raw.loc[suspended_position, "is_suspended"] = True
        raw.loc[suspended_position, "trade_status"] = "0"
    qfq = raw[["date", "code", "name", "volume", "amount"]].copy()
    for field in ("open", "high", "low", "close", "pre_close"):
        qfq[field] = raw[field] * 0.5
    qfq["adj_type"] = "qfq"
    qfq["source"] = "fixture_qfq"
    benchmark = pd.DataFrame({
        "date": dates,
        "code": "000300.SH",
        "open": 100 + pd.Series(range(len(dates))) * 0.1,
        "high": 100.2 + pd.Series(range(len(dates))) * 0.1,
        "low": 99.8 + pd.Series(range(len(dates))) * 0.1,
        "close": 100 + pd.Series(range(len(dates))) * 0.1,
        "volume": 1.0,
        "amount": 1.0,
        "adj_type": "none",
        "source": "fixture_benchmark",
    })
    raw_path = inputs / "raw.parquet"
    qfq_path = inputs / "qfq.parquet"
    benchmark_path = inputs / "benchmark.parquet"
    raw.to_parquet(raw_path, index=False)
    qfq.to_parquet(qfq_path, index=False)
    benchmark.to_parquet(benchmark_path, index=False)

    setup_path = inputs / "setup.yaml"
    setup_path.write_text(yaml.safe_dump({
        "setup_id": "STOCK_RS_PULLBACK_v1",
        "universe": {
            "exclude_new_listing_days_lt": 180,
            "min_avg_amount_20d": 300_000_000,
            "max_one_lot_value": 15_000,
            "lot_size": 100,
            "exclude_st": True,
            "exclude_suspended": True,
            "exclude_limit_up_down": True,
        },
        "strength_filter": {"lookback_days": 20, "benchmark": "000300.SH"},
        "pullback_filter": {"high_lookback_days": 10},
    }), encoding="utf-8")
    cost_path = inputs / "cost.yaml"
    cost_path.write_text("round_trip_cost: 0.002\n", encoding="utf-8")
    policy_path = inputs / "st_policy.json"
    policy_path.write_text(json.dumps({
        "daily_ratio_fallback": {"codes": []},
        "historical_st_point_overrides": [],
    }), encoding="utf-8")
    manifest = _manifest(raw_path, qfq_path, benchmark_path, setup_path, cost_path, policy_path)
    return _Fixture(root, raw_path, qfq_path, benchmark_path, setup_path, cost_path, policy_path, manifest)


def _manifest(raw: Path, qfq: Path, benchmark: Path, setup: Path, cost: Path, policy: Path) -> dict:
    return {
        "contract": {"id": "FULL_PIPELINE_RECALCULATION_V2", "timezone": "Asia/Shanghai"},
        "repository": {"commit": "b" * 40, "git_dirty": False},
        "permissions": {
            "trading_allowed": False,
            "account_simulation_allowed": False,
            "ticket_generation_allowed": False,
            "full_recalculation_allowed": False,
        },
        "strategy": {
            "source_setup": "STOCK_RS_PULLBACK_v1",
            "output_setup": "STOCK_RS_PULLBACK_v1_RECALCULATED",
            "config_sha256": sha256_file(setup),
            "rules_changed": False,
        },
        "inputs": {
            "raw_daily": _market_spec(raw, "none", "fixture_raw"),
            "qfq_daily": _market_spec(qfq, "qfq", "fixture_qfq"),
            "benchmark": _market_spec(benchmark, "none", "fixture_benchmark"),
            "st_overrides": {"path": str(policy), "sha256": sha256_file(policy)},
            "setup_config": {"path": str(setup), "sha256": sha256_file(setup)},
            "cost_config": {"path": str(cost), "sha256": sha256_file(cost)},
        },
        "policies": {
            "execution_model_version": "execution-v2",
            "price_limit_rule_version": "limit-v1",
            "st_branch_policy_version": "branch-v1",
            "close_limit_carry_forward_version": "carry-v1",
            "raw_qfq_mapping_version": "mapping-v1",
            "cost_model_version": "cost-v1",
        },
        "forbidden_inputs": list(FORBIDDEN_PIPELINE_INPUTS),
        "expected_stages": list(EXPECTED_STAGES),
    }


def _market_spec(path: Path, adj_type: str, source: str) -> dict:
    profile = profile_table(path, key_fields=("date", "code"), critical_fields=("date", "code"))
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "rows": profile["rows"],
        "min_date": profile["min_date"],
        "max_date": profile["max_date"],
        "codes": profile["code_count"],
        "adj_type": adj_type,
        "source": source,
    }
