from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from texperiment.market_rules.a_share_board import get_a_share_board


ST_IGNORED_EXECUTION_POLICY = "IGNORE_HISTORICAL_ST_ORDINARY_LIMITS_V1"
EXECUTION_COLUMNS = (
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
)


def rebuild_execution_without_historical_st(daily_bars: pd.DataFrame) -> pd.DataFrame:
    """Build ordinary A-share execution fields without consulting ST data.

    This is a counterfactual research policy for VCB only. It retains board,
    listing-phase, suspension, and ordinary price-limit handling while applying
    the non-ST limit schedule to every security.
    """
    required = {"date", "code", "raw_open", "raw_high", "raw_low", "raw_close", "adj_factor", "is_suspended"}
    missing = sorted(required - set(daily_bars.columns))
    if missing:
        raise ValueError(f"VCB execution input missing columns: {missing}")

    out = daily_bars.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out["code"] = out["code"].astype(str)
    out = out.sort_values(["code", "date"]).reset_index(drop=True)
    previous_close = (
        pd.to_numeric(out["raw_pre_close"], errors="coerce")
        if "raw_pre_close" in out
        else pd.Series(float("nan"), index=out.index, dtype="float64")
    )
    inferred_previous_close = out.groupby("code", sort=False)["raw_close"].shift(1)
    out["_previous_raw_close"] = previous_close.where(previous_close.notna(), inferred_previous_close)
    if "listing_date" not in out:
        out["listing_date"] = out.groupby("code", sort=False)["date"].transform("min")

    return _apply_ordinary_limit_execution(out).drop(columns="_previous_raw_close")


