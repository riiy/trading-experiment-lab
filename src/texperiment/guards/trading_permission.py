from __future__ import annotations

from texperiment.exceptions import PermissionDenied
from texperiment.guards.setup_status import is_archived


def assert_trading_disabled(registry: dict) -> None:
    exp = registry.get("Trading_Experiment", {})
    if exp.get("trading_allowed") is not False:
        raise PermissionDenied("Trading must remain disabled before validation and account simulation pass")


def assert_can_generate_formal_ticket(setup_status: str, account_sim_status: str) -> None:
    if is_archived(setup_status):
        raise PermissionDenied(f"Formal ticket generation blocked for archived setup: {setup_status}")
    if setup_status != "validation_passed":
        raise PermissionDenied("Setup validation has not passed")
    if account_sim_status != "passed":
        raise PermissionDenied("Account simulation has not passed")
