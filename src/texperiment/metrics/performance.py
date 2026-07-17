from __future__ import annotations

import math
from collections.abc import Sequence

import pandas as pd


def _finite(values: Sequence[float]) -> list[float]:
    out: list[float] = []
    for value in values:
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out.append(v)
    return out


def mean(values: Sequence[float]) -> float:
    nums = _finite(values)
    if not nums:
        return 0.0
    return float(sum(nums) / len(nums))


def median(values: Sequence[float]) -> float:
    nums = _finite(values)
    if not nums:
        return 0.0
    return float(pd.Series(nums).median())


def profit_factor(returns: Sequence[float]) -> float:
    nums = _finite(returns)
    gains = sum(r for r in nums if r > 0)
    losses = -sum(r for r in nums if r < 0)
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def win_rate(returns: Sequence[float]) -> float:
    nums = _finite(returns)
    if not nums:
        return 0.0
    return float(sum(1 for r in nums if r > 0) / len(nums))


def summarize_returns(returns: Sequence[float]) -> dict[str, float | int]:
    nums = _finite(returns)
    if not nums:
        return {
            "count": 0,
            "mean": 0.0,
            "median": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "sum": 0.0,
            "max_gain": 0.0,
            "max_loss": 0.0,
        }
    return {
        "count": len(nums),
        "mean": mean(nums),
        "median": median(nums),
        "win_rate": win_rate(nums),
        "profit_factor": profit_factor(nums),
        "sum": float(sum(nums)),
        "max_gain": float(max(nums)),
        "max_loss": float(min(nums)),
    }
