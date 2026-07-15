from __future__ import annotations


def render_validation_summary(metrics: dict, decision: str) -> str:
    lines = ["# Validation Report", "", f"Decision: {decision}", ""]
    for k, v in metrics.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines) + "\n"
