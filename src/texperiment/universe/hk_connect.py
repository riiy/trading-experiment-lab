from __future__ import annotations

import pandas as pd


def filter_hk_connect_universe(
    df: pd.DataFrame,
    *,
    min_avg_amount_20d_hkd: float,
    max_board_lot_value_cny: float = 15_000,
) -> pd.DataFrame:
    """Placeholder for HK Connect universe filtering. Not used in v1."""
    out = df.copy()
    mask = (
        out.get("is_hk_connect", False).astype(bool)
        & (out.get("avg_amount_20d", 0) >= min_avg_amount_20d_hkd)
        & (out.get("board_lot_value_cny", float("inf")) <= max_board_lot_value_cny)
    )
    return out.loc[mask].reset_index(drop=True)
