from texperiment.tickets.generator import generate_ticket
from texperiment.tickets.validator import validate_ticket_payload


def test_generate_and_validate_ticket_payload():
    payload = {
        "setup_id":"STOCK_RS_PULLBACK_v1",
        "code":"000001.SZ",
        "entry_price":50,
        "stop_price":47.5,
        "target_price":55,
        "shares":200,
        "planned_loss":500,
        "capital_used":10000,
    }
    validate_ticket_payload(payload)
    ticket = generate_ticket(**payload)
    assert "STOCK_RS_PULLBACK_v1" in ticket
