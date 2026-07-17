from __future__ import annotations

import math
from typing import Any


def passes_strength_filter(
    row: dict[str, Any],
    *,
    excess_return_min: float = 0.05,
    require_close_above_ma20: bool = True,
    require_ma20_above_ma60: bool = True,
    require_20d_high_recent: bool = True,
) -> bool:
    """Return True if a row passes the pre-registered strength filter.

    This function expects current/historical indicator fields only. It must not
    receive or use any future price fields.
    """
    excess = _num(row.get("excess_ret20"))
    if excess is None or not excess > excess_return_min:
        return False

    if require_close_above_ma20:
        if "close_above_ma20" in row:
            if not _bool(row.get("close_above_ma20")):
                return False
        elif not _gt(row.get("close"), row.get("ma20")):
            return False

    if require_ma20_above_ma60:
        if "ma20_above_ma60" in row:
            if not _bool(row.get("ma20_above_ma60")):
                return False
        elif not _gt(row.get("ma20"), row.get("ma60")):
            return False

    if require_20d_high_recent and not _bool(row.get("made_20d_high_recent", False)):
        return False

    return True


def passes_pullback_filter(
    row: dict[str, Any],
    *,
    drawdown_min: float = 0.03,
    drawdown_max: float = 0.08,
    volume_less_than_ma5: bool = True,
    require_close_above_ma20: bool = True,
    require_not_break_breakout_body_midpoint: bool = True,
) -> bool:
    """Return True if a row is an eligible orderly pullback day."""
    drawdown = _num(row.get("drawdown_from_10d_high"))
    if drawdown is None or not (drawdown_min <= drawdown <= drawdown_max):
        return False

    if volume_less_than_ma5:
        if "volume_below_ma5" in row:
            if not _bool(row.get("volume_below_ma5")):
                return False
        elif not _lt(row.get("volume"), row.get("vol_ma5")):
            return False

    if require_close_above_ma20:
        if "close_above_ma20" in row:
            if not _bool(row.get("close_above_ma20")):
                return False
        elif not _gt(row.get("close"), row.get("ma20")):
            return False

    if require_not_break_breakout_body_midpoint:
        midpoint = _num(row.get("breakout_body_midpoint"))
        # If the upstream indicator is not available yet, do not reject. The
        # signal report should make this explicit; later versions can implement
        # precise breakout-candle midpoint detection.
        if midpoint is not None and not _ge(row.get("close"), midpoint):
            return False

    return True


def is_entry_triggered(row: dict[str, Any], *, pullback_high: float) -> bool:
    """Entry trigger: close reclaims pullback-day high.

    The downstream backtest will execute at the next trading day's open.
    """
    return _gt(row.get("close"), pullback_high)


def is_executable_row(row: dict[str, Any]) -> bool:
    """Return True if the row is executable under universe/limit filters.

    Missing universe fields are treated as permissive for unit tests and early
    pipeline work. Formal validation should pass a daily universe table.
    """
    if "is_tradable_universe" in row and not _bool(row.get("is_tradable_universe")):
        return False
    if _bool(row.get("is_suspended", False)):
        return False
    if _bool(row.get("is_limit_up", False)) or _bool(row.get("is_limit_down", False)):
        return False
    return True


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


def _gt(left: Any, right: Any) -> bool:
    left_num = _num(left)
    right_num = _num(right)
    return left_num is not None and right_num is not None and left_num > right_num


def _ge(left: Any, right: Any) -> bool:
    left_num = _num(left)
    right_num = _num(right)
    return left_num is not None and right_num is not None and left_num >= right_num


def _lt(left: Any, right: Any) -> bool:
    left_num = _num(left)
    right_num = _num(right)
    return left_num is not None and right_num is not None and left_num < right_num
