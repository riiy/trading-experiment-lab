from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Protocol


class StageId(StrEnum):
    INPUT_SNAPSHOT = "INPUT_SNAPSHOT"
    MARKET_STATE_REBUILD = "MARKET_STATE_REBUILD"
    UNIVERSE_REBUILD = "UNIVERSE_REBUILD"
    INDICATOR_REBUILD = "INDICATOR_REBUILD"
    SIGNAL_REBUILD = "SIGNAL_REBUILD"
    TRADE_REBUILD = "TRADE_REBUILD"
    METRICS_REBUILD = "METRICS_REBUILD"
    DELTA_AND_DECISION = "DELTA_AND_DECISION"


class StageStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


@dataclass
class StageContext:
    run_id: str
    project_root: Path
    work_root: Path
    manifest: Mapping[str, Any]
    artifacts: MutableMapping[str, Path] = field(default_factory=dict)


@dataclass(frozen=True)
class StageResult:
    stage: StageId
    status: StageStatus
    started_at: str
    completed_at: str
    input_hashes: Mapping[str, str] = field(default_factory=dict)
    output_hashes: Mapping[str, str] = field(default_factory=dict)
    rows: int = 0
    min_date: str | None = None
    max_date: str | None = None
    unique_codes: int = 0
    warnings: tuple[str, ...] = ()
    blocking_errors: tuple[str, ...] = ()


class RecalculationStage(Protocol):
    stage_id: StageId

    def run(self, context: StageContext) -> StageResult:
        ...
