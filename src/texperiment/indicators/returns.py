from __future__ import annotations

import pandas as pd


def pct_return(series: pd.Series, periods: int) -> pd.Series:
    return series.pct_change(periods=periods)


def add_returns(df: pd.DataFrame, periods: int = 20) -> pd.DataFrame:
    out = df.sort_values(["code", "date"]).copy()
    out[f"ret{periods}"] = out.groupby("code")["close"].transform(lambda s: pct_return(s, periods))
    return out
