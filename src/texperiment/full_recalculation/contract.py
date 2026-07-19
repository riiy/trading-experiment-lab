from __future__ import annotations

from typing import Final

CONTRACT_ID: Final = "FULL_PIPELINE_RECALCULATION_V2"
TIMEZONE: Final = "Asia/Shanghai"
SOURCE_SETUP_ID: Final = "STOCK_RS_PULLBACK_v1"
OUTPUT_SETUP_ID: Final = "STOCK_RS_PULLBACK_v1_RECALCULATED"

EXPECTED_STAGES: Final[tuple[str, ...]] = (
    "INPUT_SNAPSHOT",
    "MARKET_STATE_REBUILD",
    "UNIVERSE_REBUILD",
    "INDICATOR_REBUILD",
    "SIGNAL_REBUILD",
    "TRADE_REBUILD",
    "METRICS_REBUILD",
    "DELTA_AND_DECISION",
)

FORBIDDEN_PIPELINE_INPUTS: Final[tuple[str, ...]] = (
    "data/signals/STOCK_RS_PULLBACK_v1_signals.csv",
    "data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv",
    "data/reports/STOCK_RS_PULLBACK_v1_metrics.json",
)

REQUIRED_MARKET_INPUTS: Final[tuple[str, ...]] = ("raw_daily", "qfq_daily", "benchmark")
REQUIRED_AUXILIARY_INPUTS: Final[tuple[str, ...]] = (
    "st_overrides",
    "setup_config",
    "cost_config",
)

REQUIRED_POLICY_FIELDS: Final[tuple[str, ...]] = (
    "execution_model_version",
    "price_limit_rule_version",
    "st_branch_policy_version",
    "close_limit_carry_forward_version",
    "raw_qfq_mapping_version",
    "cost_model_version",
)

ABORT_DECISIONS: Final[frozenset[str]] = frozenset(
    {
        "RECALCULATION_ABORTED_PIPELINE_CONTRACT_MISMATCH",
        "RECALCULATION_ABORTED_INPUT_DRIFT",
        "RECALCULATION_ABORTED_DIRTY_WORKTREE",
        "RECALCULATION_ABORTED_OUTPUT_EXISTS",
        "RECALCULATION_ABORTED_STAGE_FAILURE",
        "RECALCULATION_ABORTED_COMPARISON_INPUT_MISSING",
        "RECALCULATION_ABORTED_COMPARISON_INPUT_DRIFT",
        "RECALCULATION_ABORTED_ARCHIVE_MANIFEST_MISMATCH",
        "RECALCULATION_INCONCLUSIVE_DATA_LIMITATION",
    }
)

STRATEGY_DECISIONS: Final[frozenset[str]] = frozenset(
    {
        "CONFIRMED_FAILED_ARCHIVED",
        "EDGE_NOT_TRADABLE",
        "VALIDATION_PASSED_NEEDS_ACCOUNT_SIMULATION",
    }
)
