from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd

from texperiment.data.codecs import normalize_a_share_code, normalize_date_yyyymmdd
from texperiment.data.schema import (
    BOOLEAN_COLUMNS,
    CANONICAL_DAILY_COLUMNS,
    NUMERIC_COLUMNS,
    REQUIRED_DAILY_COLUMNS,
    SUPPORTED_PROVIDERS,
    StandardizationReport,
)

# Column mappings for common A-share data exports.
# The project does not depend on a single provider. The ingest command accepts raw CSV/parquet
# dumps and converts them into one canonical parquet dataset.
PROVIDER_COLUMN_MAPS: dict[str, Mapping[str, str]] = {
    "canonical": {},
    "akshare": {
        "日期": "date",
        "股票代码": "code",
        "代码": "code",
        "名称": "name",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
        "换手率": "turnover_rate",
        "涨跌幅": "pct_chg",
        "前收盘": "pre_close",
    },
    "tushare": {
        "ts_code": "code",
        "trade_date": "date",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "pre_close": "pre_close",
        "vol": "volume",
        "amount": "amount",
        "pct_chg": "pct_chg",
    },
    "baostock": {
        "date": "date",
        "code": "code",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "preclose": "pre_close",
        "volume": "volume",
        "amount": "amount",
        "turn": "turnover_rate",
        "tradestatus": "trade_status",
        "pctChg": "pct_chg",
        "isST": "is_st",
        "adjustflag": "adj_factor",
    },
}


class DailyBarNormalizationError(ValueError):
    pass


def detect_provider(df: pd.DataFrame) -> str:
    cols = set(df.columns)
    if {"日期", "开盘", "收盘", "成交额"}.issubset(cols):
        return "akshare"
    if {"ts_code", "trade_date", "open", "close"}.issubset(cols):
        return "tushare"
    if {"date", "code", "preclose", "tradestatus"}.issubset(cols):
        return "baostock"
    if REQUIRED_DAILY_COLUMNS.issubset(cols):
        return "canonical"
    raise DailyBarNormalizationError(
        "Cannot detect provider. Pass provider explicitly or convert input columns first. "
        f"Columns: {sorted(cols)}"
    )


def normalize_daily_bars(
    df: pd.DataFrame,
    *,
    provider: str = "auto",
    adj_type: str = "qfq",
    source: str | None = None,
    source_file: str | Path | None = None,
) -> pd.DataFrame:
    """Normalize one daily-bar DataFrame into the canonical project schema.

    Supported provider exports:
    - canonical: already has date/code/open/high/low/close/volume/amount
    - akshare: Chinese column names from stock_zh_a_hist-style exports
    - tushare: daily-style exports; volume is converted from hands to shares and amount from thousand CNY to CNY
    - baostock: query_history_k_data_plus-style exports
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise DailyBarNormalizationError(f"Unsupported provider: {provider}")
    provider_resolved = detect_provider(df) if provider == "auto" else provider
    out = _rename_columns(df, provider_resolved).copy()

    missing = REQUIRED_DAILY_COLUMNS - set(out.columns)
    if missing:
        raise DailyBarNormalizationError(f"daily bars missing columns after mapping: {sorted(missing)}")

    out["raw_code"] = out["code"].astype(str)
    out["date"] = out["date"].map(normalize_date_yyyymmdd)
    out["date"] = pd.to_datetime(out["date"], errors="raise")
    out["code"] = out["code"].map(normalize_a_share_code)
    out["market"] = out["code"].str[-2:]

    for col in NUMERIC_COLUMNS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if provider_resolved == "tushare":
        # Tushare daily: vol is hands, amount is thousand CNY.
        if "volume" in out.columns:
            out["volume"] = out["volume"] * 100
        if "amount" in out.columns:
            out["amount"] = out["amount"] * 1000

    if provider_resolved == "akshare":
        # AkShare stock_zh_a_hist 成交量通常为手；转成股，便于统一一手/流动性计算。
        if "volume" in out.columns:
            out["volume"] = out["volume"] * 100

    out["adj_type"] = adj_type
    out["source"] = source or provider_resolved
    out["source_file"] = str(source_file) if source_file is not None else ""

    _fill_optional_columns(out)
    _derive_status_columns(out)
    out = out[CANONICAL_DAILY_COLUMNS]
    out = out.sort_values(["code", "date"]).reset_index(drop=True)
    return out


def build_standardization_report(df: pd.DataFrame, provider: str) -> StandardizationReport:
    dup = int(df.duplicated(["date", "code"]).sum()) if {"date", "code"}.issubset(df.columns) else -1
    return StandardizationReport(
        provider=provider,
        rows_in=len(df),
        rows_out=len(df),
        min_date=str(df["date"].min().date()) if "date" in df.columns and len(df) else None,
        max_date=str(df["date"].max().date()) if "date" in df.columns and len(df) else None,
        code_count=int(df["code"].nunique()) if "code" in df.columns else 0,
        duplicate_count=dup,
    )


def _rename_columns(df: pd.DataFrame, provider: str) -> pd.DataFrame:
    mapping = PROVIDER_COLUMN_MAPS.get(provider)
    if mapping is None:
        raise DailyBarNormalizationError(f"No column mapping for provider: {provider}")
    return df.rename(columns=mapping)


def _fill_optional_columns(df: pd.DataFrame) -> None:
    defaults = {
        "name": "",
        "pre_close": pd.NA,
        "turnover_rate": pd.NA,
        "pct_chg": pd.NA,
        "adj_factor": pd.NA,
        "trade_status": "",
        "is_suspended": False,
        "is_limit_up": False,
        "is_limit_down": False,
        "is_st": False,
        "listing_days": pd.NA,
        "industry": "",
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default

    for col in BOOLEAN_COLUMNS:
        df[col] = _to_bool_series(df[col])


def _derive_status_columns(df: pd.DataFrame) -> None:
    # Baostock tradestatus: 1=交易, 0=停牌. Keep existing values if supplied.
    if "trade_status" in df.columns:
        status = df["trade_status"].astype(str).str.strip()
        df.loc[status == "0", "is_suspended"] = True
        df.loc[status == "1", "is_suspended"] = False

    # A simple derived limit-up/down flag. This is approximate and conservative; exact price-limit
    # handling belongs in a later corporate action / board-type module.
    if "pre_close" in df.columns and df["pre_close"].notna().any():
        pct_from_preclose = (df["close"] / df["pre_close"] - 1.0).where(df["pre_close"] > 0)
        df.loc[pct_from_preclose >= 0.098, "is_limit_up"] = True
        df.loc[pct_from_preclose <= -0.098, "is_limit_down"] = True


def _to_bool_series(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s.fillna(False)
    normalized = s.astype(str).str.strip().str.lower()
    return normalized.isin({"1", "true", "t", "yes", "y", "是", "st", "*st"})
