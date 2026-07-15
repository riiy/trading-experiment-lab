from __future__ import annotations

import pandas as pd


def assert_no_duplicate_bars(df: pd.DataFrame) -> None:
    dup = df.duplicated(["date", "code"]).sum()
    if dup:
        raise ValueError(f"duplicate daily bars found: {dup}")


def missing_ratio(df: pd.DataFrame, column: str) -> float:
    if column not in df.columns:
        raise KeyError(column)
    return float(df[column].isna().mean())
