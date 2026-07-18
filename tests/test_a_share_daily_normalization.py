from __future__ import annotations

import pandas as pd

from texperiment.data.codecs import normalize_a_share_code
from texperiment.data.normalizer import detect_provider, normalize_daily_bars
from texperiment.data.quality import validate_daily_bars


def test_normalize_a_share_code():
    assert normalize_a_share_code("000001") == "000001.SZ"
    assert normalize_a_share_code("1") == "000001.SZ"
    assert normalize_a_share_code("600000") == "600000.SH"
    assert normalize_a_share_code("sh.600000") == "600000.SH"
    assert normalize_a_share_code("sz000001") == "000001.SZ"
    assert normalize_a_share_code("833000") == "833000.BJ"


def test_detect_akshare_provider_and_normalize_units():
    raw = pd.DataFrame(
        {
            "日期": ["2026-07-14", "2026-07-15"],
            "股票代码": ["000001", "000001"],
            "开盘": [10.0, 10.5],
            "收盘": [10.5, 10.7],
            "最高": [10.8, 10.9],
            "最低": [9.9, 10.4],
            "成交量": [1000, 2000],
            "成交额": [1000000, 2200000],
            "换手率": [1.1, 1.2],
            "涨跌幅": [5.0, 1.9],
        }
    )
    assert detect_provider(raw) == "akshare"
    out = normalize_daily_bars(raw, provider="auto", adj_type="qfq")
    assert out.loc[0, "code"] == "000001.SZ"
    assert out.loc[0, "volume"] == 100000
    assert out.loc[0, "amount"] == 1000000
    assert out.loc[0, "adj_type"] == "qfq"
    assert out.loc[0, "adj_open"] == 10.0
    assert pd.isna(out.loc[0, "raw_open"])
    assert out.loc[0, "can_buy_at_open"] == "UNKNOWN"
    assert validate_daily_bars(out).ok is True


def test_tushare_amount_and_volume_conversion():
    raw = pd.DataFrame(
        {
            "ts_code": ["600000.SH"],
            "trade_date": ["20260715"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "pre_close": [10.0],
            "vol": [1234],
            "amount": [5678],
            "pct_chg": [2.0],
        }
    )
    out = normalize_daily_bars(raw, provider="tushare")
    assert out.loc[0, "date"].strftime("%Y-%m-%d") == "2026-07-15"
    assert out.loc[0, "code"] == "600000.SH"
    assert out.loc[0, "volume"] == 123400
    assert out.loc[0, "amount"] == 5678000
    assert out.loc[0, "adj_open"] == 10.0
    assert pd.isna(out.loc[0, "raw_open"])


def test_baostock_trade_status_marks_suspended():
    raw = pd.DataFrame(
        {
            "date": ["2026-07-15"],
            "code": ["sh.600000"],
            "open": [10.0],
            "high": [10.0],
            "low": [10.0],
            "close": [10.0],
            "preclose": [10.0],
            "volume": [0],
            "amount": [0],
            "tradestatus": ["0"],
            "isST": ["0"],
        }
    )
    out = normalize_daily_bars(raw, provider="baostock")
    assert out.loc[0, "is_suspended"] is True or bool(out.loc[0, "is_suspended"]) is True
    assert pd.isna(out.loc[0, "adj_factor"])
    assert validate_daily_bars(out).ok is True


def test_unadjusted_input_populates_only_raw_price_layer():
    raw = pd.DataFrame({
        "date": ["2026-07-15"],
        "code": ["600000.SH"],
        "open": [10.0],
        "high": [10.5],
        "low": [9.8],
        "close": [10.2],
        "pre_close": [10.0],
        "volume": [1000],
        "amount": [10000],
    })

    out = normalize_daily_bars(raw, provider="canonical", adj_type="none")

    assert out.loc[0, "raw_open"] == 10.0
    assert out.loc[0, "raw_pre_close"] == 10.0
    assert pd.isna(out.loc[0, "adj_open"])
    assert out.loc[0, "limit_rule_status"] == "UNKNOWN_NOT_EVALUATED"


def test_canonical_dual_price_layers_are_preserved():
    raw = pd.DataFrame({
        "date": ["2026-07-15"],
        "code": ["600000.SH"],
        "open": [9.5],
        "high": [9.975],
        "low": [9.31],
        "close": [9.69],
        "volume": [1000],
        "amount": [10000],
        "raw_open": [10.0],
        "raw_high": [10.5],
        "raw_low": [9.8],
        "raw_close": [10.2],
        "raw_pre_close": [10.0],
        "adj_open": [9.5],
        "adj_high": [9.975],
        "adj_low": [9.31],
        "adj_close": [9.69],
        "adj_factor": [0.95],
    })

    out = normalize_daily_bars(raw, provider="canonical", adj_type="qfq")

    assert out.loc[0, "raw_open"] == 10.0
    assert out.loc[0, "adj_open"] == 9.5
    assert out.loc[0, "adj_factor"] == 0.95
