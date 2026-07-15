from __future__ import annotations


def passes_strength_filter(
    row: dict,
    *,
    excess_return_min: float = 0.05,
) -> bool:
    return all([
        row.get("excess_ret20") is not None and row["excess_ret20"] > excess_return_min,
        row.get("close") is not None and row.get("ma20") is not None and row["close"] > row["ma20"],
        row.get("ma20") is not None and row.get("ma60") is not None and row["ma20"] > row["ma60"],
        bool(row.get("made_20d_high_recent", False)),
    ])


def passes_pullback_filter(
    row: dict,
    *,
    drawdown_min: float = 0.03,
    drawdown_max: float = 0.08,
) -> bool:
    drawdown = row.get("drawdown_from_10d_high")
    if drawdown is None or not (drawdown_min <= drawdown <= drawdown_max):
        return False
    return all([
        row.get("volume") is not None and row.get("vol_ma5") is not None and row["volume"] < row["vol_ma5"],
        row.get("close") is not None and row.get("ma20") is not None and row["close"] > row["ma20"],
        row.get("breakout_body_midpoint") is None or row["close"] >= row["breakout_body_midpoint"],
    ])


def is_entry_triggered(row: dict) -> bool:
    """Entry trigger: reclaim pullback-day high."""
    return row.get("close") is not None and row.get("pullback_high") is not None and row["close"] > row["pullback_high"]
