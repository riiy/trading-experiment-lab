from __future__ import annotations


def target_price(entry_price: float, stop_price: float, r_multiple: float = 2.0) -> float:
    risk = entry_price - stop_price
    if risk <= 0:
        raise ValueError("entry_price must be greater than stop_price")
    return entry_price + r_multiple * risk


def classify_exit(bar: dict, *, stop_price: float, target_price_: float) -> str | None:
    if bar.get("low") is not None and bar["low"] <= stop_price:
        return "stop_loss"
    if bar.get("high") is not None and bar["high"] >= target_price_:
        return "target_2r"
    return None
