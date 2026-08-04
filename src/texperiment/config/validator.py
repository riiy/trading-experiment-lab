from __future__ import annotations

from texperiment.exceptions import ConfigError
import pandas as pd

def validate_global_account_config(config: dict) -> None:
    account = config.get("account", {})
    risk = config.get("risk", {})
    if account.get("capital_limit") != 30000:
        raise ConfigError("account.capital_limit must be 30000")
    if account.get("trading_allowed") is not False:
        raise ConfigError("account.trading_allowed must be false before validation passes")
    if risk.get("max_planned_loss_per_trade") != 500:
        raise ConfigError("risk.max_planned_loss_per_trade must be 500")
    if risk.get("max_positions") != 1:
        raise ConfigError("risk.max_positions must be 1")


def validate_setup_config(config: dict) -> None:
    setup_id = config.get("setup_id")
    if setup_id not in {"STOCK_RS_PULLBACK_v1", "VOLATILITY_CONTRACTION_BREAKOUT_v1"}:
        raise ConfigError("unsupported setup_id")
    if config.get("trading_allowed") is not False:
        raise ConfigError("setup.trading_allowed must be false before validation passes")
    thresholds = config.get("validation_threshold", {})
    required = [
        "min_valid_trades",
        "mean_net_return_gt",
        "median_net_return_gte",
        "profit_factor_gt",
        "best_3_removed_mean_gte",
        "top3_contribution_ratio_lte",
    ]
    missing = [k for k in required if k not in thresholds]
    if missing:
        raise ConfigError(f"validation_threshold missing keys: {missing}")
    if setup_id == "VOLATILITY_CONTRACTION_BREAKOUT_v1":
        _validate_volatility_contraction_breakout(config)
        return
    window = config.get("validation_window", {})
    required_window = ["start_date", "end_date", "indicator_warmup_trading_days", "indicator_warmup_start_date"]
    missing_window = [key for key in required_window if key not in window]
    if missing_window:
        raise ConfigError(f"validation_window missing keys: {missing_window}")
    start = pd.Timestamp(window["start_date"])
    end = pd.Timestamp(window["end_date"])
    if start > end:
        raise ConfigError("validation_window.start_date must not be after end_date")
    if int(window["indicator_warmup_trading_days"]) < 60:
        raise ConfigError("validation_window.indicator_warmup_trading_days must be at least 60")
    if pd.Timestamp(window["indicator_warmup_start_date"]) > start:
        raise ConfigError("validation_window.indicator_warmup_start_date must not be after start_date")
    excluded = config.get("universe", {}).get("data_quality_excluded_codes", [])
    if len(excluded) != 21 or len(set(excluded)) != 21:
        raise ConfigError("universe.data_quality_excluded_codes must contain 21 unique codes")


def _validate_volatility_contraction_breakout(config: dict) -> None:
    development = config.get("development_window", {})
    final = config.get("final_validation_window", {})
    if development.get("start_date") != "2016-07-17" or development.get("end_date") != "2022-07-15":
        raise ConfigError("VCB development window must remain fixed")
    if final.get("start_date") != "2022-07-18" or final.get("end_date") != "2026-07-17":
        raise ConfigError("VCB final validation window must remain fixed")
    if final.get("one_time_only") is not True:
        raise ConfigError("VCB final validation must be one_time_only")
    if config.get("benchmark", {}).get("code") != "000300.SH" or config.get("benchmark", {}).get("return_basis") != "price_index":
        raise ConfigError("VCB benchmark must be 000300.SH price_index")
    thresholds = config.get("validation_threshold", {})
    required = ["min_valid_trades", "mean_net_return_gt", "median_net_return_gte", "profit_factor_gt", "best_3_removed_mean_gte", "top3_contribution_ratio_lte", "account_cagr_floor", "benchmark_cagr_spread", "account_max_drawdown_lte"]
    missing = [key for key in required if key not in thresholds]
    if missing:
        raise ConfigError(f"VCB validation_threshold missing keys: {missing}")
    if float(thresholds["account_cagr_floor"]) != 0.07 or float(thresholds["benchmark_cagr_spread"]) != 0.03 or float(thresholds["account_max_drawdown_lte"]) != 0.10:
        raise ConfigError("VCB account thresholds must remain fixed")
    if config.get("universe", {}).get("exclude_st") is not False:
        raise ConfigError("VCB must ignore historical ST in the universe")
    if config.get("execution", {}).get("historical_st_policy") != "IGNORE_HISTORICAL_ST_ORDINARY_LIMITS_V1":
        raise ConfigError("VCB must ignore historical ST in execution")
    excluded = config.get("universe", {}).get("data_quality_excluded_codes", [])
    if len(excluded) != 21 or len(set(excluded)) != 21:
        raise ConfigError("universe.data_quality_excluded_codes must contain 21 unique codes")
