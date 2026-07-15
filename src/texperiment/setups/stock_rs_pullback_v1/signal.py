from __future__ import annotations

import pandas as pd

from texperiment.setups.stock_rs_pullback_v1.rules import passes_pullback_filter, passes_strength_filter
from texperiment.setups.stock_rs_pullback_v1.schema import Signal


def generate_candidate_signals(df: pd.DataFrame) -> list[Signal]:
    """Generate candidate signals from rows with precomputed indicators.

    This initial implementation is deliberately transparent. It assumes each row
    already contains all required fields. A later version can implement full
    event-window state tracking for "first pullback" detection.
    """
    signals: list[Signal] = []
    for _, row in df.iterrows():
        r = row.to_dict()
        if passes_strength_filter(r) and passes_pullback_filter(r):
            signal_id = f"STOCK_RS_PULLBACK_v1:{r['code']}:{pd.to_datetime(r['date']).date()}"
            signals.append(
                Signal(
                    signal_id=signal_id,
                    code=str(r["code"]),
                    signal_date=str(pd.to_datetime(r["date"]).date()),
                    pullback_high=float(r["high"]),
                    pullback_low=float(r["low"]),
                    stop_price=float(r["low"]),
                )
            )
    return signals
