from __future__ import annotations

import math
from typing import Any

EXECUTION_MODEL_VERSION = "A_SHARE_EXECUTION_V2_CONSERVATIVE_LIMIT_EXITS"


def can_buy_at_open(bar: dict[str, Any]) -> tuple[bool, str | None]:
    """Fail closed unless raw-price open fillability is explicitly known."""
    suspended = _tri_state(bar.get("is_suspended"))
    if suspended is None:
        return False, "invalid_open_fillability_unknown"
    if suspended:
        return False, "invalid_suspended_cannot_buy"
    open_price = _num(bar.get("raw_open"))
    if open_price is None:
        return False, "invalid_missing_raw_open"
    if open_price <= 0:
        return False, "invalid_entry_price"
    adj_factor = _num(bar.get("adj_factor"))
    if adj_factor is None or adj_factor <= 0:
        return False, "invalid_missing_adjustment_factor"
    if price_conversion_factor(bar) is None:
        return False, "invalid_inconsistent_price_layers"

    fillability = _tri_state(bar.get("can_buy_at_open"))
    if fillability is None:
        return False, "invalid_open_fillability_unknown"
    if not fillability:
        if _tri_state(bar.get("one_price_limit_up")) is True:
            return False, "invalid_limit_up_cannot_buy"
        return False, "invalid_cannot_buy_at_open"
    return True, None


def can_sell_on_bar(bar: dict[str, Any], *, execution: str = "open") -> tuple[bool | None, str | None]:
    fields = {
        "open": "can_sell_at_open",
        "intraday": "can_sell_intraday",
        "close": "can_sell_at_close",
    }
    if execution not in fields:
        raise ValueError(f"unsupported sell execution: {execution}")
    suspended = _tri_state(bar.get("is_suspended"))
    if suspended is None:
        return None, "invalid_exit_fillability_unknown"
    if suspended:
        return False, "deferred_suspended_cannot_sell"
    if not has_valid_ohlc(bar) or not has_valid_adjusted_ohlc(bar):
        return None, "invalid_missing_price_data"
    if price_conversion_factor(bar) is None:
        return None, "invalid_inconsistent_price_layers"
    fillability = _tri_state(bar.get(fields[execution]))
    if fillability is None:
        return None, "invalid_exit_fillability_unknown"
    if not fillability:
        return False, "deferred_cannot_sell"
    return True, None


def price_transform(bar: dict[str, Any], price_tolerance: float = 0.011) -> tuple[float, float] | None:
    declared = _num(bar.get("adj_factor"))
    if declared is None or declared <= 0:
        return None
    offset = _num(bar.get("adj_offset"))
    offset = 0.0 if offset is None else offset
    for field in ("open", "high", "low", "close"):
        raw = _num(bar.get(f"raw_{field}"))
        adjusted = _num(bar.get(f"adj_{field}"))
        if raw is None or raw <= 0 or adjusted is None or adjusted <= 0:
            return None
        if abs(adjusted - (raw * declared + offset)) > price_tolerance:
            return None
    return declared, offset


def price_conversion_factor(bar: dict[str, Any], price_tolerance: float = 0.011) -> float | None:
    transform = price_transform(bar, price_tolerance=price_tolerance)
    return None if transform is None else transform[0]


def has_valid_ohlc(bar: dict[str, Any]) -> bool:
    vals = [_num(bar.get(c)) for c in ["raw_open", "raw_high", "raw_low", "raw_close"]]
    return all(v is not None and v > 0 for v in vals)


def has_valid_adjusted_ohlc(bar: dict[str, Any]) -> bool:
    vals = [_num(bar.get(c)) for c in ["adj_open", "adj_high", "adj_low", "adj_close"]]
    return all(v is not None and v > 0 for v in vals)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def _tri_state(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    try:
        number = float(value)
        if math.isnan(number):
            return None
        if math.isnan(number):
            return False
        if number in {0.0, 1.0}:
            return bool(number)
    except (TypeError, ValueError):
        pass
    normalized = str(value).strip().lower()
    if normalized in {"1", "1.0", "true", "t", "yes", "y", "是"}:
        return True
    if normalized in {"0", "0.0", "false", "f", "no", "n", "否"}:
        return False
    return None
