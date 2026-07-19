from __future__ import annotations

from enum import StrEnum

from texperiment.full_recalculation.contract import STRATEGY_DECISIONS


class RunType(StrEnum):
    FULL_PIPELINE_RECALCULATION = "FULL_PIPELINE_RECALCULATION"
    SIGNAL_EXECUTION_REPLAY = "SIGNAL_EXECUTION_REPLAY"


def assert_decision_allowed(run_type: RunType, decision: str) -> None:
    if run_type == RunType.SIGNAL_EXECUTION_REPLAY and decision in STRATEGY_DECISIONS:
        raise PermissionError("signal execution replay cannot produce a strategy validation decision")
