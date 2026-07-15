from __future__ import annotations

import pandas as pd


def next_trading_day(calendar: list[pd.Timestamp], date: pd.Timestamp) -> pd.Timestamp | None:
    for d in calendar:
        if d > date:
            return d
    return None
