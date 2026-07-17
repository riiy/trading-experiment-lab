from __future__ import annotations

from typing import Any

from texperiment.tickets.generator import render_ticket_generation_report


def render_ticket_report(ticket: str) -> str:
    return ticket


def render_ticket_summary_report(summary: dict[str, Any]) -> str:
    return render_ticket_generation_report(summary)
