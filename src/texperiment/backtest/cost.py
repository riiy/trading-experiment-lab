from __future__ import annotations


def apply_round_trip_cost(gross_return: float, round_trip_cost: float = 0.002) -> float:
    return gross_return - round_trip_cost
