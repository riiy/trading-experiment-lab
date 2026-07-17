from __future__ import annotations

import math
from typing import Any


def can_buy_at_open(bar: dict[str, Any]) -> tuple[bool, str | None]:
    """Return whether a signal can be executed at the next open.

    Conservative assumptions for A-share daily bars:
    - suspended / no-trade bars cannot be bought;
    - limit-up entry bars are treated as unfillable;
    - missing or non-positive open is invalid.
    """
    if _bool(bar.get("is_suspended", False)):
        return False, "invalid_suspended_cannot_buy"
    if _bool(bar.get("is_limit_up", False)):
        return False, "invalid_limit_up_cannot_buy"
    open_price = _num(bar.get("open"))
    if open_price is None:
        return False, "invalid_no_next_open"
    if open_price <= 0:
        return False, "invalid_entry_price"
    return True, None


def has_valid_ohlc(bar: dict[str, Any]) -> bool:
    vals = [_num(bar.get(c)) for c in ["open", "high", "low", "close"]]
    return all(v is not None and v > 0 for v in vals)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def _bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    try:
        number = float(value)
        if math.isnan(number):
            return False
        if number in {0.0, 1.0}:
            return bool(number)
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in {"1", "1.0", "true", "t", "yes", "y", "是"}
