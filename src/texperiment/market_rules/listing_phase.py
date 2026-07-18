from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from texperiment.market_rules.a_share_board import AShareBoard


@dataclass(frozen=True)
class ListingPhaseResult:
    status: str
    has_daily_limit: bool | None
    reason: str


def get_listing_phase(
    board: AShareBoard,
    trade_date: date,
    listing_date: date | None,
    listing_trading_day: int | None = None,
) -> ListingPhaseResult:
    if listing_date is None:
        return ListingPhaseResult("UNKNOWN_LISTING_DATE", None, "listing date unavailable")
    if trade_date < listing_date:
        return ListingPhaseResult("UNKNOWN_LISTING_PHASE", None, "trade date precedes listing date")
    if listing_trading_day is not None and listing_trading_day <= 0:
        return ListingPhaseResult("UNKNOWN_LISTING_PHASE", None, "listing trading day must be positive")

    no_limit_days = _registration_no_limit_days(board, listing_date)
    if no_limit_days is not None:
        if listing_trading_day is None:
            if trade_date == listing_date:
                listing_trading_day = 1
            else:
                return ListingPhaseResult(
                    "UNKNOWN_LISTING_PHASE",
                    None,
                    "listing trading-day ordinal required during registration-based listing phase",
                )
        if listing_trading_day <= no_limit_days:
            return ListingPhaseResult(
                "KNOWN_NO_DAILY_LIMIT",
                False,
                f"registration-based listing day {listing_trading_day}",
            )
        return ListingPhaseResult("KNOWN_ORDINARY_PHASE", True, "post-listing no-limit phase")

    if trade_date == listing_date or listing_trading_day == 1:
        return ListingPhaseResult(
            "UNKNOWN_SPECIAL_LISTING_RULE",
            None,
            "legacy first-day limit requires dated security-level policy",
        )
    return ListingPhaseResult("KNOWN_ORDINARY_PHASE", True, "ordinary post-listing phase")


def _registration_no_limit_days(board: AShareBoard, listing_date: date) -> int | None:
    if board == AShareBoard.STAR and listing_date >= date(2019, 7, 22):
        return 5
    if board == AShareBoard.CHINEXT and listing_date >= date(2020, 8, 24):
        return 5
    if board in {AShareBoard.MAIN_SH, AShareBoard.MAIN_SZ} and listing_date >= date(2023, 4, 10):
        return 5
    if board == AShareBoard.BEIJING and listing_date >= date(2021, 11, 15):
        return 1
    return None
