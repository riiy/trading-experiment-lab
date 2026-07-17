from __future__ import annotations

import pandas as pd

from texperiment.metrics.performance import profit_factor
from texperiment.metrics.robustness import best_n_removed_mean


def attach_latest_industry(
    trades: pd.DataFrame,
    metadata: pd.DataFrame | None,
    *,
    trade_date_col: str = "signal_date",
) -> pd.DataFrame:
    """Attach industry/name as known on each trade date.

    ``metadata`` may be a universe table, daily bars table, or any table with at
    least ``code`` and optional ``industry`` / ``name`` columns. Dated metadata
    uses the latest row on or before each trade date; future metadata is ignored.
    """
    out = trades.copy()
    if metadata is None or metadata.empty or "code" not in metadata.columns or "industry" not in metadata.columns:
        if "industry" not in out.columns:
            out["industry"] = "UNKNOWN"
        out["industry"] = _normalize_industry(out["industry"])
        return out

    meta = metadata.copy()
    meta["code"] = meta["code"].astype(str)
    if "date" in meta.columns and trade_date_col in out.columns:
        meta["date"] = pd.to_datetime(meta["date"], errors="coerce")
        out[trade_date_col] = pd.to_datetime(out[trade_date_col], errors="coerce")
        meta = meta.dropna(subset=["date"]).sort_values(["date", "code"])
        meta = meta.drop_duplicates(["code", "date"], keep="last")
        meta_cols = ["code", "date", "industry"]
        if "name" in meta.columns and "name" not in out.columns:
            meta_cols.append("name")
        right = meta[meta_cols]
        left = out.copy()
        left["__row_order"] = range(len(left))
        left = left.sort_values([trade_date_col, "code"])
        right = right.sort_values(["date", "code"])
        joined = pd.merge_asof(
            left,
            right,
            left_on=trade_date_col,
            right_on="date",
            by="code",
            direction="backward",
        )
        joined = joined.sort_values("__row_order").drop(columns=["__row_order", "date_y"], errors="ignore")
        joined = joined.rename(columns={"date_x": "date"})
        joined["industry"] = _normalize_industry(joined["industry"])
        return joined
    else:
        meta = meta.drop_duplicates("code", keep="last")
    cols = ["code", "industry"]
    if "name" in meta.columns and "name" not in out.columns:
        cols.append("name")
    meta = meta[cols]
    out["code"] = out["code"].astype(str)
    if "industry" in out.columns:
        out = out.drop(columns=["industry"])
    out = out.merge(meta, on="code", how="left")
    out["industry"] = _normalize_industry(out["industry"])
    return out


def by_industry(df: pd.DataFrame, return_col: str = "net_return") -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "industry",
                "valid_trades",
                "trade_share",
                "mean_net_return",
                "median_net_return",
                "win_rate",
                "profit_factor",
                "best_3_removed_mean",
                "net_return_sum",
            ]
        )
    out = df.copy()
    if "industry" not in out.columns:
        out["industry"] = "UNKNOWN"
    out["industry"] = _normalize_industry(out["industry"])
    out[return_col] = pd.to_numeric(out[return_col], errors="coerce")
    out = out.dropna(subset=[return_col])
    total = max(len(out), 1)

    rows: list[dict] = []
    for industry, group in out.groupby("industry", sort=True):
        returns = group[return_col].dropna().astype(float).tolist()
        series = pd.Series(returns, dtype="float64")
        rows.append(
            {
                "industry": str(industry),
                "valid_trades": int(len(returns)),
                "trade_share": float(len(returns) / total),
                "mean_net_return": float(series.mean()) if returns else 0.0,
                "median_net_return": float(series.median()) if returns else 0.0,
                "win_rate": float((series > 0).mean()) if returns else 0.0,
                "profit_factor": profit_factor(returns),
                "best_3_removed_mean": best_n_removed_mean(returns, n=3),
                "net_return_sum": float(sum(returns)),
            }
        )
    return pd.DataFrame(rows).sort_values(["valid_trades", "net_return_sum"], ascending=[False, False]).reset_index(drop=True)


def _normalize_industry(values: pd.Series) -> pd.Series:
    out = values.astype("string").str.strip()
    return out.mask(out.isna() | out.eq(""), "UNKNOWN").astype(str)
