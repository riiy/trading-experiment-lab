from __future__ import annotations

from texperiment.tickets.template import TRADE_TICKET_TEMPLATE


def generate_ticket(**kwargs) -> str:
    return TRADE_TICKET_TEMPLATE.format(**kwargs)
