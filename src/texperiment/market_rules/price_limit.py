from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import pandas as pd

from texperiment.market_rules.a_share_board import AShareBoard, get_a_share_board
from texperiment.market_rules.historical_status import HistoricalSTStatus, normalize_historical_st_status
from texperiment.market_rules.listing_phase import get_listing_phase

TRUE = "TRUE"
FALSE = "FALSE"
UNKNOWN = "UNKNOWN"
PRICE_TICK = Decimal("0.01")
PRICE_LIMIT_RULE_VERSION = "A_SHARE_PRICE_LIMIT_V1_BRANCH_INVARIANT"


@dataclass(frozen=True)
class PriceLimitResult:
    rule_status: str
    limit_up_price: float | None
    limit_down_price: float | None
    reason: str


def get_price_limit_rule(
    code: str,
    trade_date: date,
    board: str | AShareBoard | None,
    historical_st_status: Any,
    listing_date: date | None,
    previous_unadjusted_close: float | None,
    *,
    listing_trading_day: int | None = None,
) -> PriceLimitResult:
    resolved_board = _resolve_board(code, board)
    if resolved_board in {AShareBoard.UNKNOWN, AShareBoard.NON_A_SHARE}:
        return PriceLimitResult(str(resolved_board), None, None, "unsupported or unknown A-share board")

    phase = get_listing_phase(resolved_board, trade_date, listing_date, listing_trading_day)
    if phase.has_daily_limit is None:
        return PriceLimitResult(phase.status, None, None, phase.reason)
    if phase.has_daily_limit is False:
        return PriceLimitResult("KNOWN_NO_DAILY_LIMIT", None, None, phase.reason)

    st_status = normalize_historical_st_status(historical_st_status)
    if st_status == HistoricalSTStatus.UNKNOWN:
        return PriceLimitResult(
            "UNKNOWN_MISSING_HISTORICAL_ST",
            None,
            None,
            "point-in-time ST status unavailable",
        )

    previous_close = _decimal(previous_unadjusted_close)
    if previous_close is None or previous_close <= 0:
        return PriceLimitResult(
            "UNKNOWN_MISSING_RAW_PRE_CLOSE",
            None,
            None,
            "positive unadjusted previous close required",
        )

    rate = _ordinary_limit_rate(resolved_board, trade_date, st_status)
    if rate is None:
        return PriceLimitResult(
            "UNKNOWN_POLICY_INTERVAL",
            None,
            None,
            "price-limit policy unavailable for board/date/status",
        )
    up = (previous_close * (Decimal("1") + rate)).quantize(PRICE_TICK, rounding=ROUND_HALF_UP)
    down = (previous_close * (Decimal("1") - rate)).quantize(PRICE_TICK, rounding=ROUND_HALF_UP)
    if up <= previous_close:
        up = previous_close + PRICE_TICK
    if down >= previous_close and previous_close > PRICE_TICK:
        down = previous_close - PRICE_TICK
    down = max(PRICE_TICK, down)
    return PriceLimitResult("KNOWN_LIMIT", float(up), float(down), f"ordinary daily limit rate={rate}")


