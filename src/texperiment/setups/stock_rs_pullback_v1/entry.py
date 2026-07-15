from __future__ import annotations


def next_open_entry_price(next_bar: dict) -> float | None:
    if next_bar.get("is_limit_up", False):
        return None
    price = next_bar.get("open")
    return float(price) if price is not None else None
