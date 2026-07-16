from __future__ import annotations

import struct

import pandas as pd

from texperiment.data.akshare_source import fetch_a_share_daily
from texperiment.data.codecs import normalize_a_share_code
from texperiment.data.normalizer import detect_provider, normalize_daily_bars
from texperiment.data.quality import validate_daily_bars
from texperiment.data.tdx_source import ingest_tdx_a_share_daily, write_tdx_parquet


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
    assert validate_daily_bars(out).ok is True


def test_fetch_akshare_full_market_normalizes_and_reports_failures():
    class FakeAkShare:
        def stock_info_a_code_name(self):
            return pd.DataFrame({"股票代码": ["000001", "600000"], "股票简称": ["平安银行", "浦发银行"]})

        def stock_zh_a_hist(self, *, symbol, **kwargs):
            if symbol == "600000":
                raise RuntimeError("temporary provider failure")
            return pd.DataFrame(
                {
                    "日期": ["20260715"],
                    "股票代码": [symbol],
                    "开盘": [10.0],
                    "最高": [10.5],
                    "最低": [9.8],
                    "收盘": [10.2],
                    "成交量": [100],
                    "成交额": [100000],
                }
            )

    out, report = fetch_a_share_daily(
        "20260715",
        "20260715",
        api=FakeAkShare(),
        pause_seconds=0,
        max_retries=1,
        sleep=lambda _: None,
    )

    assert out.loc[0, "code"] == "000001.SZ"
    assert out.loc[0, "name"] == "平安银行"
    assert out.loc[0, "volume"] == 10000
    assert report.symbols_requested == 2
    assert report.symbols_succeeded == 1
    assert report.symbols_failed == 1
    assert "600000" in report.failed_symbols


def test_fetch_akshare_falls_back_to_spot_symbol_list():
    class FakeAkShare:
        def stock_info_a_code_name(self):
            raise TimeoutError("exchange endpoint timeout")

        def stock_zh_a_spot_em(self):
            return pd.DataFrame({"代码": ["000001"], "名称": ["平安银行"]})

        def stock_zh_a_hist(self, *, symbol, **kwargs):
            return pd.DataFrame(
                {
                    "日期": ["20260715"],
                    "股票代码": [symbol],
                    "开盘": [10.0],
                    "最高": [10.5],
                    "最低": [9.8],
                    "收盘": [10.2],
                    "成交量": [100],
                    "成交额": [100000],
                }
            )

    out, report = fetch_a_share_daily(
        "20260715",
        "20260715",
        api=FakeAkShare(),
        pause_seconds=0,
        max_retries=1,
        sleep=lambda _: None,
    )

    assert out.loc[0, "code"] == "000001.SZ"
    assert report.symbols_requested == 1


def test_ingest_tdx_day_file(tmp_path):
    lday = tmp_path / "vipdoc" / "sz" / "lday"
    lday.mkdir(parents=True)
    records = [
        (20260714, 1000, 1050, 990, 1020, 100000.0, 10, 0),
        (20260715, 1020, 1080, 1010, 1070, 220000.0, 20, 0),
    ]
    (lday / "sz000001.day").write_bytes(b"".join(struct.pack("<5IfII", *record) for record in records))

    out = ingest_tdx_a_share_daily(tmp_path / "vipdoc")

    assert out["code"].tolist() == ["000001.SZ", "000001.SZ"]
    assert out["volume"].tolist() == [1000, 2000]
    assert out.loc[1, "pre_close"] == 10.2
    assert out.loc[0, "source"] == "tongdaxin"
    assert validate_daily_bars(out).ok is True

    report = write_tdx_parquet(
        tmp_path / "vipdoc",
        tmp_path / "processed" / "a_share_daily.parquet",
    )
    assert report.rows == 2
    assert len(pd.read_parquet(tmp_path / "processed" / "a_share_daily.parquet")) == 2
