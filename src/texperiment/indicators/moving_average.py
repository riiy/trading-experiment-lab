from __future__ import annotations

import pandas as pd


def moving_average(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def add_moving_averages(df: pd.DataFrame, windows: tuple[int, ...] = (20, 60)) -> pd.DataFrame:
    out = df.sort_values(["code", "date"]).copy()
    for window in windows:
        out[f"ma{window}"] = out.groupby("code")["close"].transform(lambda s: moving_average(s, window))
    return out
