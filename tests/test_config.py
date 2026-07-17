from pathlib import Path

from texperiment.config.loader import load_yaml
from texperiment.config.validator import validate_global_account_config, validate_setup_config

ROOT = Path(__file__).resolve().parents[1]


def test_global_account_config_valid():
    config = load_yaml(ROOT / "configs" / "global_account.yaml")
    validate_global_account_config(config)


def test_setup_config_valid():
    config = load_yaml(ROOT / "configs" / "setups" / "STOCK_RS_PULLBACK_v1.yaml")
    validate_setup_config(config)


def test_registry_is_closed_after_audit():
    registry = load_yaml(ROOT / "experiment_registry.yaml")
    experiment = registry["Trading_Experiment"]
    archived = registry["setups"]["STOCK_RS_PULLBACK_v1"]

    assert experiment["status"] == "audit_closed"
    assert experiment["current_setup"] is None
    assert experiment["tradable_setups"] == 0
    assert experiment["trading_allowed"] is False
    assert archived["status"] == "FAILED_ARCHIVED"
    assert archived["account_simulation_allowed"] is False
    assert archived["ticket_generation_allowed"] is False
    assert archived["audit"]["status"] == "CLOSED"
    assert archived["audit"]["decision"] == "ENGINE_ERROR_FOUND"
    assert archived["audit"]["recalculation_performed"] is False
    assert archived["audit"]["new_setup_started"] is False
