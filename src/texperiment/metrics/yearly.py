from __future__ import annotations

import pandas as pd

from texperiment.metrics.performance import profit_factor
from texperiment.metrics.robustness import best_n_removed_mean


def by_year(df: pd.DataFrame, return_col: str = "net_return", date_col: str = "exit_date") -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "year",
                "valid_trades",
                "mean_net_return",
                "median_net_return",
                "win_rate",
                "profit_factor",
                "best_3_removed_mean",
                "net_return_sum",
            ]
        )
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    out[return_col] = pd.to_numeric(out[return_col], errors="coerce")
    out = out.dropna(subset=[date_col, return_col])
    out["year"] = out[date_col].dt.year

    rows: list[dict] = []
    for year, group in out.groupby("year", sort=True):
        returns = group[return_col].dropna().astype(float).tolist()
        rows.append(
            {
                "year": int(year),
                "valid_trades": int(len(returns)),
                "mean_net_return": float(pd.Series(returns).mean()) if returns else 0.0,
                "median_net_return": float(pd.Series(returns).median()) if returns else 0.0,
                "win_rate": float((pd.Series(returns) > 0).mean()) if returns else 0.0,
                "profit_factor": profit_factor(returns),
                "best_3_removed_mean": best_n_removed_mean(returns, n=3),
                "net_return_sum": float(sum(returns)),
            }
        )
    return pd.DataFrame(rows)
