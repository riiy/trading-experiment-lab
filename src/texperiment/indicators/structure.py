from __future__ import annotations

import pandas as pd


def add_high_lookback(df: pd.DataFrame, window: int = 10) -> pd.DataFrame:
    out = df.sort_values(["code", "date"]).copy()
    out[f"high_{window}d"] = out.groupby("code")["high"].transform(
        lambda s: s.rolling(window=window, min_periods=window).max()
    )
    out[f"drawdown_from_{window}d_high"] = 1 - out["close"] / out[f"high_{window}d"]
    return out


def body_midpoint(open_price: float, close_price: float) -> float:
    return (open_price + close_price) / 2


def is_within_drawdown(row: dict, *, min_dd: float = 0.03, max_dd: float = 0.08, key: str = "drawdown_from_10d_high") -> bool:
    dd = row.get(key)
    return dd is not None and min_dd <= dd <= max_dd
