from __future__ import annotations

import pandas as pd


def add_volume_ma(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    out = df.sort_values(["code", "date"]).copy()
    out[f"vol_ma{window}"] = out.groupby("code")["volume"].transform(
        lambda s: s.rolling(window=window, min_periods=window).mean()
    )
    return out


def volume_less_than_ma(row: dict, window: int = 5) -> bool:
    key = f"vol_ma{window}"
    return row.get("volume") is not None and row.get(key) is not None and row["volume"] < row[key]
