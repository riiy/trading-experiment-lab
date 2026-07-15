from __future__ import annotations

REQUIRED_TICKET_FIELDS = {
    "setup_id", "code", "entry_price", "stop_price", "target_price",
    "shares", "planned_loss", "capital_used"
}


def validate_ticket_payload(payload: dict) -> None:
    missing = REQUIRED_TICKET_FIELDS - set(payload)
    if missing:
        raise ValueError(f"ticket payload missing fields: {sorted(missing)}")
    if payload["planned_loss"] > 500:
        raise ValueError("planned_loss exceeds 500")
