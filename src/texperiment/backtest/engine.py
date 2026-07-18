from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from texperiment.backtest.cost import apply_round_trip_cost
from texperiment.backtest.execution_model import (
    can_buy_at_open,
    can_sell_on_bar,
    has_valid_adjusted_ohlc,
    has_valid_ohlc,
    price_transform,
)
from texperiment.backtest.trade_builder import TRADE_OUTPUT_COLUMNS


@dataclass(frozen=True)
class StockRSPullbackBacktestConfig:
    setup_id: str = "STOCK_RS_PULLBACK_v1"
    entry_execution: str = "next_day_open"
    target_r_multiple: float = 2.0
    max_holding_days: int = 10
    time_stop_days: int = 5
    time_stop_progress_r_multiple: float = 1.0
    time_stop_condition: str = "no_upside_progress"
    round_trip_cost: float = 0.002
    allow_same_day_exit: bool = False
    intraday_priority: str = "stop_first"

    @classmethod
    def from_setup_config(cls, setup_config: dict[str, Any] | None = None) -> "StockRSPullbackBacktestConfig":
        cfg = setup_config or {}
        entry = cfg.get("entry", {})
        exit_cfg = cfg.get("exit", {})
        cost = cfg.get("cost", {})
        return cls(
            setup_id=str(cfg.get("setup_id", "STOCK_RS_PULLBACK_v1")),
            entry_execution=str(entry.get("execution", "next_day_open")),
            target_r_multiple=float(exit_cfg.get("target_r_multiple", 2.0)),
            max_holding_days=int(exit_cfg.get("max_holding_days", 10)),
            time_stop_days=int(exit_cfg.get("time_stop_days", 5)),
            time_stop_progress_r_multiple=float(exit_cfg.get("time_stop_progress_r_multiple", 1.0)),
            time_stop_condition=str(exit_cfg.get("time_stop_condition", "no_upside_progress")),
            round_trip_cost=float(cost.get("round_trip_cost", 0.002)),
            allow_same_day_exit=bool(exit_cfg.get("allow_same_day_exit", False)),
            intraday_priority=str(exit_cfg.get("intraday_priority", "stop_first")),
        )


def compute_trade_return(entry_price: float, exit_price: float, *, round_trip_cost: float = 0.002) -> dict[str, float]:
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    gross = exit_price / entry_price - 1
    net = apply_round_trip_cost(gross, round_trip_cost)
    return {"gross_return": gross, "net_return": net}


