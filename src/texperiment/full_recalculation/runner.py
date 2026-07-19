from __future__ import annotations

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
        validate_manifest_v2(context.manifest)
        results: list[StageResult] = []
        for stage_id, stage in self._stages.items():
            try:
                result = stage.run(context)
            except Exception as exc:
                raise StageExecutionError(
                    stage_id,
                    tuple(results),
                    f"stage failed before completion: {stage_id.value}",
                ) from exc
            if result.stage != stage_id:
                raise StageExecutionError(stage_id, tuple(results), "stage returned the wrong ID")
            results.append(result)
            if result.status != StageStatus.PASSED or result.blocking_errors:
                raise StageExecutionError(stage_id, tuple(results), f"stage failed: {stage_id.value}")
        return tuple(results)
