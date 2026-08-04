from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from texperiment.backtest.execution_model import can_buy_at_open, can_sell_on_bar, has_valid_adjusted_ohlc, has_valid_ohlc, price_transform
from texperiment.backtest.trade_builder import TRADE_OUTPUT_COLUMNS
from texperiment.setups.volatility_contraction_breakout_v1.execution import (
    ST_IGNORED_EXECUTION_POLICY,
    rebuild_execution_without_historical_st,
)


@dataclass(frozen=True)
class VolatilityContractionBreakoutBacktestConfig:
    setup_id: str = "VOLATILITY_CONTRACTION_BREAKOUT_v1"
    stop_atr_multiple: float = 2.0
    low_exit_window: int = 10
    max_holding_days: int = 20
    intraday_priority: str = "stop_first"
    historical_st_policy: str = ST_IGNORED_EXECUTION_POLICY

    @classmethod
    def from_setup_config(cls, setup_config: dict[str, Any] | None = None) -> "VolatilityContractionBreakoutBacktestConfig":
        cfg = setup_config or {}
        signal, exit_cfg = cfg.get("signal", {}), cfg.get("exit", {})
        return cls(
            setup_id=str(cfg.get("setup_id", cls.setup_id)),
            stop_atr_multiple=float(exit_cfg.get("initial_stop_atr_multiple", 2.0)),
            low_exit_window=int(exit_cfg.get("close_break_low_window", 10)),
            max_holding_days=int(exit_cfg.get("max_holding_days", 20)),
            intraday_priority=str(exit_cfg.get("intraday_priority", "stop_first")),
            historical_st_policy=str(cfg.get("execution", {}).get("historical_st_policy", ST_IGNORED_EXECUTION_POLICY)),
        )


def run_volatility_contraction_breakout_backtest(
    signals: pd.DataFrame, daily_bars: pd.DataFrame, *, setup_config: dict[str, Any] | None = None
) -> pd.DataFrame:
    """Backtest VCB signals with raw fills and qfq stop/structure mapping.

    It deliberately performs no cost deduction: the daily account ledger applies
    actual commission, stamp duty, and slippage exactly once.
    """
    cfg = VolatilityContractionBreakoutBacktestConfig.from_setup_config(setup_config)
    if cfg.historical_st_policy != ST_IGNORED_EXECUTION_POLICY:
        raise ValueError("VCB requires IGNORE_HISTORICAL_ST_ORDINARY_LIMITS_V1 execution policy")
    bars = _prepare_bars(daily_bars, rebuild_execution=True)
    signals = _prepare_signals(signals)
    by_code = {code: frame.reset_index(drop=True) for code, frame in bars.groupby("code", sort=False)}
    rows = [_backtest_one(signal._asdict(), by_code.get(signal.code), cfg) for signal in signals.itertuples(index=False)]
    out = pd.DataFrame(rows)
    for col in TRADE_OUTPUT_COLUMNS:
        if col not in out:
            out[col] = pd.NA
    return out[TRADE_OUTPUT_COLUMNS].sort_values(["code", "signal_date", "trade_id"]).reset_index(drop=True)


def _backtest_one(signal: dict[str, Any], bars: pd.DataFrame | None, cfg: VolatilityContractionBreakoutBacktestConfig) -> dict[str, Any]:
    base = _base(signal, cfg)
    if bars is None or bars.empty:
        return _invalid(base, "invalid_no_next_open")
    signal_date = pd.Timestamp(signal["signal_date"])
    horizon = bars.loc[bars["date"] > signal_date].copy().reset_index(drop=True)
    if horizon.empty:
        return _invalid(base, "invalid_no_next_open")
    entry_bar = horizon.iloc[0].to_dict()
    buyable, reason = can_buy_at_open(entry_bar)
    if not buyable:
        return _invalid(base, reason or "invalid_no_next_open", entry_bar)
    entry_transform = price_transform(entry_bar)
    assert entry_transform is not None
    entry_factor, entry_offset = entry_transform
    entry_raw, entry_adj = float(entry_bar["raw_open"]), float(entry_bar["adj_open"])
    atr = _num(signal.get("atr10"))
    if atr is None or atr <= 0:
        return _invalid(base, "invalid_missing_atr", entry_bar)
    stop_adj = entry_adj - cfg.stop_atr_multiple * atr
    stop_raw = (stop_adj - entry_offset) / entry_factor
    if stop_adj <= 0 or stop_adj >= entry_adj:
        return _invalid({**base, "stop_price": stop_raw, "stop_adjusted_price": stop_adj}, "invalid_stop_not_below_entry", entry_bar)

    context = {**base, "entry_date": _date(entry_bar["date"]), "entry_price": entry_raw, "entry_adjusted_price": entry_adj,
               "entry_adj_factor": entry_factor, "entry_adj_offset": entry_offset, "stop_price": stop_raw, "stop_adjusted_price": stop_adj}
    pending_open_exit: str | None = None
    for i, (_, bar_row) in enumerate(horizon.iterrows(), start=1):
        bar = bar_row.to_dict()
        transform = price_transform(bar)
        if not has_valid_ohlc(bar) or not has_valid_adjusted_ohlc(bar) or transform is None:
            return _invalid(context, "invalid_inconsistent_price_layers")
        factor, offset = transform
        can_exit = i > 1  # A-share T+1
        if can_exit and pending_open_exit is not None:
            sellable, sell_reason = can_sell_on_bar(bar, execution="open")
            if sellable is None:
                return _invalid(context, sell_reason or "invalid_exit_fillability_unknown")
            if sellable:
                return _trade(context, signal, bar, float(bar["raw_open"]), pending_open_exit, i, factor, offset)

        if can_exit and float(bar["adj_low"]) <= stop_adj:
            sellable, sell_reason = can_sell_on_bar(bar, execution="intraday")
            if sellable is None:
                return _invalid(context, sell_reason or "invalid_exit_fillability_unknown")
            if sellable:
                price = float(bar["raw_open"]) if float(bar["adj_open"]) <= stop_adj else (stop_adj - offset) / factor
                return _trade(context, signal, bar, price, "stop_loss", i, factor, offset)
            pending_open_exit = "stop_loss"
            continue

        # A close breach schedules the *next* open; the low window excludes today.
        previous = bars.loc[bars["date"] < bar["date"], "adj_low"].tail(cfg.low_exit_window)
        if len(previous) == cfg.low_exit_window and float(bar["adj_close"]) < float(previous.min()):
            pending_open_exit = pending_open_exit or "close_break_10d_low"

        if i >= cfg.max_holding_days:
            sellable, sell_reason = can_sell_on_bar(bar, execution="close")
            if sellable is None:
                return _invalid(context, sell_reason or "invalid_exit_fillability_unknown")
            if sellable:
                return _trade(context, signal, bar, float(bar["raw_close"]), "max_holding_exit", i, factor, offset)
            pending_open_exit = pending_open_exit or "max_holding_exit"
    return _invalid(context, "invalid_no_exit_data")


