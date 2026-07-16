from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Canonical internal schema for A-share daily bars.
# Price fields are expected to be numeric and comparable inside the same adjustment type.
# For STOCK_RS_PULLBACK_v1, use qfq/前复权 data for historical signal research.
CANONICAL_DAILY_COLUMNS: Final[list[str]] = [
    "date",
    "code",
    "raw_code",
    "name",
    "market",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "turnover_rate",
    "pct_chg",
    "adj_type",
    "adj_factor",
    "trade_status",
    "is_suspended",
    "is_limit_up",
    "is_limit_down",
    "is_st",
    "listing_days",
    "industry",
    "source",
    "source_file",
]

REQUIRED_DAILY_COLUMNS: Final[set[str]] = {
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
}

NUMERIC_COLUMNS: Final[list[str]] = [
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "turnover_rate",
    "pct_chg",
    "adj_factor",
    "listing_days",
]

BOOLEAN_COLUMNS: Final[list[str]] = [
    "is_suspended",
    "is_limit_up",
    "is_limit_down",
    "is_st",
]

SUPPORTED_PROVIDERS: Final[set[str]] = {"auto", "canonical", "akshare", "tushare", "baostock"}


@dataclass(frozen=True)
class StandardizationReport:
    provider: str
    rows_in: int
    rows_out: int
    min_date: str | None
    max_date: str | None
    code_count: int
    duplicate_count: int
