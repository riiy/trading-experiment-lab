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
    if config.get("setup_id") != "STOCK_RS_PULLBACK_v1":
        raise ConfigError("setup_id must be STOCK_RS_PULLBACK_v1 during audit planning")
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
