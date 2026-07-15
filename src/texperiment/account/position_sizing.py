from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PositionSizeResult:
    shares: int
    capital_used: float
    planned_loss: float
    per_share_risk: float
    valid: bool
    reason: str | None = None


def size_position(
    *,
    entry_price: float,
    stop_price: float,
    max_planned_loss: float = 500,
    capital_limit: float = 30_000,
    lot_size: int = 100,
    max_one_lot_value: float = 15_000,
) -> PositionSizeResult:
    per_share_risk = entry_price - stop_price
    if per_share_risk <= 0:
        return PositionSizeResult(0, 0, 0, per_share_risk, False, "invalid_stop_price")
    if entry_price * lot_size > max_one_lot_value:
        return PositionSizeResult(0, 0, 0, per_share_risk, False, "invalid_one_lot_too_expensive")

    raw_shares = max_planned_loss / per_share_risk
    shares = math.floor(raw_shares / lot_size) * lot_size
    if shares < lot_size:
        return PositionSizeResult(0, 0, 0, per_share_risk, False, "invalid_risk_too_wide")

    capital_used = shares * entry_price
    planned_loss = shares * per_share_risk
    if capital_used > capital_limit:
        shares = math.floor(capital_limit / entry_price / lot_size) * lot_size
        capital_used = shares * entry_price
        planned_loss = shares * per_share_risk
    if shares < lot_size:
        return PositionSizeResult(0, 0, 0, per_share_risk, False, "invalid_capital_not_enough")
    if planned_loss > max_planned_loss + 1e-9:
        return PositionSizeResult(shares, capital_used, planned_loss, per_share_risk, False, "invalid_planned_loss_exceeded")

    return PositionSizeResult(shares, capital_used, planned_loss, per_share_risk, True)