def _trade(context: dict[str, Any], signal: dict[str, Any], bar: dict[str, Any], exit_raw: float, reason: str, holding: int, factor: float, offset: float) -> dict[str, Any]:
    entry_raw = float(context["entry_price"])
    entry_adj = float(context["entry_adjusted_price"])
    exit_adj = exit_raw * factor + offset
    risk = entry_adj - float(context["stop_adjusted_price"])
    date = _date(bar["date"])
    return {**context, "trade_id": f"{context['setup_id']}:{context['code']}:{context['signal_date']}:{context['entry_date']}",
            "exit_date": date, "exit_price": exit_raw, "exit_adjusted_price": exit_adj, "exit_adj_factor": factor, "exit_adj_offset": offset,
            "exit_reason": reason, "gross_return": exit_raw / entry_raw - 1.0, "net_return": exit_raw / entry_raw - 1.0,
            "r_multiple": (exit_adj - entry_adj) / risk, "holding_days": holding, "round_trip_cost": 0.0,
            "status": "valid_trade", "invalid_reason": None}


def _prepare_signals(signals: pd.DataFrame) -> pd.DataFrame:
    required = {"signal_id", "code", "signal_date", "status", "atr10"}
    missing = sorted(required - set(signals.columns))
    if missing:
        raise ValueError(f"signals missing required columns: {missing}")
    out = signals.loc[signals["status"] == "triggered_entry_next_open"].copy()
    out["signal_date"] = pd.to_datetime(out["signal_date"], errors="coerce").dt.normalize()
    out["code"] = out["code"].astype(str)
    return out.dropna(subset=["signal_date"]).drop_duplicates("signal_id", keep="last")


def _prepare_bars(daily_bars: pd.DataFrame, *, rebuild_execution: bool) -> pd.DataFrame:
    required = {"date", "code", "raw_open", "raw_high", "raw_low", "raw_close", "adj_open", "adj_high", "adj_low", "adj_close", "adj_factor", "adj_offset", "is_suspended"}
    missing = sorted(required - set(daily_bars.columns))
    if missing:
        raise ValueError(f"daily_bars missing required columns: {missing}")
    out = daily_bars.copy(); out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize(); out["code"] = out["code"].astype(str)
    for col in [c for c in required if c.startswith("raw_") or c.startswith("adj_")]: out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.drop_duplicates(["date", "code"], keep="last").sort_values(["code", "date"]).reset_index(drop=True)
    return rebuild_execution_without_historical_st(out) if rebuild_execution else out


def _base(signal: dict[str, Any], cfg: VolatilityContractionBreakoutBacktestConfig) -> dict[str, Any]:
    date = _date(signal.get("signal_date"))
    return {"trade_id": f"{cfg.setup_id}:{signal.get('code')}:{date}:invalid", "signal_id": signal.get("signal_id"), "setup_id": signal.get("setup_id") or cfg.setup_id, "code": str(signal.get("code")), "name": signal.get("name"), "signal_date": date, "pullback_date": date, "trigger_date": date, "entry_date": None, "entry_price": None, "entry_adjusted_price": None, "entry_adj_factor": None, "entry_adj_offset": None, "stop_price": None, "stop_adjusted_price": None, "target_price": None, "target_adjusted_price": None, "exit_date": None, "exit_price": None, "exit_adjusted_price": None, "exit_adj_factor": None, "exit_adj_offset": None, "exit_reason": None, "gross_return": None, "net_return": None, "r_multiple": None, "holding_days": None, "round_trip_cost": 0.0, "status": "invalid_trade", "invalid_reason": None}


def _invalid(base: dict[str, Any], reason: str, entry_bar: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {**base, "status": "invalid_trade", "invalid_reason": reason}
    if entry_bar is not None:
        out.update({"entry_date": _date(entry_bar.get("date")), "entry_price": _num(entry_bar.get("raw_open")), "entry_adjusted_price": _num(entry_bar.get("adj_open")), "entry_adj_factor": _num(entry_bar.get("adj_factor")), "entry_adj_offset": _num(entry_bar.get("adj_offset"))})
    return out


def _date(value: Any) -> str:
    return "" if value is None or pd.isna(value) else str(pd.Timestamp(value).date())


def _num(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)
