import pytest

from texperiment.tickets.generator import generate_ticket
from texperiment.tickets.validator import validate_ticket_payload


def test_generate_and_validate_ticket_payload():
    payload = {
        "setup_id":"STOCK_RS_PULLBACK_v1",
        "status":"accepted_trade",
        "code":"000001.SZ",
        "entry_date":"2026-01-03",
        "entry_price":50,
        "stop_price":47.5,
        "target_price":55,
        "shares":200,
        "planned_loss":500,
        "capital_used":10000,
        "per_share_risk":2.5,
    }
    validate_ticket_payload(payload)
    ticket = generate_ticket(**payload)
    assert "STOCK_RS_PULLBACK_v1" in ticket


def test_ticket_validator_rejects_fractional_shares():
    payload = {
        "status": "accepted_trade",
        "setup_id": "STOCK_RS_PULLBACK_v1",
        "code": "000001.SZ",
        "entry_date": "2026-01-03",
        "entry_price": 50,
        "stop_price": 47.5,
        "target_price": 55,
        "shares": 100.5,
        "planned_loss": 251.25,
        "capital_used": 5025,
        "per_share_risk": 2.5,
    }
    with pytest.raises(ValueError, match="integer"):
        validate_ticket_payload(payload)


def test_direct_ticket_generation_rejects_forbidden_order_field():
    with pytest.raises(ValueError, match="forbidden live-order fields"):
        generate_ticket(
            status="accepted_trade",
            setup_id="STOCK_RS_PULLBACK_v1",
            code="000001.SZ",
            entry_date="2026-01-03",
            entry_price=50,
            stop_price=47.5,
            target_price=55,
            shares=200,
            planned_loss=500,
            capital_used=10000,
            per_share_risk=2.5,
            broker="forbidden",
        )
