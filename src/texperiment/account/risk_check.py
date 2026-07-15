from __future__ import annotations


def check_planned_loss(planned_loss: float, limit: float = 500) -> tuple[bool, str | None]:
    if planned_loss > limit:
        return False, "planned_loss_exceeded"
    return True, None
