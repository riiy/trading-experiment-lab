from __future__ import annotations

import math


def top_n_contribution_ratio(returns: list[float], n: int = 3) -> float:
    """Return top-N contribution to total net return sum.

    A ratio above 1.0 means the result depends on the best few trades. When
    total return is zero or negative, the ratio is not economically passable;
    return ``inf`` if top-N contribution is positive, otherwise 0.0.
    """
    nums = [float(r) for r in returns if _is_number(r)]
    if not nums:
        return 0.0
    total = sum(nums)
    top = sum(sorted(nums, reverse=True)[:n])
    if total <= 0:
        return float("inf") if top > 0 else 0.0
    return float(top / total)


def top_n_contribution_sum(returns: list[float], n: int = 3) -> float:
    nums = [float(r) for r in returns if _is_number(r)]
    if not nums:
        return 0.0
    return float(sum(sorted(nums, reverse=True)[:n]))


def bottom_n_contribution_sum(returns: list[float], n: int = 3) -> float:
    nums = [float(r) for r in returns if _is_number(r)]
    if not nums:
        return 0.0
    return float(sum(sorted(nums)[:n]))


def _is_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
