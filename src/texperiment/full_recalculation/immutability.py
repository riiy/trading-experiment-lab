from __future__ import annotations

from pathlib import Path
from typing import Iterable

from texperiment.full_recalculation.contract import FORBIDDEN_PIPELINE_INPUTS
from texperiment.full_recalculation.stages import StageId


class ForbiddenPipelineInputError(ValueError):
    pass


class RecalculationAbort(RuntimeError):
    def __init__(self, decision: str, message: str):
        super().__init__(message)
        self.decision = decision


def assert_stage_inputs_allowed(stage: StageId, paths: Iterable[str | Path]) -> None:
    """Allow original research artifacts only in the final Delta stage."""
    if stage == StageId.DELTA_AND_DECISION:
        return
    rejected = sorted(
        Path(path).as_posix()
        for path in paths
        if any(_path_has_suffix(Path(path), Path(forbidden)) for forbidden in FORBIDDEN_PIPELINE_INPUTS)
    )
    if rejected:
        raise ForbiddenPipelineInputError(
            f"{stage.value} cannot read original comparison artifacts: {rejected}"
        )


def _path_has_suffix(path: Path, suffix: Path) -> bool:
    return len(path.parts) >= len(suffix.parts) and path.parts[-len(suffix.parts):] == suffix.parts


def assert_repository_frozen(*, current_commit: str, expected_commit: str, git_dirty: bool) -> None:
    if git_dirty:
        raise RecalculationAbort(
            "RECALCULATION_ABORTED_DIRTY_WORKTREE",
            "Git worktree must be clean",
        )
    if current_commit != expected_commit:
        raise RecalculationAbort(
            "RECALCULATION_ABORTED_INPUT_DRIFT",
            "Git commit changed after Manifest freeze",
        )


def assert_hashes_unchanged(expected: dict[str, str], actual: dict[str, str]) -> None:
    if expected != actual:
        raise RecalculationAbort(
            "RECALCULATION_ABORTED_INPUT_DRIFT",
            "frozen input hashes changed",
        )


def assert_publish_target_absent(path: str | Path) -> None:
    if Path(path).exists():
        raise RecalculationAbort(
            "RECALCULATION_ABORTED_OUTPUT_EXISTS",
            f"formal output already exists: {path}",
        )