def run_stock_rs_pullback_backtest(
    signals: pd.DataFrame,
    daily_bars: pd.DataFrame,
    *,
    setup_config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Backtest triggered STOCK_RS_PULLBACK_v1 signals with daily bars.

    Execution model:
    - entry: next trading day's open after ``trigger_date`` / ``signal_date``;
    - stop: pullback low / signal stop price;
    - target: entry + R * target_r_multiple;
    - max holding: D10 close, counting entry day as D1;
    - time stop: D5 close when price has not reached +1R progress;
    - A-share T+1: same-day exit is disabled by default;
    - ambiguous stop+target day: stop first by default.
    """
    cfg = StockRSPullbackBacktestConfig.from_setup_config(setup_config)
    sig = _prepare_signals(signals, cfg)
    bars = _prepare_daily_bars(daily_bars)
    bars_by_code = {code: g.sort_values("date").reset_index(drop=True) for code, g in bars.groupby("code", sort=False)}

    rows = _run_signal_groups(sig, bars_by_code, cfg)
    return _format_trade_rows(rows)


def run_stock_rs_pullback_backtest_from_parquet(
    signals: pd.DataFrame,
    daily_path: str | Path,
    *,
    setup_config: dict[str, Any] | None = None,
    batch_size: int = 250_000,
) -> pd.DataFrame:
    """Backtest signals while reading sorted daily Parquet by code batches."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    cfg = StockRSPullbackBacktestConfig.from_setup_config(setup_config)
    sig = _prepare_signals(signals, cfg)
    if sig.empty:
        return _format_trade_rows([])

    signal_codes = set(sig["code"].astype(str))
    bars_file = pq.ParquetFile(daily_path)
    columns = [
        "date",
        "code",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "adj_factor",
        "adj_offset",
        "is_suspended",
        "can_buy_at_open",
        "can_sell_at_open",
        "can_sell_intraday",
        "can_sell_at_close",
        "one_price_limit_up",
        "one_price_limit_down",
        "limit_rule_status",
    ]
    rows: list[dict[str, Any]] = []
    processed_codes: set[str] = set()
    active_code: str | None = None
    active_bars: pd.DataFrame | None = None

    for batch in bars_file.iter_batches(batch_size=batch_size, columns=columns):
        chunk = batch.to_pandas()
        chunk["code"] = chunk["code"].astype(str)
        chunk = chunk.loc[chunk["code"].isin(signal_codes)]
        if chunk.empty:
            continue
        # Preserve Parquet source order; source code blocks may cross market boundaries.
        chunk = chunk.reset_index(drop=True)
        for code, code_bars in chunk.groupby("code", sort=False):
            code = str(code)
            code_bars = code_bars.copy()
            if active_code == code:
                active_bars = pd.concat([active_bars, code_bars], ignore_index=True)
                continue
            if active_code is not None and active_bars is not None:
                rows.extend(_run_signal_groups(
                    sig.loc[sig["code"] == active_code],
                    {active_code: active_bars},
                    cfg,
                ))
                processed_codes.add(active_code)
            active_code = code
            active_bars = code_bars

    if active_code is not None and active_bars is not None:
        rows.extend(_run_signal_groups(
            sig.loc[sig["code"] == active_code],
            {active_code: active_bars},
            cfg,
        ))
        processed_codes.add(active_code)
    for code in signal_codes - processed_codes:
        rows.extend(_run_signal_groups(sig.loc[sig["code"] == code], {}, cfg))
    return _format_trade_rows(rows)


def _run_signal_groups(
    signals: pd.DataFrame,
    bars_by_code: dict[str, pd.DataFrame],
    cfg: StockRSPullbackBacktestConfig,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, signal in signals.iterrows():
        code = str(signal["code"])
        bars = bars_by_code.get(code)
        if bars is not None:
            bars = _prepare_daily_bars(bars)
        rows.append(_backtest_one_signal(signal.to_dict(), bars, cfg))
    return rows


def _format_trade_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    out = pd.DataFrame(rows)
    for col in TRADE_OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    out = out.drop_duplicates("signal_id", keep="first")
    return out[TRADE_OUTPUT_COLUMNS].sort_values(["code", "signal_date", "trade_id"]).reset_index(drop=True)


def write_trades(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        df.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        df.to_parquet(path, index=False)
    return path


def summarize_backtest_trades(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {
            "rows": 0,
            "valid_trades": 0,
            "invalid_trades": 0,
            "mean_net_return": None,
            "median_net_return": None,
            "win_rate": None,
            "profit_factor": None,
            "status_counts": {},
            "exit_reason_counts": {},
            "invalid_reason_counts": {},
        }
    valid = trades.loc[trades["status"] == "valid_trade"].copy()
    returns = pd.to_numeric(valid["net_return"], errors="coerce").dropna()
    gains = returns.loc[returns > 0].sum()
    losses = -returns.loc[returns < 0].sum()
    pf = float("inf") if losses == 0 and gains > 0 else (float(gains / losses) if losses > 0 else 0.0)
    return {
        "rows": int(len(trades)),
        "valid_trades": int(len(valid)),
        "invalid_trades": int(len(trades) - len(valid)),
        "mean_net_return": None if returns.empty else float(returns.mean()),
        "median_net_return": None if returns.empty else float(returns.median()),
        "win_rate": None if returns.empty else float((returns > 0).mean()),
        "profit_factor": pf,
        "status_counts": trades["status"].value_counts(dropna=False).to_dict(),
        "exit_reason_counts": valid["exit_reason"].value_counts(dropna=False).to_dict() if not valid.empty else {},
        "invalid_reason_counts": trades.loc[trades["status"] != "valid_trade", "invalid_reason"].value_counts(dropna=False).to_dict(),
    }


def _backtest_one_signal(signal: dict[str, Any], bars: pd.DataFrame | None, cfg: StockRSPullbackBacktestConfig) -> dict[str, Any]:
    base = _base_trade_row(signal, cfg)
    if signal.get("status") != "triggered_entry_next_open":
        return _invalid(base, "invalid_signal_status")
    if bars is None or bars.empty:
        return _invalid(base, "invalid_no_next_open")

    signal_date = pd.Timestamp(signal.get("trigger_date") or signal.get("signal_date")).normalize()
    future = bars.loc[bars["date"] > signal_date].sort_values("date").reset_index(drop=True)
    if future.empty:
        return _invalid(base, "invalid_no_next_open")

    entry_bar = future.iloc[0].to_dict()
    ok, reason = can_buy_at_open(entry_bar)
    if not ok:
        return _invalid(base, reason or "invalid_no_next_open", entry_bar=entry_bar)

    entry_price = float(entry_bar["raw_open"])
    entry_adjusted_price = float(entry_bar["adj_open"])
    entry_transform = price_transform(entry_bar)
    if entry_transform is None:
        return _invalid(base, "invalid_inconsistent_price_layers", entry_bar=entry_bar)
    entry_factor, entry_offset = entry_transform
    stop_adjusted_price = _num(signal.get("stop_price"))
    if stop_adjusted_price is None:
        stop_adjusted_price = _num(signal.get("pullback_low"))
    stop_price = None if stop_adjusted_price is None else (stop_adjusted_price - entry_offset) / entry_factor
    if stop_adjusted_price is None or stop_adjusted_price >= entry_adjusted_price:
        row = {
            **base,
            "entry_date": _date_str(entry_bar["date"]),
            "entry_price": entry_price,
            "entry_adjusted_price": entry_adjusted_price,
            "entry_adj_factor": entry_factor,
            "entry_adj_offset": entry_offset,
            "stop_price": stop_price,
            "stop_adjusted_price": stop_adjusted_price,
        }
        return _invalid(row, "invalid_stop_not_below_entry")

    risk_adjusted = entry_adjusted_price - stop_adjusted_price
    target_adjusted_price = entry_adjusted_price + risk_adjusted * cfg.target_r_multiple
    progress_adjusted_price = entry_adjusted_price + risk_adjusted * cfg.time_stop_progress_r_multiple
    target_price = (target_adjusted_price - entry_offset) / entry_factor
    horizon = future.copy().reset_index(drop=True)
    if horizon.empty:
        return _invalid(base, "invalid_no_exit_data", entry_bar=entry_bar)

    max_high_since_entry = float("-inf")
    exit_date = None
    exit_price = None
    exit_reason = None
    holding_days = None
    pending_exit_reason = None
    invalid_context = {
        **base,
        "entry_date": _date_str(entry_bar["date"]),
        "entry_price": entry_price,
        "entry_adjusted_price": entry_adjusted_price,
        "entry_adj_factor": entry_factor,
        "entry_adj_offset": entry_offset,
        "stop_price": stop_price,
        "stop_adjusted_price": stop_adjusted_price,
        "target_price": target_price,
        "target_adjusted_price": target_adjusted_price,
    }

    for i, (_, row) in enumerate(horizon.iterrows(), start=1):
        bar = row.to_dict()
        transform = price_transform(bar)
        if not has_valid_ohlc(bar) or not has_valid_adjusted_ohlc(bar) or transform is None:
            return _invalid(invalid_context, "invalid_inconsistent_price_layers")
        factor, offset = transform

        if pending_exit_reason is not None:
            sellable, sell_reason = can_sell_on_bar(bar, execution="open")
            if sellable is None:
                return _invalid(invalid_context, sell_reason or "invalid_exit_fillability_unknown")
            if not sellable:
                continue
            exit_date = _date_str(bar["date"])
            exit_price = float(bar["raw_open"])
            exit_reason = pending_exit_reason
            holding_days = i
            break

        high = float(bar["adj_high"])
        low = float(bar["adj_low"])
        close = float(bar["adj_close"])
        max_high_since_entry = max(max_high_since_entry, high)
        can_exit_today = cfg.allow_same_day_exit or i > 1

        if can_exit_today:
            stop_hit = low <= stop_adjusted_price
            target_hit = high >= target_adjusted_price
            if stop_hit or target_hit:
                sellable, sell_reason = can_sell_on_bar(bar, execution="intraday")
                if sellable is None:
                    return _invalid(invalid_context, sell_reason or "invalid_exit_fillability_unknown")
                chosen_reason = "target_2r" if target_hit and not stop_hit else "stop_loss"
                if stop_hit and target_hit and cfg.intraday_priority == "target_first":
                    chosen_reason = "target_2r"
                if not sellable:
                    pending_exit_reason = chosen_reason
                    continue
                exit_date = _date_str(bar["date"])
                if chosen_reason == "target_2r":
                    exit_price = _target_exit_price(bar, target_adjusted_price, factor, offset)
                    exit_reason = "target_2r"
                else:
                    exit_price = _stop_exit_price(bar, stop_adjusted_price, factor, offset)
                    exit_reason = "stop_loss"
                holding_days = i
                break

        if i == cfg.time_stop_days and max_high_since_entry < progress_adjusted_price:
            sellable, sell_reason = can_sell_on_bar(bar, execution="close")
            if sellable is None:
                return _invalid(invalid_context, sell_reason or "invalid_exit_fillability_unknown")
            if not sellable:
                pending_exit_reason = "time_stop_no_upside_progress"
                continue
            exit_date = _date_str(bar["date"])
            exit_price = float(bar["raw_close"])
            exit_reason = "time_stop_no_upside_progress"
            holding_days = i
            break

        if i >= cfg.max_holding_days:
            sellable, sell_reason = can_sell_on_bar(bar, execution="close")
            if sellable is None:
                return _invalid(invalid_context, sell_reason or "invalid_exit_fillability_unknown")
            if not sellable:
                pending_exit_reason = "max_holding_exit"
                continue
            exit_date = _date_str(bar["date"])
            exit_price = float(bar["raw_close"])
            exit_reason = "max_holding_exit"
            holding_days = i
            break

    if exit_date is None:
        return _invalid(
            {**base, "entry_date": _date_str(entry_bar["date"]), "entry_price": entry_price, "stop_price": stop_price, "target_price": target_price},
            "invalid_no_exit_data",
        )

    exit_bar = horizon.loc[horizon["date"].eq(pd.Timestamp(exit_date))].iloc[-1]
    exit_transform = price_transform(exit_bar.to_dict())
    if exit_transform is None:
        return _invalid(base, "invalid_inconsistent_price_layers")
    exit_factor, exit_offset = exit_transform
    exit_adjusted_price = float(exit_price) * exit_factor + exit_offset
    ret = compute_trade_return(entry_adjusted_price, exit_adjusted_price, round_trip_cost=cfg.round_trip_cost)
    r_multiple = (exit_adjusted_price - entry_adjusted_price) / risk_adjusted
    trade_id = f"{cfg.setup_id}:{signal['code']}:{_date_str(signal_date)}:{_date_str(entry_bar['date'])}"
    return {
        **base,
        "trade_id": trade_id,
        "entry_date": _date_str(entry_bar["date"]),
        "entry_price": entry_price,
        "entry_adjusted_price": entry_adjusted_price,
        "entry_adj_factor": entry_factor,
        "entry_adj_offset": entry_offset,
        "stop_price": stop_price,
        "stop_adjusted_price": stop_adjusted_price,
        "target_price": target_price,
        "target_adjusted_price": target_adjusted_price,
        "exit_date": exit_date,
        "exit_price": float(exit_price),
        "exit_adjusted_price": exit_adjusted_price,
        "exit_adj_factor": exit_factor,
        "exit_adj_offset": exit_offset,
        "exit_reason": exit_reason,
        "gross_return": ret["gross_return"],
        "net_return": ret["net_return"],
        "r_multiple": r_multiple,
        "holding_days": int(holding_days),
        "round_trip_cost": cfg.round_trip_cost,
        "status": "valid_trade",
        "invalid_reason": None,
    }


def _prepare_signals(signals: pd.DataFrame, cfg: StockRSPullbackBacktestConfig) -> pd.DataFrame:
    if signals.empty:
        raise ValueError("signals is empty")
    required = {"signal_id", "code", "signal_date", "status", "stop_price"}
    missing = sorted(required - set(signals.columns))
    if missing:
        raise ValueError(f"signals missing required columns: {missing}")
    out = signals.copy()
    out["code"] = out["code"].astype(str)
    out["signal_date"] = pd.to_datetime(out["signal_date"]).dt.normalize()
    if "trigger_date" in out.columns:
        out["trigger_date"] = pd.to_datetime(out["trigger_date"], errors="coerce").dt.normalize()
    else:
        out["trigger_date"] = out["signal_date"]
    for col in ["stop_price", "pullback_low", "pullback_high", "trigger_close"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.drop_duplicates(["signal_id"], keep="last")
    return out.loc[out["status"] == "triggered_entry_next_open"].reset_index(drop=True)


def _prepare_daily_bars(daily_bars: pd.DataFrame) -> pd.DataFrame:
    if daily_bars.empty:
        raise ValueError("daily_bars is empty")
    required = {
        "date",
        "code",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "adj_factor",
        "adj_offset",
        "is_suspended",
        "can_buy_at_open",
        "can_sell_at_open",
        "can_sell_intraday",
        "can_sell_at_close",
    }
    missing = sorted(required - set(daily_bars.columns))
    if missing:
        raise ValueError(f"daily_bars missing required columns: {missing}")
    out = daily_bars.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["code"] = out["code"].astype(str)
    for col in [
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "adj_factor",
    ]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.drop_duplicates(["date", "code"], keep="last")
    return out.sort_values(["code", "date"]).reset_index(drop=True)


def _base_trade_row(signal: dict[str, Any], cfg: StockRSPullbackBacktestConfig) -> dict[str, Any]:
    signal_date = _date_str(signal.get("signal_date"))
    trigger_date = _date_str(signal.get("trigger_date") or signal.get("signal_date"))
    pullback_date = _date_str(signal.get("pullback_date") or signal.get("signal_date"))
    return {
        "trade_id": f"{cfg.setup_id}:{signal.get('code')}:{trigger_date}:invalid",
        "signal_id": signal.get("signal_id"),
        "setup_id": signal.get("setup_id") or cfg.setup_id,
        "code": str(signal.get("code")),
        "name": _optional_str(signal.get("name")),
        "signal_date": signal_date,
        "pullback_date": pullback_date,
        "trigger_date": trigger_date,
        "entry_date": None,
        "entry_price": None,
        "entry_adjusted_price": None,
        "entry_adj_factor": None,
        "entry_adj_offset": None,
        "stop_price": _num(signal.get("stop_price")),
        "stop_adjusted_price": _num(signal.get("stop_price")),
        "target_price": None,
        "target_adjusted_price": None,
        "exit_date": None,
        "exit_price": None,
        "exit_adjusted_price": None,
        "exit_adj_factor": None,
        "exit_adj_offset": None,
        "exit_reason": None,
        "gross_return": None,
        "net_return": None,
        "r_multiple": None,
        "holding_days": None,
        "round_trip_cost": cfg.round_trip_cost,
        "status": "invalid_trade",
        "invalid_reason": None,
    }


def _invalid(base: dict[str, Any], reason: str, *, entry_bar: dict[str, Any] | None = None) -> dict[str, Any]:
    row = dict(base)
    if entry_bar is not None:
        row["entry_date"] = _date_str(entry_bar.get("date"))
        row["entry_price"] = _num(entry_bar.get("raw_open"))
        row["entry_adjusted_price"] = _num(entry_bar.get("adj_open"))
        row["entry_adj_factor"] = _num(entry_bar.get("adj_factor"))
        row["entry_adj_offset"] = _num(entry_bar.get("adj_offset"))
    row["status"] = "invalid_trade"
    row["invalid_reason"] = reason
    return row


def _num(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_str(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(pd.Timestamp(value).date())


def _optional_str(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)


def _stop_exit_price(bar: dict[str, Any], adjusted_stop: float, factor: float, offset: float) -> float:
    adjusted_open = float(bar["adj_open"])
    return float(bar["raw_open"]) if adjusted_open <= adjusted_stop else (adjusted_stop - offset) / factor


def _target_exit_price(bar: dict[str, Any], adjusted_target: float, factor: float, offset: float) -> float:
    adjusted_open = float(bar["adj_open"])
    return float(bar["raw_open"]) if adjusted_open >= adjusted_target else (adjusted_target - offset) / factor