def evaluate_price_limit_bar(
    *,
    code: str,
    trade_date: date,
    board: str | AShareBoard | None,
    historical_st_status: Any,
    listing_date: date | None,
    previous_unadjusted_close: float | None,
    raw_open: float | None,
    raw_high: float | None,
    raw_low: float | None,
    raw_close: float | None,
    is_suspended: bool | None,
    adj_factor: float | None,
    listing_trading_day: int | None = None,
    opening_auction_fill_status: Any = None,
    closing_auction_fill_status: Any = None,
) -> dict[str, Any]:
    rule = get_price_limit_rule(
        code,
        trade_date,
        board,
        historical_st_status,
        listing_date,
        previous_unadjusted_close,
        listing_trading_day=listing_trading_day,
    )
    result: dict[str, Any] = {
        "limit_rule_status": rule.rule_status,
        "limit_up_price": rule.limit_up_price,
        "limit_down_price": rule.limit_down_price,
        "limit_rule_reason": rule.reason,
        "open_at_limit_up": UNKNOWN,
        "open_at_limit_down": UNKNOWN,
        "close_at_limit_up": UNKNOWN,
        "close_at_limit_down": UNKNOWN,
        "one_price_limit_up": UNKNOWN,
        "one_price_limit_down": UNKNOWN,
        "can_buy_at_open": UNKNOWN,
        "can_sell_at_open": UNKNOWN,
        "can_sell_intraday": UNKNOWN,
        "can_sell_at_close": UNKNOWN,
        "scheduled_close_fill_status": UNKNOWN,
        "historical_st_branch_status": (
            "NOT_EVALUATED" if normalize_historical_st_status(historical_st_status) == HistoricalSTStatus.UNKNOWN
            else "NOT_APPLICABLE_STATUS_KNOWN"
        ),
    }

    if is_suspended is None:
        result["limit_rule_reason"] = "point-in-time trade status unavailable"
        return result
    if is_suspended:
        result["can_buy_at_open"] = FALSE
        result["can_sell_at_open"] = FALSE
        result["can_sell_intraday"] = FALSE
        result["can_sell_at_close"] = FALSE
        result["scheduled_close_fill_status"] = "ASSUMED_UNFILLED_CONSERVATIVE"
        return result
    if rule.rule_status == "UNKNOWN_MISSING_HISTORICAL_ST":
        return _evaluate_unknown_st_execution(
            result,
            code=code,
            trade_date=trade_date,
            board=board,
            listing_date=listing_date,
            previous_unadjusted_close=previous_unadjusted_close,
            raw_open=raw_open,
            raw_high=raw_high,
            raw_low=raw_low,
            raw_close=raw_close,
            adj_factor=adj_factor,
            listing_trading_day=listing_trading_day,
            opening_auction_fill_status=opening_auction_fill_status,
            closing_auction_fill_status=closing_auction_fill_status,
        )
    if rule.rule_status.startswith("UNKNOWN"):
        return result

    prices = [_decimal(value) for value in (raw_open, raw_high, raw_low, raw_close)]
    if any(value is None or value <= 0 for value in prices):
        result["limit_rule_reason"] = f"{rule.reason}; positive unadjusted OHLC required for bar evaluation"
        return result

    open_price, high_price, low_price, close_price = prices
    if rule.rule_status == "KNOWN_NO_DAILY_LIMIT":
        for key in (
            "open_at_limit_up",
            "open_at_limit_down",
            "close_at_limit_up",
            "close_at_limit_down",
            "one_price_limit_up",
            "one_price_limit_down",
        ):
            result[key] = FALSE
        factor = _decimal(adj_factor)
        if factor is not None and factor > 0:
            result["can_buy_at_open"] = TRUE
            result["can_sell_at_open"] = TRUE
            result["can_sell_intraday"] = TRUE
            result["can_sell_at_close"] = TRUE
            result["scheduled_close_fill_status"] = "FILLED_AT_CLOSE"
        else:
            result["limit_rule_reason"] = f"{rule.reason}; adjustment factor unavailable for execution validation"
        return result

    up = _decimal(rule.limit_up_price)
    down = _decimal(rule.limit_down_price)
    assert up is not None and down is not None
    flags = {
        "open_at_limit_up": open_price == up,
        "open_at_limit_down": open_price == down,
        "close_at_limit_up": close_price == up,
        "close_at_limit_down": close_price == down,
        "one_price_limit_up": open_price == high_price == low_price == close_price == up,
        "one_price_limit_down": open_price == high_price == low_price == close_price == down,
    }
    result.update({key: TRUE if value else FALSE for key, value in flags.items()})
    factor = _decimal(adj_factor)
    if factor is None or factor <= 0:
        result["limit_rule_reason"] = f"{rule.reason}; adjustment factor unavailable for execution validation"
        return result
    auction_fill = _optional_bool(opening_auction_fill_status)
    if flags["one_price_limit_up"]:
        result["can_buy_at_open"] = FALSE
    elif flags["open_at_limit_up"]:
        result["can_buy_at_open"] = UNKNOWN if auction_fill is None else (TRUE if auction_fill else FALSE)
    else:
        result["can_buy_at_open"] = TRUE
    result["can_sell_at_open"] = FALSE if flags["one_price_limit_down"] else TRUE
    result["can_sell_intraday"] = FALSE if flags["one_price_limit_down"] else TRUE
    if flags["one_price_limit_down"]:
        result["can_sell_at_close"] = FALSE
        result["scheduled_close_fill_status"] = "ASSUMED_UNFILLED_CONSERVATIVE"
    elif flags["close_at_limit_down"]:
        result["can_sell_at_close"] = FALSE
        result["scheduled_close_fill_status"] = "ASSUMED_UNFILLED_CONSERVATIVE"
    else:
        result["can_sell_at_close"] = TRUE
        result["scheduled_close_fill_status"] = "FILLED_AT_CLOSE"
    return result


