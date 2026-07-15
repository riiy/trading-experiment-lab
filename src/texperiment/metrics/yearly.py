from __future__ import annotations

import pandas as pd


def by_year(df: pd.DataFrame, return_col: str = "net_return") -> pd.DataFrame:
    out = df.copy()
    out["year"] = pd.to_datetime(out["exit_date"]).dt.year
    return out.groupby("year", as_index=False)[return_col].mean()
