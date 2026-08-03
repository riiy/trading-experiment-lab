from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from texperiment.account.position_sizing import size_position


@dataclass(frozen=True)
class DailyEquityConfig:
    initial_cash: float = 30_000.0
    max_planned_loss_per_trade: float = 500.0
    max_monthly_loss: float = 1_500.0
    max_total_drawdown: float = 3_000.0
    lot_size: int = 100
    max_one_lot_value: float = 15_000.0
    commission_rate: float = 0.0003
    minimum_commission: float = 5.0
    sell_stamp_duty_rate: float = 0.0005
    slippage_rate: float = 0.0005

    @classmethod
    def from_configs(cls, account_config: dict[str, Any] | None = None, setup_config: dict[str, Any] | None = None) -> "DailyEquityConfig":
        account, setup = account_config or {}, setup_config or {}
        account_values, risk, universe = account.get("account", {}), account.get("risk", {}), setup.get("universe", {})
        cost = setup.get("account_cost", setup.get("cost", {}))
        return cls(
            initial_cash=float(account_values.get("capital_limit", 30_000)), max_planned_loss_per_trade=float(risk.get("max_planned_loss_per_trade", 500)),
            max_monthly_loss=float(risk.get("max_monthly_loss", 1_500)), max_total_drawdown=float(risk.get("max_total_drawdown", 3_000)),
            lot_size=int(universe.get("lot_size", 100)), max_one_lot_value=float(universe.get("max_one_lot_value", 15_000)),
            commission_rate=float(cost.get("commission_rate", 0.0003)), minimum_commission=float(cost.get("minimum_commission", 5)),
            sell_stamp_duty_rate=float(cost.get("sell_stamp_duty_rate", 0.0005)), slippage_rate=float(cost.get("slippage_rate", 0.0005)),
        )


EQUITY_COLUMNS = ["date", "cash", "position_market_value", "equity", "cumulative_cost", "peak_equity", "drawdown", "drawdown_pct", "position_code", "shares", "account_frozen"]
LEDGER_COLUMNS = ["trade_id", "status", "invalid_reason", "code", "entry_date", "exit_date", "shares", "entry_fill_price", "exit_fill_price", "buy_commission", "sell_commission", "stamp_duty", "slippage_cost", "total_cost", "realized_pnl", "planned_loss"]


def commission(notional: float, cfg: DailyEquityConfig) -> float:
    if notional <= 0:
        raise ValueError("notional must be positive")
    return max(notional * cfg.commission_rate, cfg.minimum_commission)


def transaction_costs(*, side: str, price: float, shares: int, cfg: DailyEquityConfig) -> dict[str, float]:
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if price <= 0 or shares <= 0:
        raise ValueError("price and shares must be positive")
    fill = price * (1 + cfg.slippage_rate) if side == "buy" else price * (1 - cfg.slippage_rate)
    notional = fill * shares
    fee = commission(notional, cfg)
    stamp = notional * cfg.sell_stamp_duty_rate if side == "sell" else 0.0
    return {"fill_price": fill, "notional": notional, "commission": fee, "stamp_duty": stamp, "slippage_cost": abs(fill - price) * shares, "cash_change": -(notional + fee) if side == "buy" else notional - fee - stamp}


