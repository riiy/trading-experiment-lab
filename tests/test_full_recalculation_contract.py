from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from texperiment.full_recalculation.artifact_registry import build_artifact_registry
from texperiment.full_recalculation.contract import EXPECTED_STAGES, FORBIDDEN_PIPELINE_INPUTS
from texperiment.full_recalculation.decision import RunType, assert_decision_allowed
from texperiment.full_recalculation.immutability import (
    ForbiddenPipelineInputError,
    RecalculationAbort,
    assert_hashes_unchanged,
    assert_publish_target_absent,
    assert_repository_frozen,
    assert_stage_inputs_allowed,
)
from texperiment.full_recalculation.runner import FullPipelineRunner, StageExecutionError
from texperiment.full_recalculation.schema import ManifestValidationError, validate_manifest_v2
from texperiment.full_recalculation.stages import StageContext, StageId, StageResult, StageStatus


def test_manifest_v2_accepts_complete_contract():
    validate_manifest_v2(_manifest())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda manifest: manifest["contract"].pop("timezone"), "timezone"),
        (lambda manifest: manifest["inputs"]["raw_daily"].pop("rows"), "rows"),
        (lambda manifest: manifest["inputs"]["qfq_daily"].pop("min_date"), "min_date"),
        (lambda manifest: manifest["inputs"]["benchmark"].pop("max_date"), "max_date"),
        (lambda manifest: manifest.pop("permissions"), "permissions"),
    ],
)
def test_manifest_v2_rejects_missing_safety_fields(mutation, message):
    manifest = _manifest()
    mutation(manifest)

    with pytest.raises(ManifestValidationError, match=message):
        validate_manifest_v2(manifest)


def test_original_signals_and_trades_are_delta_only_inputs():
    for path in FORBIDDEN_PIPELINE_INPUTS:
        with pytest.raises(ForbiddenPipelineInputError):
            assert_stage_inputs_allowed(StageId.SIGNAL_REBUILD, [path])

    assert_stage_inputs_allowed(StageId.DELTA_AND_DECISION, FORBIDDEN_PIPELINE_INPUTS)

    with pytest.raises(ForbiddenPipelineInputError):
        assert_stage_inputs_allowed(
            StageId.TRADE_REBUILD,
            [Path("/project") / FORBIDDEN_PIPELINE_INPUTS[0]],
        )


def test_runner_executes_exact_stage_order():
    calls: list[str] = []
    stages = {
        StageId(name): _Stage(StageId(name), calls)
        for name in EXPECTED_STAGES
    }
    runner = FullPipelineRunner(stages)
    context = _context()
    context.manifest["permissions"]["full_recalculation_allowed"] = True

    results = runner.run(context)

    assert calls == list(EXPECTED_STAGES)
    assert [result.stage.value for result in results] == list(EXPECTED_STAGES)


def test_stage_failure_stops_all_downstream_stages():
    calls: list[str] = []
    stages = {
        StageId(name): _Stage(
            StageId(name),
            calls,
            fail=StageId(name) == StageId.UNIVERSE_REBUILD,
        )
        for name in EXPECTED_STAGES
    }

    context = _context()
    context.manifest["permissions"]["full_recalculation_allowed"] = True
    with pytest.raises(StageExecutionError) as caught:
        FullPipelineRunner(stages).run(context)

    assert caught.value.stage == StageId.UNIVERSE_REBUILD
    assert calls == ["INPUT_SNAPSHOT", "MARKET_STATE_REBUILD", "UNIVERSE_REBUILD"]