def _evaluate_unknown_st_execution(
    result: dict[str, Any],
    *,
    code: str,
    trade_date: date,
    board: str | AShareBoard | None,
    listing_date: date | None,
    previous_unadjusted_close: float | None,
    raw_open: float | None,
    raw_high: float | None,
    raw_low: float | None,
    raw_close: float | None,
    adj_factor: float | None,
    listing_trading_day: int | None,
    opening_auction_fill_status: Any,
    closing_auction_fill_status: Any,
) -> dict[str, Any]:
    branch_results = [
        evaluate_price_limit_bar(
            code=code,
            trade_date=trade_date,
            board=board,
            historical_st_status=st_status,
            listing_date=listing_date,
            previous_unadjusted_close=previous_unadjusted_close,
            raw_open=raw_open,
            raw_high=raw_high,
            raw_low=raw_low,
            raw_close=raw_close,
            is_suspended=False,
            adj_factor=adj_factor,
            listing_trading_day=listing_trading_day,
            opening_auction_fill_status=opening_auction_fill_status,
            closing_auction_fill_status=closing_auction_fill_status,
        )
        for st_status in (HistoricalSTStatus.FALSE, HistoricalSTStatus.TRUE)
    ]
    if any(branch["limit_rule_status"] not in {"KNOWN_LIMIT", "KNOWN_NO_DAILY_LIMIT"} for branch in branch_results):
        return result
    prices = [_decimal(value) for value in (raw_open, raw_high, raw_low, raw_close)]
    factor = _decimal(adj_factor)
    if any(value is None or value <= 0 for value in prices) or factor is None or factor <= 0:
        return result
    open_price, high_price, low_price, close_price = prices
    up_prices = {_decimal(branch["limit_up_price"]) for branch in branch_results if branch["limit_up_price"] is not None}
    down_prices = {_decimal(branch["limit_down_price"]) for branch in branch_results if branch["limit_down_price"] is not None}

    result["open_at_limit_up"] = _candidate_match(open_price, up_prices)
    result["open_at_limit_down"] = _candidate_match(open_price, down_prices)
    result["close_at_limit_up"] = _candidate_match(close_price, up_prices)
    result["close_at_limit_down"] = _candidate_match(close_price, down_prices)
    non_flat = len({open_price, high_price, low_price, close_price}) > 1
    result["one_price_limit_up"] = FALSE if non_flat else _candidate_match(open_price, up_prices)
    result["one_price_limit_down"] = FALSE if non_flat else _candidate_match(open_price, down_prices)

    execution_fields = (
        "can_buy_at_open",
        "can_sell_at_open",
        "can_sell_intraday",
        "can_sell_at_close",
        "scheduled_close_fill_status",
    )
    invariant = True
    for field in execution_fields:
        values = {branch[field] for branch in branch_results}
        if len(values) == 1:
            result[field] = values.pop()
        else:
            result[field] = UNKNOWN
            invariant = False
    result["historical_st_branch_status"] = "PASS_BRANCH_INVARIANT" if invariant else "NOT_EVALUABLE_MISSING_HISTORICAL_ST"
    result["limit_rule_reason"] += "; execution state resolved only where both ST rule candidates agree"
    return result