def run_daily_account_equity(
    trades: pd.DataFrame,
    daily_bars: pd.DataFrame,
    *,
    start_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp,
    account_config: dict[str, Any] | None = None,
    setup_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the authoritative daily 30,000 CNY equity curve for this setup.

    The engine fails closed if a held security cannot be marked at a daily close.
    Costs are reflected only here, rather than in a trade-return layer.
    """
    cfg = DailyEquityConfig.from_configs(account_config, setup_config)
    start, end = pd.Timestamp(start_date).normalize(), pd.Timestamp(end_date).normalize()
    if end < start:
        raise ValueError("end_date must not be before start_date")
    bars = _prepare_bars(daily_bars)
    dates = pd.DatetimeIndex(sorted(bars.loc[(bars.date >= start) & (bars.date <= end), "date"].unique()))
    if dates.empty:
        raise ValueError("daily equity not evaluable: no trading dates in validation window")
    accepted, ledger = _accept_executable_trades(trades, cfg, start, end)
    entry_events = {pd.Timestamp(t.entry_date): t for t in accepted}
    exit_events = {pd.Timestamp(t.exit_date): t for t in accepted}
    lookup = bars.set_index(["date", "code"])["raw_close"]
    cash, cumulative_cost, peak = cfg.initial_cash, 0.0, cfg.initial_cash
    position: dict[str, Any] | None = None
    frozen = False
    monthly_pnl: dict[str, float] = {}
    realized_total = 0.0
    rows: list[dict[str, Any]] = []
    ledger_rows = {row["trade_id"]: row for row in ledger}
    for date in dates:
        if position is not None and date in exit_events:
            event = exit_events[date]
            sell = transaction_costs(side="sell", price=float(event.exit_price), shares=int(position["shares"]), cfg=cfg)
            cash += sell["cash_change"]; cumulative_cost += sell["commission"] + sell["stamp_duty"] + sell["slippage_cost"]
            record = ledger_rows[event.trade_id]
            record.update({"exit_fill_price": sell["fill_price"], "sell_commission": sell["commission"], "stamp_duty": sell["stamp_duty"], "slippage_cost": record["slippage_cost"] + sell["slippage_cost"], "total_cost": record["total_cost"] + sell["commission"] + sell["stamp_duty"] + sell["slippage_cost"], "realized_pnl": cash - position["cash_before"]})
            month = pd.Timestamp(event.entry_date).strftime("%Y-%m")
            monthly_pnl[month] = monthly_pnl.get(month, 0.0) + float(record["realized_pnl"])
            realized_total += float(record["realized_pnl"])
            position = None
        if position is None and frozen and date in entry_events:
            ledger_rows[entry_events[date].trade_id].update({"status": "rejected_or_skipped", "invalid_reason": "skipped_after_total_drawdown_freeze", "shares": 0})
        if position is None and not frozen and date in entry_events:
            event = entry_events[date]
            record = ledger_rows[event.trade_id]
            month = pd.Timestamp(event.entry_date).strftime("%Y-%m")
            if monthly_pnl.get(month, 0.0) <= -cfg.max_monthly_loss or monthly_pnl.get(month, 0.0) - float(event.planned_loss) < -cfg.max_monthly_loss:
                record.update({"status": "rejected_or_skipped", "invalid_reason": "rejected_monthly_loss_budget", "shares": 0})
            elif realized_total - float(event.planned_loss) < -cfg.max_total_drawdown:
                record.update({"status": "rejected_or_skipped", "invalid_reason": "rejected_total_drawdown_budget", "shares": 0})
            else:
                buy = transaction_costs(side="buy", price=float(event.entry_price), shares=int(event.shares), cfg=cfg)
                if buy["notional"] + buy["commission"] > cash + 1e-9:
                    record.update({"status": "rejected_or_skipped", "invalid_reason": "rejected_cash_after_cost", "shares": 0})
                else:
                    before = cash; cash += buy["cash_change"]; cumulative_cost += buy["commission"] + buy["slippage_cost"]
                    position = {"code": event.code, "shares": int(event.shares), "cash_before": before, "trade_id": event.trade_id}
                    record.update({"entry_fill_price": buy["fill_price"], "buy_commission": buy["commission"], "slippage_cost": buy["slippage_cost"], "total_cost": buy["commission"] + buy["slippage_cost"], "status": "accepted_trade"})
        value = 0.0
        if position is not None:
            try:
                close = float(lookup.loc[(date, position["code"])])
            except KeyError as exc:
                raise ValueError(f"daily equity not evaluable: missing raw close for held {position['code']} on {date.date()}") from exc
            if not math.isfinite(close) or close <= 0:
                raise ValueError(f"daily equity not evaluable: invalid raw close for held {position['code']} on {date.date()}")
            value = close * position["shares"]
        equity = cash + value; peak = max(peak, equity); drawdown = equity - peak
        if drawdown <= -cfg.max_total_drawdown:
            frozen = True
        rows.append({"date": date, "cash": cash, "position_market_value": value, "equity": equity, "cumulative_cost": cumulative_cost, "peak_equity": peak, "drawdown": drawdown, "drawdown_pct": drawdown / peak if peak else 0.0, "position_code": position["code"] if position else None, "shares": position["shares"] if position else 0, "account_frozen": frozen})
    curve = pd.DataFrame(rows, columns=EQUITY_COLUMNS)
    ledger_df = pd.DataFrame(list(ledger_rows.values()), columns=LEDGER_COLUMNS)
    return {"equity_curve": curve, "ledger": ledger_df, "summary": summarize_daily_equity(curve, cfg), "config": asdict(cfg)}


def summarize_daily_equity(curve: pd.DataFrame, cfg: DailyEquityConfig | None = None) -> dict[str, Any]:
    cfg = cfg or DailyEquityConfig()
    if curve.empty:
        raise ValueError("daily equity not evaluable: empty curve")
    start, end = pd.Timestamp(curve.iloc[0].date), pd.Timestamp(curve.iloc[-1].date)
    years = max((end - start).days / 365.25, 0.0)
    final = float(curve.iloc[-1].equity)
    cagr = (final / cfg.initial_cash) ** (1 / years) - 1 if years > 0 and final > 0 else None
    return {"initial_equity": cfg.initial_cash, "final_equity": final, "account_cagr": cagr, "max_drawdown": float(-curve["drawdown"].min()), "max_drawdown_pct": float(-curve["drawdown_pct"].min()), "start_date": str(start.date()), "end_date": str(end.date()), "days_for_cagr": (end - start).days, "account_frozen": bool(curve["account_frozen"].iloc[-1])}


def _accept_executable_trades(trades: pd.DataFrame, cfg: DailyEquityConfig, start: pd.Timestamp, end: pd.Timestamp) -> tuple[list[pd.Series], list[dict[str, Any]]]:
    required = {"trade_id", "code", "entry_date", "exit_date", "entry_price", "exit_price", "stop_price", "status"}
    missing = sorted(required - set(trades.columns))
    if missing:
        raise ValueError(f"trades missing required columns: {missing}")
    work = trades.copy(); work["entry_date"] = pd.to_datetime(work["entry_date"], errors="coerce").dt.normalize(); work["exit_date"] = pd.to_datetime(work["exit_date"], errors="coerce").dt.normalize()
    work = work.sort_values(["entry_date", "exit_date", "trade_id"], na_position="last")
    accepted: list[pd.Series] = []; ledger: list[dict[str, Any]] = []; active_until: pd.Timestamp | None = None; monthly: dict[str, float] = {}; realized_total = 0.0; frozen = False
    for _, trade in work.iterrows():
        base = {"trade_id": str(trade.trade_id), "status": "rejected_or_skipped", "invalid_reason": None, "code": str(trade.code), "entry_date": trade.entry_date, "exit_date": trade.exit_date, "shares": 0, "entry_fill_price": None, "exit_fill_price": None, "buy_commission": 0.0, "sell_commission": 0.0, "stamp_duty": 0.0, "slippage_cost": 0.0, "total_cost": 0.0, "realized_pnl": 0.0, "planned_loss": 0.0}
        if trade.status != "valid_trade" or pd.isna(trade.entry_date) or pd.isna(trade.exit_date) or not (start <= trade.entry_date <= end and trade.exit_date <= end): base["invalid_reason"] = "skipped_invalid_or_outside_trade"; ledger.append(base); continue
        if frozen: base["invalid_reason"] = "skipped_after_total_drawdown_freeze"; ledger.append(base); continue
        if active_until is not None and trade.entry_date <= active_until: base["invalid_reason"] = "rejected_max_positions"; ledger.append(base); continue
        sizing = size_position(entry_price=float(trade.entry_price), stop_price=float(trade.stop_price), max_planned_loss=cfg.max_planned_loss_per_trade, capital_limit=cfg.initial_cash, lot_size=cfg.lot_size, max_one_lot_value=cfg.max_one_lot_value)
        if not sizing.valid: base["invalid_reason"] = sizing.reason; ledger.append(base); continue
        month = trade.entry_date.strftime("%Y-%m")
        if monthly.get(month, 0.0) <= -cfg.max_monthly_loss or monthly.get(month, 0.0) - sizing.planned_loss < -cfg.max_monthly_loss: base["invalid_reason"] = "rejected_monthly_loss_budget"; ledger.append(base); continue
        if realized_total - sizing.planned_loss < -cfg.max_total_drawdown: base["invalid_reason"] = "rejected_total_drawdown_budget"; ledger.append(base); continue
        trade = trade.copy(); trade["shares"] = sizing.shares; trade["planned_loss"] = sizing.planned_loss; accepted.append(trade); active_until = trade.exit_date; base.update({"status": "accepted_trade", "shares": sizing.shares, "planned_loss": sizing.planned_loss}); ledger.append(base)
    return accepted, ledger


def _prepare_bars(daily_bars: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "code", "raw_close"}
    missing = sorted(required - set(daily_bars.columns))
    if missing: raise ValueError(f"daily_bars missing required columns: {missing}")
    out = daily_bars[["date", "code", "raw_close"]].copy(); out["date"] = pd.to_datetime(out.date, errors="coerce").dt.normalize(); out["code"] = out.code.astype(str); out["raw_close"] = pd.to_numeric(out.raw_close, errors="coerce")
    return out.drop_duplicates(["date", "code"], keep="last").dropna(subset=["date", "raw_close"])
