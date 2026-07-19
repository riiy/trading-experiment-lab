from pathlib import Path

import pytest

from texperiment.cli import _assert_full_recalculation_allowed, _assert_recalculated_paths
from texperiment.config.loader import load_yaml
from texperiment.config.validator import validate_global_account_config, validate_setup_config

ROOT = Path(__file__).resolve().parents[1]


def test_global_account_config_valid():
    config = load_yaml(ROOT / "configs" / "global_account.yaml")
    validate_global_account_config(config)


def test_setup_config_valid():
    config = load_yaml(ROOT / "configs" / "setups" / "STOCK_RS_PULLBACK_v1.yaml")
    validate_setup_config(config)


def test_registry_requires_full_pipeline_recalculation_v2_implementation():
    registry = load_yaml(ROOT / "experiment_registry.yaml")
    experiment = registry["Trading_Experiment"]
    archived = registry["setups"]["STOCK_RS_PULLBACK_v1"]

    assert experiment["status"] == "recalculation_implementation_required"
    assert experiment["current_setup"] is None
    assert experiment["tradable_setups"] == 0
    assert experiment["trading_allowed"] is False
    assert experiment["account_simulation_allowed"] is False
    assert experiment["ticket_generation_allowed"] is False
    assert archived["lifecycle_status"] == "ARCHIVED_NON_TRADABLE"
    assert archived["validation_status"] == "INVALIDATED_BY_ENGINE_ERROR"
    assert archived["trading_allowed"] is False
    assert archived["account_simulation_allowed"] is False
    assert archived["ticket_generation_allowed"] is False
    assert archived["original_validation"]["preserved"] is True
    assert archived["original_validation"]["status"] == "INVALIDATED_BY_ENGINE_ERROR"
    assert archived["audit"]["status"] == "CLOSED"
    assert archived["audit"]["decision"] == "ENGINE_ERROR_FOUND"
    assert archived["audit"]["locked_commit"] == "1cbfa676459e31075c479826cb68dc58b3beeec8"
    assert archived["audit"]["full_recalculation_performed"] is False
    assert archived["audit"]["new_setup_started"] is False

    remediation = registry["engine_remediation_tasks"]["ENGINE_REMEDIATION_A_SHARE_EXECUTION_v1"]
    assert remediation["status"] == "REMEDIATION_AUDIT_PASSED_V1_RECALCULATION_REVOKED"
    assert remediation["is_new_setup"] is False
    assert remediation["full_recalculation_allowed"] is False
    assert remediation["historical_st_remediation_deferred"] is True
    assert remediation["sample_audit"]["decision"] == "REMEDIATION_AUDIT_PASSED"
    assert remediation["sample_audit"]["original_limit_up_errors_resolved"] == 5
    assert remediation["sample_audit"]["remediated_valid_trades"] == 50
    assert remediation["sample_audit"]["material_blocking_trade_count"] == 0
    assert remediation["sample_audit"]["historical_st_point_overrides"] == 2
    assert remediation["sample_audit"]["full_recalculation_performed"] is False

    implementation = registry["full_pipeline_recalculation_tasks"]["FULL_PIPELINE_RECALCULATION_IMPLEMENTATION_v2"]
    assert implementation["status"] == "implementation_error_found"
    assert implementation["baseline_commit"] == "468bacc6fead27020e2dfce5f33368a623492122"
    assert implementation["implementation_frozen"] is False
    assert implementation["implementation_audited"] is False
    assert implementation["implementation_audit_decision"] == "IMPLEMENTATION_ERROR_FOUND"
    assert implementation["contract_defined"] is True
    assert implementation["orchestration_skeleton_ready"] is True
    assert implementation["concrete_stages_implemented"] is True
    assert implementation["upstream_stages_implemented"] == [
        "INPUT_SNAPSHOT",
        "MARKET_STATE_REBUILD",
        "UNIVERSE_REBUILD",
        "INDICATOR_REBUILD",
    ]
    assert implementation["downstream_stages_implemented"] == [
        "SIGNAL_REBUILD",
        "TRADE_REBUILD",
        "METRICS_REBUILD",
        "DELTA_AND_DECISION",
    ]
    assert implementation["full_recalculation_allowed"] is False
    assert implementation["full_recalculation_performed"] is False


def test_full_recalculation_is_blocked_during_v2_implementation():
    with pytest.raises(SystemExit, match="V2 implementation audit"):
        _assert_full_recalculation_allowed(ROOT)


def test_recalculation_cannot_overwrite_original_paths():
    with pytest.raises(SystemExit, match="must use STOCK_RS_PULLBACK_v1_RECALCULATED"):
        _assert_recalculated_paths("data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv")
    _assert_recalculated_paths("data/trades/STOCK_RS_PULLBACK_v1_RECALCULATED_trades.csv")
