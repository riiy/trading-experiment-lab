from __future__ import annotations

import math


def best_n_removed_mean(returns: list[float], n: int = 3) -> float:
    nums = [float(r) for r in returns if _is_number(r)]
    if not nums:
        return 0.0
    trimmed = sorted(nums, reverse=True)[n:]
    if not trimmed:
        return 0.0
    return float(sum(trimmed) / len(trimmed))


def worst_n_removed_mean(returns: list[float], n: int = 3) -> float:
    nums = [float(r) for r in returns if _is_number(r)]
    if not nums:
        return 0.0
    trimmed = sorted(nums)[n:]
    if not trimmed:
        return 0.0
    return float(sum(trimmed) / len(trimmed))


def _is_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
