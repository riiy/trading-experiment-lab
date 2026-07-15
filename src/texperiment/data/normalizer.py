from __future__ import annotations

import pandas as pd

REQUIRED_DAILY_COLUMNS = {
    "date", "code", "open", "high", "low", "close", "volume", "amount"
}


def normalize_daily_bars(df: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_DAILY_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"daily bars missing columns: {sorted(missing)}")
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values(["code", "date"]).reset_index(drop=True)
    return out
