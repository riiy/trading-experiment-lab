from __future__ import annotations

import pandas as pd


def add_excess_return(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    *,
    periods: int = 20,
    benchmark_code: str = "000300.SH",
) -> pd.DataFrame:
    """Add benchmark return and excess return by date."""
    b = benchmark_df.loc[benchmark_df["code"] == benchmark_code, ["date", f"ret{periods}"]].copy()
    b = b.rename(columns={f"ret{periods}": f"benchmark_ret{periods}"})
    out = stock_df.merge(b, on="date", how="left")
    out[f"excess_ret{periods}"] = out[f"ret{periods}"] - out[f"benchmark_ret{periods}"]
    return out
