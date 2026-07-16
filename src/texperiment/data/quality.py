from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from texperiment.data.schema import REQUIRED_DAILY_COLUMNS


@dataclass(frozen=True)
class DataQualityReport:
    rows: int
    code_count: int
    min_date: str | None
    max_date: str | None
    duplicate_bars: int
    null_required_cells: int
    non_positive_price_rows: int
    negative_volume_rows: int
    negative_amount_rows: int

    @property
    def ok(self) -> bool:
        return (
            self.duplicate_bars == 0
            and self.null_required_cells == 0
            and self.non_positive_price_rows == 0
            and self.negative_volume_rows == 0
            and self.negative_amount_rows == 0
        )


def assert_no_duplicate_bars(df: pd.DataFrame) -> None:
    dup = int(df.duplicated(["date", "code"]).sum())
    if dup:
        raise ValueError(f"duplicate daily bars found: {dup}")


def missing_ratio(df: pd.DataFrame, column: str) -> float:
    if column not in df.columns:
        raise KeyError(column)
    return float(df[column].isna().mean())


def validate_daily_bars(df: pd.DataFrame, *, strict: bool = True) -> DataQualityReport:
    missing = REQUIRED_DAILY_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"daily bars missing required columns: {sorted(missing)}")

    required = list(REQUIRED_DAILY_COLUMNS)
    duplicate_bars = int(df.duplicated(["date", "code"]).sum())
    null_required_cells = int(df[required].isna().sum().sum())
    price_cols = ["open", "high", "low", "close"]
    non_positive_price_rows = int((df[price_cols] <= 0).any(axis=1).sum())
    negative_volume_rows = int((df["volume"] < 0).sum())
    negative_amount_rows = int((df["amount"] < 0).sum())

    report = DataQualityReport(
        rows=int(len(df)),
        code_count=int(df["code"].nunique()) if len(df) else 0,
        min_date=str(pd.to_datetime(df["date"]).min().date()) if len(df) else None,
        max_date=str(pd.to_datetime(df["date"]).max().date()) if len(df) else None,
        duplicate_bars=duplicate_bars,
        null_required_cells=null_required_cells,
        non_positive_price_rows=non_positive_price_rows,
        negative_volume_rows=negative_volume_rows,
        negative_amount_rows=negative_amount_rows,
    )
    if strict and not report.ok:
        raise ValueError(f"daily bars quality check failed: {report}")
    return report
