from __future__ import annotations


def render_account_summary(summary: dict) -> str:
    return "\n".join(["# Account Simulation Report", ""] + [f"- {k}: {v}" for k, v in summary.items()]) + "\n"
