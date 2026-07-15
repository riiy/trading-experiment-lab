from __future__ import annotations

from texperiment.exceptions import PermissionDenied


def assert_trading_disabled(registry: dict) -> None:
    exp = registry.get("Trading_Experiment", {})
    if exp.get("trading_allowed") is not False:
        raise PermissionDenied("Trading must remain disabled before validation and account simulation pass")


def assert_can_generate_formal_ticket(setup_status: str, account_sim_status: str) -> None:
    if setup_status != "validation_passed":
        raise PermissionDenied("Setup validation has not passed")
    if account_sim_status != "passed":
        raise PermissionDenied("Account simulation has not passed")
