from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from texperiment.market_rules.a_share_board import AShareBoard, get_a_share_board
from texperiment.market_rules.price_limit import enrich_price_limit_fields, evaluate_price_limit_bar, get_price_limit_rule


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("600000.SH", AShareBoard.MAIN_SH),
        ("000001.SZ", AShareBoard.MAIN_SZ),
        ("300039.SZ", AShareBoard.CHINEXT),
        ("688001.SH", AShareBoard.STAR),
        ("833000.BJ", AShareBoard.BEIJING),
        ("900901.SH", AShareBoard.NON_A_SHARE),
    ],
)
def test_board_classification(code, expected):
    assert get_a_share_board(code) == expected


@pytest.mark.parametrize(
    ("code", "trade_date", "st", "expected_up"),
    [
        ("600000.SH", date(2022, 1, 1), "FALSE", 11.0),
        ("600000.SH", date(2022, 1, 1), "TRUE", 10.5),
        ("300001.SZ", date(2020, 8, 23), "FALSE", 11.0),
        ("300001.SZ", date(2020, 8, 24), "FALSE", 12.0),
        ("688001.SH", date(2022, 1, 1), "FALSE", 12.0),
        ("833000.BJ", date(2022, 1, 1), "FALSE", 13.0),
    ],
)
def test_board_date_and_st_limit_rates(code, trade_date, st, expected_up):
    result = get_price_limit_rule(
        code,
        trade_date,
        None,
        st,
        date(2010, 1, 1),
        10.0,
        listing_trading_day=100,
    )
    assert result.rule_status == "KNOWN_LIMIT"
    assert result.limit_up_price == expected_up


def test_unknown_historical_st_is_blocking():
    result = get_price_limit_rule(
        "600000.SH",
        date(2022, 1, 1),
        None,
        "UNKNOWN",
        date(1999, 1, 1),
        10.0,
        listing_trading_day=100,
    )
    assert result.rule_status == "UNKNOWN_MISSING_HISTORICAL_ST"
    assert result.limit_up_price is None


def test_unknown_st_can_still_resolve_normal_open_execution():
    result = evaluate_price_limit_bar(
        code="600000.SH",
        trade_date=date(2022, 1, 4),
        board=None,
        historical_st_status="UNKNOWN",
        listing_date=date(2000, 1, 1),
        previous_unadjusted_close=10.0,
        raw_open=9.9,
        raw_high=11.0,
        raw_low=9.8,
        raw_close=11.0,
        is_suspended=False,
        adj_factor=1.0,
        listing_trading_day=100,
    )
    assert result["limit_rule_status"] == "UNKNOWN_MISSING_HISTORICAL_ST"
    assert result["open_at_limit_up"] == "FALSE"
    assert result["close_at_limit_up"] == "UNKNOWN"
    assert result["can_buy_at_open"] == "TRUE"
    assert result["historical_st_branch_status"] == "PASS_BRANCH_INVARIANT"


def test_unknown_st_keeps_candidate_limit_open_ambiguous():
    result = evaluate_price_limit_bar(
        code="600000.SH",
        trade_date=date(2022, 1, 4),
        board=None,
        historical_st_status="UNKNOWN",
        listing_date=date(2000, 1, 1),
        previous_unadjusted_close=10.0,
        raw_open=10.5,
        raw_high=11.0,
        raw_low=10.4,
        raw_close=11.0,
        is_suspended=False,
        adj_factor=1.0,
        listing_trading_day=100,
    )
    assert result["open_at_limit_up"] == "UNKNOWN"
    assert result["can_buy_at_open"] == "UNKNOWN"
    assert result["historical_st_branch_status"] == "NOT_EVALUABLE_MISSING_HISTORICAL_ST"


def test_scheduled_close_at_limit_down_is_conservatively_unfilled():
    result = evaluate_price_limit_bar(
        code="600000.SH",
        trade_date=date(2022, 1, 4),
        board=None,
        historical_st_status="FALSE",
        listing_date=date(2000, 1, 1),
        previous_unadjusted_close=10.0,
        raw_open=9.8,
        raw_high=10.0,
        raw_low=9.0,
        raw_close=9.0,
        is_suspended=False,
        adj_factor=1.0,
        listing_trading_day=100,
    )
    assert result["close_at_limit_down"] == "TRUE"
    assert result["can_sell_at_close"] == "FALSE"
    assert result["scheduled_close_fill_status"] == "ASSUMED_UNFILLED_CONSERVATIVE"


def test_registration_listing_phase_has_no_ordinary_limit():
    result = get_price_limit_rule(
        "300001.SZ",
        date(2024, 1, 3),
        None,
        "FALSE",
        date(2024, 1, 2),
        10.0,
        listing_trading_day=2,
    )
    assert result.rule_status == "KNOWN_NO_DAILY_LIMIT"


