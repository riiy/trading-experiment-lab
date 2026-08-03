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

    assert experiment["status"] == "new_strategy_discovery_active_no_trade"
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

    setup = load_yaml(ROOT / "configs" / "setups" / "STOCK_RS_PULLBACK_v1.yaml")
    assert setup["validation_window"] == {
        "start_date": "2016-07-17",
        "end_date": "2026-07-17",
        "indicator_warmup_trading_days": 60,
        "indicator_warmup_start_date": "2016-04-20",
        "exclusion_reason": "DATA_QUALITY_RAW_QFQ_MAPPING_AMBIGUITY",
    }
    assert len(setup["universe"]["data_quality_excluded_codes"]) == 21
    assert "000564.SZ" in setup["universe"]["data_quality_excluded_codes"]

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
    assert implementation["status"] == "recalculation_engine_audited"
    assert implementation["baseline_commit"] == "468bacc6fead27020e2dfce5f33368a623492122"
    assert implementation["implementation_commit"] == "508ceceafcfc4403bab746051f26d6ff23e78a9c"
    assert implementation["prior_implementation_commit"] == "a68770e151238fbf1b8f0050808cc877973dfd13"
    assert implementation["implementation_frozen"] is True
    assert implementation["implementation_audited"] is True
    assert implementation["implementation_audit_decision"] == "IMPLEMENTATION_AUDIT_PASSED"
    assert implementation["historical_implementation_audit_decision"] == "IMPLEMENTATION_AUDIT_PASSED"
    assert implementation["remediation_1"]["status"] == "completed_pending_reaudit"
    assert implementation["reaudit_1"]["decision"] == "IMPLEMENTATION_ERROR_FOUND"
    assert implementation["remediation_2"]["status"] == "completed_pending_reaudit"
    assert implementation["reaudit_2"]["decision"] == "IMPLEMENTATION_AUDIT_PASSED"
    assert implementation["remediation_3"]["status"] == "implementation_audit_passed"
    assert implementation["remediation_3"]["implementation_commit"] == "508ceceafcfc4403bab746051f26d6ff23e78a9c"
    assert implementation["remediation_3"]["audit"]["decision"] == "IMPLEMENTATION_AUDIT_PASSED"
    assert implementation["remediation_3"]["blocking_error"] == "ATOMIC_PUBLICATION_SEAL_ORDER_INCOMPATIBLE"
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
    assert implementation["engine_full_recalculation_capable"] is True
    assert implementation["formal_recalculation_run_authorized"] is False
    assert implementation["full_recalculation_allowed"] is False
    assert implementation["full_recalculation_performed"] is False

    manifest_task = registry["full_pipeline_recalculation_tasks"]["FULL_PIPELINE_RECALCULATION_MANIFEST_V2_IMPLEMENTATION"]
    assert manifest_task["status"] == "formal_run_authorized"
    assert manifest_task["manifest_tool_commit"] == "b25fe28c9f57d1ae65f9c3990a9480e55eac454c"
    assert manifest_task["manifest_tool_audit_record_commit"] == "58027cee8a554568730f6840c2e0f760b64fe8e1"
    assert manifest_task["manifest_v2_implemented"] is True
    assert manifest_task["manifest_v2_audited"] is True
    assert manifest_task["manifest_v2_audit_decision"] == "MANIFEST_V2_AUDIT_PASSED"
    assert manifest_task["historical_audit_decision"] == "MANIFEST_V2_AUDIT_PASSED"
    assert manifest_task["formal_contract_eligible"] is True
    assert manifest_task["manifest_freeze_authorized"] is True
    assert manifest_task["formal_recalculation_run_authorized"] is True
    assert manifest_task["legacy_freezer_run_type"] == "SIGNAL_EXECUTION_REPLAY"
    assert manifest_task["remediation_1"]["implementation_commit"] == "30523f4"
    assert manifest_task["remediation_1"]["status"] == "implementation_pending_reaudit"
    assert manifest_task["remediation_1"]["reaudit_1"]["decision"] == "MANIFEST_V2_AUDIT_INCONCLUSIVE"
    assert manifest_task["remediation_1"]["reaudit_2"]["decision"] == "MANIFEST_V2_AUDIT_PASSED"
    assert manifest_task["remediation_1"]["reaudit_3"]["decision"] == "MANIFEST_V2_AUDIT_PASSED"
    assert manifest_task["last_freeze_attempt"]["formal_manifest_generated"] is False
    comparison_archive = manifest_task["comparison_archive_completion_v1"]
    assert comparison_archive["decision"] == "ORIGINAL_METRICS_ARCHIVE_AUDIT_PASSED"
    assert comparison_archive["strategy_decision_generated"] is False
    assert comparison_archive["formal_recalculation_performed"] is False

    core_inputs = registry["full_pipeline_recalculation_tasks"][
        "STOCK_RS_PULLBACK_v1_CORE_INPUT_SNAPSHOT_PREPARATION"
    ]
    assert core_inputs["status"] == "FORMAL_INPUT_FROZEN_PENDING_FORMAL_MANIFEST_FREEZE"
    assert core_inputs["raw_daily_available"] is True
    assert core_inputs["qfq_pairing_verified"] is True
    assert core_inputs["input_hashes_frozen"] is True
    assert core_inputs["blocking_mismatches"] == 3306
    candidate = core_inputs["generation_attempt_3"]
    assert candidate["status"] == "FORMAL_INPUT_FROZEN_PENDING_FORMAL_MANIFEST_FREEZE"
    assert candidate["rows"] == 10249283
    assert candidate["min_date"] == "2016-04-20"
    assert candidate["max_date"] == "2026-07-17"
    assert candidate["unevaluable_mapping_rows"] == 0
    assert candidate["audit"]["decision"] == "CORE_INPUT_PAIR_AUDIT_PASSED"
    assert candidate["formal_input_published"] is False
    freezer = core_inputs["formal_input_freeze_v1"]
    assert freezer["audit_decision"] == "FORMAL_CORE_INPUT_FREEZE_AUDIT_PASSED"
    assert freezer["formal_input_freeze_authorized"] is True
    assert freezer["formal_recalculation_run_authorized"] is False
    assert freezer["formal_input_published"] is True
    scope = core_inputs["recent_10y_validation_scope_v1"]
    assert scope["status"] == "audit_passed"
    assert scope["audit_decision"] == "RECENT_10Y_VALIDATION_SCOPE_AUDIT_PASSED"
    assert scope["regression_record_commit"] == "3f2075b"
    assert scope["validation_window"]["start_date"] == "2016-07-17"
    assert scope["validation_window"]["end_date"] == "2026-07-17"
    assert scope["validation_window"]["indicator_warmup_trading_days"] == 60
    assert scope["validation_window"]["indicator_warmup_start_date"] == "2016-04-20"
    assert scope["mapping_ambiguity"] == {
        "historical_rows": 3306,
        "in_window_rows": 30,
        "excluded_codes": 21,
    }
    assert scope["formal_input_published"] is False
    amendment = scope["global_warmup_boundary_amendment_v1"]
    assert amendment["status"] == "audit_passed"
    assert amendment["implementation_commit"] == "1dedf1e"
    assert amendment["audit_decision"] == "RECENT_10Y_VALIDATION_SCOPE_REAUDIT_PASSED"
    assert amendment["benchmark_derived_warmup_start_date"] == "2016-04-20"
    assert amendment["formal_input_published"] is False
    assert core_inputs["remediation_1"]["status"] == "implementation_error_found"
    assert core_inputs["remediation_1"]["implementation_commit"] == "84315e86ee48cc302a3c0512a988fb30adb0e7f1"
    assert core_inputs["remediation_1"]["audit"]["decision"] == "DATA_PAIR_IMPLEMENTATION_ERROR_FOUND"
    assert core_inputs["remediation_2"]["status"] == "implementation_audit_passed"
    assert core_inputs["remediation_2"]["implementation_commit"] == "f90bd7c863b635f942b89fa60e1955ae8a112c9c"
    assert core_inputs["remediation_2"]["audit"]["decision"] == "DATA_PAIR_IMPLEMENTATION_AUDIT_PASSED"
    assert core_inputs["generation_attempt_1"]["blocking_error"] == "FULLY_FILTERED_SECURITY_EMPTY_SPLIT_INDEX_ERROR"
    assert core_inputs["generation_attempt_1"]["formal_input_published"] is False
    assert core_inputs["remediation_3"]["status"] == "implementation_audit_passed"
    assert core_inputs["remediation_3"]["implementation_commit"] == "7faf0784eece73021dd7e58ec3de9136658276d4"
    assert core_inputs["remediation_3"]["audit"]["decision"] == "DATA_PAIR_IMPLEMENTATION_AUDIT_PASSED"
    assert core_inputs["generation_attempt_2"]["blocking_error"] == "RAW_QFQ_MAPPING_NOT_EVALUABLE"
    assert core_inputs["generation_attempt_2"]["source_raw_only_keys"] == 0
    assert core_inputs["generation_attempt_2"]["source_qfq_only_keys"] == 0
    assert core_inputs["generation_attempt_2"]["unevaluable_mapping_rows"] == 3306
    assert core_inputs["generation_attempt_2"]["formal_input_published"] is False
    diagnostics = core_inputs["mapping_unevaluable_diagnostics_v1"]
    assert diagnostics["status"] == "completed"
    assert diagnostics["decision"] == "MAPPING_DIAGNOSTICS_MIXED_DETERMINISTIC"
    assert diagnostics["retained_rows"] == 15925710
    assert diagnostics["unevaluable_rows"] == 3306
    assert diagnostics["qfq_flat_ohlc_rows"] == 3306
    assert diagnostics["raw_flat_ohlc_rows"] == 0
    assert diagnostics["formal_input_published"] is False
    rounding_v2 = core_inputs["rounding_interval_mapping_v2"]
    assert rounding_v2["status"] == "implementation_active"
    assert rounding_v2["implementation_commit"] == "7840726"
    assert rounding_v2["candidate_generation_allowed"] is False
    assert rounding_v2["implementation_audited"] is False
    assert rounding_v2["requirements"]["target_rows"] == 3306
    assert rounding_v2["requirements"]["price_values_transformed"] is False
    assert rounding_v2["requirements"]["rows_silently_dropped"] == 0
    assert rounding_v2["requirements"]["global_tolerance_changed"] is False
    assert rounding_v2["requirements"]["security_specific_hardcodes"] == 0
    assert rounding_v2["preflight"]["status"] == "completed"
    assert rounding_v2["preflight"]["evidence_commit"] == "d7251c6"
    assert rounding_v2["preflight"]["interval_feasible_rows"] == 3306
    assert rounding_v2["preflight"]["set_valued_rows"] == 3306
    assert rounding_v2["preflight"]["unbounded_affine_slope_rows"] == 90
    assert rounding_v2["preflight"]["execution_referenced_rows"] == 0


def test_full_recalculation_is_blocked_until_manifest_v2_audit():
    with pytest.raises(SystemExit, match="V2 implementation audit"):
        _assert_full_recalculation_allowed(ROOT)


def test_recalculation_cannot_overwrite_original_paths():
    with pytest.raises(SystemExit, match="must use STOCK_RS_PULLBACK_v1_RECALCULATED"):
        _assert_recalculated_paths("data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv")
    _assert_recalculated_paths("data/trades/STOCK_RS_PULLBACK_v1_RECALCULATED_trades.csv")
