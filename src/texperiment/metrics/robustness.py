from __future__ import annotations


def best_n_removed_mean(returns: list[float], n: int = 3) -> float:
    if not returns:
        return 0.0
    trimmed = sorted(returns, reverse=True)[n:]
    if not trimmed:
        return 0.0
    return sum(trimmed) / len(trimmed)
