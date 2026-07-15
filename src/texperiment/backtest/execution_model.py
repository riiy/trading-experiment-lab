from __future__ import annotations


def can_buy_at_open(bar: dict) -> tuple[bool, str | None]:
    if bar.get("is_limit_up", False):
        return False, "invalid_limit_up_cannot_buy"
    if bar.get("open") is None:
        return False, "invalid_no_next_open"
    return True, None
