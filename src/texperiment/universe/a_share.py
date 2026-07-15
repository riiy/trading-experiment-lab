from __future__ import annotations

import pandas as pd


def filter_a_share_universe(
    df: pd.DataFrame,
    *,
    min_listing_days: int = 180,
    min_avg_amount_20d: float = 300_000_000,
    max_one_lot_value: float = 15_000,
    lot_size: int = 100,
) -> pd.DataFrame:
    """Filter A-share universe for the small Trading Experiment account."""
    out = df.copy()
    conditions = []
    conditions.append(~out.get("is_st", False).astype(bool))
    conditions.append(out.get("listing_days", min_listing_days) >= min_listing_days)
    conditions.append(~out.get("is_suspended", False).astype(bool))
    conditions.append(~out.get("is_limit_up", False).astype(bool))
    conditions.append(~out.get("is_limit_down", False).astype(bool))
    conditions.append(out.get("avg_amount_20d", 0) >= min_avg_amount_20d)
    conditions.append(out["close"] * lot_size <= max_one_lot_value)

    mask = conditions[0]
    for cond in conditions[1:]:
        mask = mask & cond
    return out.loc[mask].reset_index(drop=True)