def _candidate_match(value: Decimal, candidates: set[Decimal | None]) -> str:
    candidates.discard(None)
    matches = [value == candidate for candidate in candidates]
    if not any(matches):
        return FALSE
    return TRUE if len(candidates) == 1 else UNKNOWN




def enrich_price_limit_fields(daily_bars: pd.DataFrame) -> pd.DataFrame:
    """Evaluate rule and execution fields without inventing missing metadata."""
    required = {
        "date",
        "code",
        "historical_st_status",
        "listing_date",
        "raw_pre_close",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "is_suspended",
        "adj_factor",
    }
    missing = sorted(required - set(daily_bars.columns))
    if missing:
        raise ValueError(f"daily bars missing execution-rule columns: {missing}")
    out = daily_bars.copy()
    evaluations = []
    for row in out.to_dict("records"):
        listing_date = _date_or_none(row.get("listing_date"))
        evaluations.append(evaluate_price_limit_bar(
            code=str(row["code"]),
            trade_date=pd.Timestamp(row["date"]).date(),
            board=row.get("board"),
            historical_st_status=row.get("historical_st_status"),
            listing_date=listing_date,
            previous_unadjusted_close=row.get("raw_pre_close"),
            raw_open=row.get("raw_open"),
            raw_high=row.get("raw_high"),
            raw_low=row.get("raw_low"),
            raw_close=row.get("raw_close"),
            is_suspended=_optional_bool(row.get("is_suspended")),
            adj_factor=row.get("adj_factor"),
            listing_trading_day=_positive_int_or_none(row.get("listing_trading_day")),
            opening_auction_fill_status=row.get("opening_auction_fill_status"),
            closing_auction_fill_status=row.get("closing_auction_fill_status"),
        ))
    evaluated = pd.DataFrame(evaluations, index=out.index)
    for column in evaluated:
        out[column] = evaluated[column]
    return out


def _ordinary_limit_rate(
    board: AShareBoard,
    trade_date: date,
    st_status: HistoricalSTStatus,
) -> Decimal | None:
    if board in {AShareBoard.MAIN_SH, AShareBoard.MAIN_SZ}:
        return Decimal("0.05") if st_status == HistoricalSTStatus.TRUE else Decimal("0.10")
    if board == AShareBoard.CHINEXT:
        if trade_date >= date(2020, 8, 24):
            return Decimal("0.20")
        return Decimal("0.05") if st_status == HistoricalSTStatus.TRUE else Decimal("0.10")
    if board == AShareBoard.STAR and trade_date >= date(2019, 7, 22):
        return Decimal("0.20")
    if board == AShareBoard.BEIJING and trade_date >= date(2021, 11, 15):
        return Decimal("0.30")
    return None


def _resolve_board(code: str, board: str | AShareBoard | None) -> AShareBoard:
    if isinstance(board, AShareBoard):
        return board
    if board:
        try:
            return AShareBoard(str(board))
        except ValueError:
            return AShareBoard.UNKNOWN
    return get_a_share_board(code)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _date_or_none(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def _positive_int_or_none(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    result = int(value)
    return result if result > 0 else None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().upper()
    if normalized in {"TRUE", "1"}:
        return True
    if normalized in {"FALSE", "0"}:
        return False
    return None