def test_one_price_limit_up_is_not_buyable():
    result = _evaluate(
        code="600000.SH",
        trade_date=date(2022, 1, 4),
        previous=10.0,
        open_=11.0,
        high=11.0,
        low=11.0,
        close=11.0,
    )
    assert result["one_price_limit_up"] == "TRUE"
    assert result["can_buy_at_open"] == "FALSE"


def test_limit_up_open_that_later_opens_remains_unknown_from_daily_data():
    result = _evaluate(
        code="600000.SH",
        trade_date=date(2022, 1, 4),
        previous=10.0,
        open_=11.0,
        high=11.0,
        low=10.5,
        close=10.8,
    )
    assert result["open_at_limit_up"] == "TRUE"
    assert result["one_price_limit_up"] == "FALSE"
    assert result["can_buy_at_open"] == "UNKNOWN"


def test_opening_auction_evidence_can_confirm_limit_open_fill():
    result = evaluate_price_limit_bar(
        code="600000.SH",
        trade_date=date(2022, 1, 4),
        board=None,
        historical_st_status="FALSE",
        listing_date=date(2000, 1, 1),
        previous_unadjusted_close=10.0,
        raw_open=11.0,
        raw_high=11.0,
        raw_low=10.5,
        raw_close=10.8,
        is_suspended=False,
        adj_factor=1.0,
        listing_trading_day=100,
        opening_auction_fill_status="TRUE",
    )
    assert result["can_buy_at_open"] == "TRUE"


def test_missing_adjustment_factor_keeps_execution_unknown():
    result = evaluate_price_limit_bar(
        code="600000.SH",
        trade_date=date(2022, 1, 4),
        board=None,
        historical_st_status="FALSE",
        listing_date=date(2000, 1, 1),
        previous_unadjusted_close=10.0,
        raw_open=10.0,
        raw_high=11.0,
        raw_low=9.9,
        raw_close=11.0,
        is_suspended=False,
        adj_factor=None,
        listing_trading_day=100,
    )
    assert result["limit_rule_status"] == "KNOWN_LIMIT"
    assert result["can_buy_at_open"] == "UNKNOWN"


def test_enrichment_populates_explicit_execution_fields():
    bars = pd.DataFrame([{
        "date": "2022-01-04",
        "code": "600000.SH",
        "board": "MAIN_SH",
        "historical_st_status": "FALSE",
        "listing_date": "1999-11-10",
        "listing_trading_day": 1000,
        "raw_pre_close": 10.0,
        "raw_open": 10.0,
        "raw_high": 11.0,
        "raw_low": 9.9,
        "raw_close": 11.0,
        "adj_factor": 1.0,
        "is_suspended": False,
    }])

    out = enrich_price_limit_fields(bars)

    assert out.loc[0, "limit_up_price"] == 11.0
    assert out.loc[0, "close_at_limit_up"] == "TRUE"
    assert out.loc[0, "one_price_limit_up"] == "FALSE"
    assert out.loc[0, "can_buy_at_open"] == "TRUE"


@pytest.mark.parametrize(
    ("code", "trade_date", "previous", "open_", "high", "low", "close", "listing_date"),
    [
        ("600221.SH", date(2010, 2, 4), 3.45, 3.44, 3.82, 3.37, 3.82, date(1999, 11, 25)),
        ("601059.SH", date(2023, 8, 4), 17.96, 18.51, 19.78, 17.96, 19.78, date(2023, 2, 1)),
        ("600229.SH", date(2015, 5, 14), 20.14, 20.07, 22.32, 19.81, 22.32, date(2000, 1, 20)),
        ("300039.SZ", date(2022, 2, 21), 6.71, 6.64, 7.93, 6.39, 7.45, date(2010, 1, 8)),
        ("000034.SZ", date(2015, 11, 10), 11.06, 10.92, 12.32, 10.81, 12.32, date(1994, 5, 9)),
    ],
)
def test_audited_close_limit_cases_are_not_one_price_unbuyable(
    code, trade_date, previous, open_, high, low, close, listing_date
):
    result = _evaluate(
        code=code,
        trade_date=trade_date,
        previous=previous,
        open_=open_,
        high=high,
        low=low,
        close=close,
        listing_date=listing_date,
    )
    assert result["one_price_limit_up"] == "FALSE"
    assert result["can_buy_at_open"] == "TRUE"


def _evaluate(
    *,
    code: str,
    trade_date: date,
    previous: float,
    open_: float,
    high: float,
    low: float,
    close: float,
    listing_date: date = date(2000, 1, 1),
):
    return evaluate_price_limit_bar(
        code=code,
        trade_date=trade_date,
        board=None,
        historical_st_status="FALSE",
        listing_date=listing_date,
        previous_unadjusted_close=previous,
        raw_open=open_,
        raw_high=high,
        raw_low=low,
        raw_close=close,
        is_suspended=False,
        adj_factor=1.0,
        listing_trading_day=100,
    )
