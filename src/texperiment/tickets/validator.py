from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

FORBIDDEN_LIVE_ORDER_FIELDS = {
    "broker",
    "broker_account",
    "order_id",
    "order_type",
    "submit_order",
    "auto_submit",
    "live_order",
    "api_key",
}

REQUIRED_TICKET_FIELDS = {
    "status",
    "setup_id",
    "code",
    "entry_date",
    "entry_price",
    "stop_price",
    "target_price",
    "shares",
    "planned_loss",
    "capital_used",
    "per_share_risk",
}


@dataclass(frozen=True)
class TicketValidationConfig:
    """Hard risk limits for a generated trade ticket."""

    setup_id: str = "STOCK_RS_PULLBACK_v1"
    capital_limit: float = 30_000.0
    max_planned_loss_per_trade: float = 500.0
    max_one_lot_value: float = 15_000.0
    lot_size: int = 100

    @classmethod
    def from_configs(
        cls,
        *,
        account_config: dict[str, Any] | None = None,
        setup_config: dict[str, Any] | None = None,
    ) -> "TicketValidationConfig":
        account_config = account_config or {}
        setup_config = setup_config or {}
        account = account_config.get("account", {})
        risk = account_config.get("risk", {})
        universe = setup_config.get("universe", {})
        account_constraints = setup_config.get("account_constraints", {})
        return cls(
            setup_id=str(setup_config.get("setup_id", "STOCK_RS_PULLBACK_v1")),
            capital_limit=float(account_constraints.get("capital_limit", account.get("capital_limit", 30_000.0))),
            max_planned_loss_per_trade=float(
                account_constraints.get("max_planned_loss_per_trade", risk.get("max_planned_loss_per_trade", 500.0))
            ),
            max_one_lot_value=float(universe.get("max_one_lot_value", 15_000.0)),
            lot_size=int(universe.get("lot_size", 100)),
        )


def validate_ticket_payload(payload: dict[str, Any], *, config: TicketValidationConfig | None = None) -> None:
    """Validate a ticket payload before writing it.

    This is deliberately conservative. The ticket layer must never turn a research row
    into an executable broker order, and it must re-check account constraints even when
    the row comes from account simulation.
    """
    cfg = config or TicketValidationConfig()
    forbidden = FORBIDDEN_LIVE_ORDER_FIELDS.intersection(payload)
    if forbidden:
        raise ValueError(f"ticket payload contains forbidden live-order fields: {sorted(forbidden)}")

    missing = REQUIRED_TICKET_FIELDS - set(payload)
    if missing:
        raise ValueError(f"ticket payload missing fields: {sorted(missing)}")

    if str(payload.get("status")) != "accepted_trade":
        raise ValueError("only accepted_trade payloads can generate tickets")

    setup_id = str(payload.get("setup_id"))
    if setup_id != cfg.setup_id:
        raise ValueError(f"unexpected setup_id: {setup_id}; expected {cfg.setup_id}")

    entry_price = _num_required(payload, "entry_price")
    stop_price = _num_required(payload, "stop_price")
    target_price = _num_required(payload, "target_price")
    shares_value = _num_required(payload, "shares")
    if not shares_value.is_integer():
        raise ValueError("shares must be an integer")
    shares = int(shares_value)
    planned_loss = _num_required(payload, "planned_loss")
    capital_used = _num_required(payload, "capital_used")
    per_share_risk = _num_required(payload, "per_share_risk")

    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if stop_price <= 0:
        raise ValueError("stop_price must be positive")
    if target_price <= entry_price:
        raise ValueError("target_price must be greater than entry_price")
    if stop_price >= entry_price:
        raise ValueError("stop_price must be below entry_price")
    if shares <= 0:
        raise ValueError("shares must be positive")
    if cfg.lot_size > 0 and shares % cfg.lot_size != 0:
        raise ValueError(f"shares must be a multiple of lot_size={cfg.lot_size}")
    if entry_price * cfg.lot_size > cfg.max_one_lot_value + 1e-9:
        raise ValueError("one lot value exceeds max_one_lot_value")
    if capital_used > cfg.capital_limit + 1e-9:
        raise ValueError("capital_used exceeds account capital_limit")
    if planned_loss > cfg.max_planned_loss_per_trade + 1e-9:
        raise ValueError("planned_loss exceeds max_planned_loss_per_trade")

    expected_risk = entry_price - stop_price
    if not _close(per_share_risk, expected_risk):
        raise ValueError("per_share_risk does not match entry_price - stop_price")
    if not _close(capital_used, entry_price * shares):
        raise ValueError("capital_used does not match entry_price * shares")
    if not _close(planned_loss, expected_risk * shares):
        raise ValueError("planned_loss does not match per_share_risk * shares")

    entry_date = payload.get("entry_date")
    if entry_date is None or str(entry_date).strip() == "":
        raise ValueError("entry_date is required")


def validate_account_sim_row_for_ticket(
    row: pd.Series | dict[str, Any],
    *,
    config: TicketValidationConfig | None = None,
) -> dict[str, Any]:
    """Convert an accepted account-simulation row to a validated ticket payload."""
    data = dict(row)
    if str(data.get("status")) != "accepted_trade":
        raise ValueError("only accepted_trade rows can generate formal tickets")

    payload = {
        "ticket_id": data.get("ticket_id") or _make_ticket_id(data),
        "simulation_id": data.get("simulation_id"),
        "trade_id": data.get("trade_id"),
        "setup_id": data.get("setup_id") or (config.setup_id if config else "STOCK_RS_PULLBACK_v1"),
        "code": data.get("code"),
        "name": data.get("name") or "",
        "entry_date": _date_str(data.get("entry_date")),
        "exit_date": _date_str(data.get("exit_date")),
        "entry_price": _num_required(data, "entry_price"),
        "stop_price": _num_required(data, "stop_price"),
        "target_price": _num_required(data, "target_price"),
        "shares": int(_num_required(data, "shares")),
        "planned_loss": _num_required(data, "planned_loss"),
        "capital_used": _num_required(data, "capital_used"),
        "per_share_risk": _num_required(data, "per_share_risk"),
        "status": data.get("status"),
        "exit_reason": data.get("exit_reason"),
        "net_return": _num_optional(data.get("net_return")),
        "r_multiple": _num_optional(data.get("r_multiple")),
        "pnl": _num_optional(data.get("pnl")),
        "order_permission": "manual_review_only_no_auto_order",
    }
    validate_ticket_payload(payload, config=config)
    return payload


def _make_ticket_id(data: dict[str, Any]) -> str:
    base = str(data.get("simulation_id") or data.get("trade_id") or f"{data.get('code')}-{data.get('entry_date')}")
    safe = "".join(ch if ch.isalnum() else "-" for ch in base).strip("-")
    return f"TICKET-{safe}"


def _num_required(data: dict[str, Any], key: str) -> float:
    out = _num_optional(data.get(key))
    if out is None:
        raise ValueError(f"{key} is required and must be numeric")
    return out


def _num_optional(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return None
        return out
    except (TypeError, ValueError):
        return None


def _date_str(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d")


def _close(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
