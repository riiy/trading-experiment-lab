from __future__ import annotations


def top_n_contribution_ratio(returns: list[float], n: int = 3) -> float:
    total = sum(returns)
    if total == 0:
        return float("inf")
    top = sum(sorted(returns, reverse=True)[:n])
    return top / total