def test_dirty_hash_drift_and_existing_output_abort(tmp_path):
    with pytest.raises(RecalculationAbort) as dirty:
        assert_repository_frozen(current_commit="a" * 40, expected_commit="a" * 40, git_dirty=True)
    assert dirty.value.decision == "RECALCULATION_ABORTED_DIRTY_WORKTREE"

    with pytest.raises(RecalculationAbort) as drift:
        assert_hashes_unchanged({"raw": "a"}, {"raw": "b"})
    assert drift.value.decision == "RECALCULATION_ABORTED_INPUT_DRIFT"

    output = tmp_path / "published"
    output.mkdir()
    with pytest.raises(RecalculationAbort) as exists:
        assert_publish_target_absent(output)
    assert exists.value.decision == "RECALCULATION_ABORTED_OUTPUT_EXISTS"


def test_artifact_registry_separates_temporary_formal_and_failure_paths(tmp_path):
    registry = build_artifact_registry(tmp_path / "data", tmp_path / "diagnostics", "run-001")

    assert registry.temporary_root != registry.final_root
    assert ".tmp" in registry.temporary_root.parts
    assert "recalculation_attempts" in registry.failure_diagnostics_root.parts


def test_signal_replay_cannot_emit_strategy_decision():
    with pytest.raises(PermissionError, match="cannot produce"):
        assert_decision_allowed(RunType.SIGNAL_EXECUTION_REPLAY, "CONFIRMED_FAILED_ARCHIVED")


def test_full_runner_rejects_closed_recalculation_permission():
    calls: list[str] = []
    stages = {StageId(name): _Stage(StageId(name), calls) for name in EXPECTED_STAGES}

    with pytest.raises(PermissionError, match="not authorized"):
        FullPipelineRunner(stages).run(_context())

    assert calls == []


def test_manifest_requires_strategy_and_setup_hash_identity():
    manifest = _manifest()
    manifest["strategy"]["config_sha256"] = "0" * 64

    with pytest.raises(ManifestValidationError, match="setup_config"):
        validate_manifest_v2(manifest)


class _Stage:
    def __init__(self, stage_id: StageId, calls: list[str], *, fail: bool = False):
        self.stage_id = stage_id
        self.calls = calls
        self.fail = fail

    def run(self, context: StageContext) -> StageResult:
        self.calls.append(self.stage_id.value)
        result = StageResult(
            stage=self.stage_id,
            status=StageStatus.PASSED,
            started_at="2026-07-19T00:00:00+08:00",
            completed_at="2026-07-19T00:00:01+08:00",
        )
        return replace(result, status=StageStatus.FAILED, blocking_errors=("test failure",)) if self.fail else result


def _context() -> StageContext:
    return StageContext(
        run_id="run-001",
        project_root=Path("/project"),
        work_root=Path("/project/data/recalculations/.tmp/run-001"),
        manifest=_manifest(),
    )


def _manifest() -> dict:
    market_input = {
        "path": "data/input.parquet",
        "sha256": "a" * 64,
        "rows": 100,
        "min_date": "2020-01-01",
        "max_date": "2026-07-18",
        "codes": 2,
        "adj_type": "none",
        "source": "fixture",
    }
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
            "config_sha256": "e" * 64,
            "rules_changed": False,
        },
        "inputs": {
            "raw_daily": dict(market_input),
            "qfq_daily": {**market_input, "adj_type": "qfq"},
            "benchmark": {**market_input, "path": "data/index.parquet", "codes": 1},
            "st_overrides": {"path": "diagnostics/st.json", "sha256": "d" * 64},
            "setup_config": {"path": "configs/setup.yaml", "sha256": "e" * 64},
            "cost_config": {"path": "configs/cost.yaml", "sha256": "f" * 64},
        },
        "policies": {
            "execution_model_version": "execution-v2",
            "price_limit_rule_version": "limit-v1",
            "st_branch_policy_version": "st-v1",
            "close_limit_carry_forward_version": "carry-v1",
            "raw_qfq_mapping_version": "mapping-v1",
            "cost_model_version": "cost-v1",
        },
        "forbidden_inputs": list(FORBIDDEN_PIPELINE_INPUTS),
        "expected_stages": list(EXPECTED_STAGES),
    }