def _apply_ordinary_limit_execution(out: pd.DataFrame) -> pd.DataFrame:
    """Vectorized non-ST branch of the A-share price-limit contract.

    The VCB universe has over ten million rows, so this implements the exact
    ordinary-limit schedule in vector form rather than per-row dispatch. Rows
    without enough non-ST information remain UNKNOWN and consequently fail
    closed at signal or execution time.
    """
    result = out.copy()
    date = result["date"]
    board = result.get("board", pd.Series("", index=result.index)).astype(str)
    missing_board = board.str.strip().eq("") | board.eq("UNKNOWN_BOARD")
    if missing_board.any():
        board = board.where(~missing_board, result.loc[missing_board, "code"].map(lambda code: str(get_a_share_board(code))))
    listing_date = pd.to_datetime(result["listing_date"], errors="coerce").dt.normalize()
    listing_day = (
        pd.to_numeric(result["listing_trading_day"], errors="coerce")
        if "listing_trading_day" in result
        else pd.Series(float("nan"), index=result.index, dtype="float64")
    )
    raw = {field: pd.to_numeric(result[f"raw_{field}"], errors="coerce") for field in ("open", "high", "low", "close")}
    previous_close = pd.to_numeric(result["_previous_raw_close"], errors="coerce")
    adj_factor = pd.to_numeric(result["adj_factor"], errors="coerce")
    suspended = _bool_series(result["is_suspended"])
    valid_prices = previous_close.gt(0) & adj_factor.gt(0)
    for value in raw.values():
        valid_prices &= value.gt(0)

    no_limit_days = pd.Series(0, index=result.index, dtype="int64")
    no_limit_days.loc[board.eq("STAR") & listing_date.ge(pd.Timestamp("2019-07-22"))] = 5
    no_limit_days.loc[board.eq("CHINEXT") & listing_date.ge(pd.Timestamp("2020-08-24"))] = 5
    no_limit_days.loc[board.isin(["MAIN_SH", "MAIN_SZ"]) & listing_date.ge(pd.Timestamp("2023-04-10"))] = 5
    no_limit_days.loc[board.eq("BEIJING") & listing_date.ge(pd.Timestamp("2021-11-15"))] = 1
    listing_phase_known = listing_date.notna() & ((listing_day.notna() & listing_day.gt(0)) | date.eq(listing_date))
    effective_listing_day = listing_day.where(listing_day.notna(), np.where(date.eq(listing_date), 1, np.nan))
    known_no_limit = listing_phase_known & no_limit_days.gt(0) & effective_listing_day.le(no_limit_days)
    legacy_first_day_unknown = listing_phase_known & no_limit_days.eq(0) & effective_listing_day.eq(1)

    rate = pd.Series(np.nan, index=result.index, dtype="float64")
    rate.loc[board.isin(["MAIN_SH", "MAIN_SZ"])] = 0.10
    rate.loc[board.eq("CHINEXT") & date.lt(pd.Timestamp("2020-08-24"))] = 0.10
    rate.loc[board.eq("CHINEXT") & date.ge(pd.Timestamp("2020-08-24"))] = 0.20
    rate.loc[board.eq("STAR") & date.ge(pd.Timestamp("2019-07-22"))] = 0.20
    rate.loc[board.eq("BEIJING") & date.ge(pd.Timestamp("2021-11-15"))] = 0.30
    known_limit = rate.notna() & ~known_no_limit & ~legacy_first_day_unknown & valid_prices
    limit_up = (np.floor(previous_close * (1.0 + rate) * 100.0 + 0.5) / 100.0).where(known_limit)
    limit_down = (np.floor(previous_close * (1.0 - rate) * 100.0 + 0.5) / 100.0).clip(lower=0.01).where(known_limit)

    for column in EXECUTION_COLUMNS:
        result[column] = "UNKNOWN"
    result["limit_up_price"] = limit_up
    result["limit_down_price"] = limit_down
    result.loc[known_limit, "limit_rule_status"] = "KNOWN_LIMIT"
    result.loc[known_limit, "limit_rule_reason"] = "ordinary non-ST daily limit"
    result.loc[known_no_limit & valid_prices, "limit_rule_status"] = "KNOWN_NO_DAILY_LIMIT"
    result.loc[known_no_limit & valid_prices, "limit_rule_reason"] = "registration-based listing no-limit phase"

    open_up = known_limit & _same_price(raw["open"], limit_up)
    open_down = known_limit & _same_price(raw["open"], limit_down)
    close_up = known_limit & _same_price(raw["close"], limit_up)
    close_down = known_limit & _same_price(raw["close"], limit_down)
    one_up = open_up & _same_price(raw["high"], limit_up) & _same_price(raw["low"], limit_up) & close_up
    one_down = open_down & _same_price(raw["high"], limit_down) & _same_price(raw["low"], limit_down) & close_down
    known_execution = (known_limit | known_no_limit) & valid_prices & ~suspended
    for name, mask in {
        "open_at_limit_up": open_up,
        "open_at_limit_down": open_down,
        "close_at_limit_up": close_up,
        "close_at_limit_down": close_down,
        "one_price_limit_up": one_up,
        "one_price_limit_down": one_down,
    }.items():
        result.loc[known_execution, name] = "FALSE"
        result.loc[mask, name] = "TRUE"
    result.loc[known_execution, "can_buy_at_open"] = "TRUE"
    result.loc[open_up, "can_buy_at_open"] = "UNKNOWN"
    result.loc[one_up, "can_buy_at_open"] = "FALSE"
    result.loc[known_execution, "can_sell_at_open"] = "TRUE"
    result.loc[known_execution, "can_sell_intraday"] = "TRUE"
    result.loc[one_down, ["can_sell_at_open", "can_sell_intraday"]] = "FALSE"
    result.loc[known_execution, "can_sell_at_close"] = "TRUE"
    result.loc[close_down, "can_sell_at_close"] = "FALSE"
    result.loc[known_execution, "scheduled_close_fill_status"] = "FILLED_AT_CLOSE"
    result.loc[close_down, "scheduled_close_fill_status"] = "ASSUMED_UNFILLED_CONSERVATIVE"

    result.loc[suspended, ["can_buy_at_open", "can_sell_at_open", "can_sell_intraday", "can_sell_at_close"]] = "FALSE"
    result.loc[suspended, "scheduled_close_fill_status"] = "ASSUMED_UNFILLED_CONSERVATIVE"
    return result


def _same_price(left: pd.Series, right: pd.Series) -> pd.Series:
    return left.notna() & right.notna() & left.sub(right).abs().le(1e-9)


def _bool_series(values: pd.Series) -> pd.Series:
    if values.dtype == bool:
        return values.fillna(False)
    return values.astype(str).str.strip().str.lower().isin({"1", "true", "t", "yes", "y", "是"})
