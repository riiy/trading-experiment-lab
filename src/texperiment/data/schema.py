from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Canonical internal schema for A-share daily bars.
# Generic OHLC preserves provider compatibility. New research must use explicit adjusted
# fields for signals and explicit raw fields for execution.
CANONICAL_DAILY_COLUMNS: Final[list[str]] = [
    "date",
    "code",
    "raw_code",
    "name",
    "market",
    "board",
    "listing_date",
    "listing_date_status",
    "listing_trading_day",
    "historical_st_status",
    "historical_st_branch_status",
    "opening_auction_fill_status",
    "closing_auction_fill_status",
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
    "adj_offset",
    "adjustment_status",
    "adjustment_fit_error",
    "hfq_fit_error",
    "volume_layer_match",
    "amount_layer_match",
    "raw_open",
    "raw_high",
    "raw_low",
    "raw_close",
    "raw_pre_close",
    "adj_open",
    "adj_high",
    "adj_low",
    "adj_close",
    "hfq_open",
    "hfq_high",
    "hfq_low",
    "hfq_close",
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
    "can_sell_intraday",
    "can_sell_at_close",
    "scheduled_close_fill_status",
    "limit_rule_status",
    "limit_rule_reason",
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
    "adj_offset",
    "adjustment_fit_error",
    "hfq_fit_error",
    "raw_open",
    "raw_high",
    "raw_low",
    "raw_close",
    "raw_pre_close",
    "adj_open",
    "adj_high",
    "adj_low",
    "adj_close",
    "hfq_open",
    "hfq_high",
    "hfq_low",
    "hfq_close",
    "limit_up_price",
    "limit_down_price",
    "listing_days",
    "listing_trading_day",
]

BOOLEAN_COLUMNS: Final[list[str]] = [
    "is_suspended",
    "is_limit_up",
    "is_limit_down",
    "is_st",
    "volume_layer_match",
    "amount_layer_match",
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
