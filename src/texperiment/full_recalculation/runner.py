from __future__ import annotations

import json
import os
from collections.abc import Mapping

from texperiment.full_recalculation.contract import EXPECTED_STAGES
from texperiment.full_recalculation.schema import validate_manifest_v2
from texperiment.full_recalculation.stages import (
    RecalculationStage,
    StageContext,
    StageId,
    StageResult,
    StageStatus,
)


class StageExecutionError(RuntimeError):
    def __init__(self, stage: StageId, results: tuple[StageResult, ...], message: str):
        super().__init__(message)
        self.stage = stage
        self.results = results


class FullPipelineRunner:
    """Order-only V2 orchestrator. Domain work remains in injected stage implementations."""

    def __init__(self, stages: Mapping[StageId, RecalculationStage]):
        expected = tuple(StageId(name) for name in EXPECTED_STAGES)
        if tuple(stages) != expected:
            raise ValueError("stage implementations must match the exact V2 order")
        for stage_id, stage in stages.items():
            if stage.stage_id != stage_id:
                raise ValueError(f"stage implementation ID mismatch: {stage_id.value}")
        self._stages = dict(stages)

    def run(self, context: StageContext) -> tuple[StageResult, ...]:
        if context.manifest.get("permissions", {}).get("full_recalculation_allowed") is not True:
            raise PermissionError("full pipeline execution is not authorized")
        return self.run_until(context, StageId.DELTA_AND_DECISION)

    def run_until(self, context: StageContext, final_stage: StageId) -> tuple[StageResult, ...]:
        validate_manifest_v2(context.manifest)
        results: list[StageResult] = []
        for stage_id, stage in self._stages.items():
            try:
                result = stage.run(context)
            except Exception as exc:
                error = StageExecutionError(
                    stage_id,
                    tuple(results),
                    f"stage failed before completion: {stage_id.value}",
                )
                _quarantine_failure(context, error)
                raise error from exc
            if result.stage != stage_id:
                error = StageExecutionError(stage_id, tuple(results), "stage returned the wrong ID")
                _quarantine_failure(context, error)
                raise error
            results.append(result)
            if result.status != StageStatus.PASSED or result.blocking_errors:
                error = StageExecutionError(stage_id, tuple(results), f"stage failed: {stage_id.value}")
                _quarantine_failure(context, error)
                raise error
            if stage_id == final_stage:
                return tuple(results)
        raise ValueError(f"final stage is not registered: {final_stage.value}")


def _quarantine_failure(context: StageContext, error: StageExecutionError) -> None:
    if context.failure_root is None:
        return
    if context.failure_root.exists():
        raise FileExistsError(f"failure diagnostics already exist: {context.failure_root}")
    context.failure_root.parent.mkdir(parents=True, exist_ok=True)
    if context.work_root.exists():
        os.replace(context.work_root, context.failure_root)
    else:
        context.failure_root.mkdir()
    (context.failure_root / "failure.json").write_text(
        json.dumps(
            {
                "decision": "RECALCULATION_ABORTED_STAGE_FAILURE",
                "failed_stage": error.stage.value,
                "completed_stages": [result.stage.value for result in error.results],
                "strategy_decision_generated": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
