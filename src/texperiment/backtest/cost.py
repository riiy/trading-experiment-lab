from __future__ import annotations

COST_MODEL_VERSION = "ROUND_TRIP_COST_V1"


def apply_round_trip_cost(gross_return: float, round_trip_cost: float = 0.002) -> float:
    """Apply simple round-trip cost to a gross percentage return.

    ``round_trip_cost=0.002`` means 20 bps total friction for buy+sell.
    """
    return gross_return - round_trip_cost
