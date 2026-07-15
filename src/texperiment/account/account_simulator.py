from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AccountState:
    cash: float = 30_000
    realized_pnl: float = 0.0
    open_positions: int = 0


def can_open_position(state: AccountState, *, max_positions: int = 1) -> bool:
    return state.open_positions < max_positions
