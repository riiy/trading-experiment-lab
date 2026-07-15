from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskFlag:
    code: str
    date: str
    reason: str
