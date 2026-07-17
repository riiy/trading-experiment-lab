from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from texperiment.account.position_sizing import PositionSizeResult, size_position


@dataclass(frozen=True)
class AccountSimulationConfig:
    """Account-level constraints for the 30,000 CNY Trading Experiment account."""

    setup_id: str = "STOCK_RS_PULLBACK_v1"
    capital_limit: float = 30_000.0
    max_planned_loss_per_trade: float = 500.0
    max_monthly_loss: float = 1_500.0
    max_total_drawdown: float = 3_000.0
    max_positions: int = 1
    lot_size: int = 100
    max_one_lot_value: float = 15_000.0
    initial_equity: float = 30_000.0

    @classmethod
    def from_configs(
        cls,
        *,
        account_config: dict[str, Any] | None = None,
        setup_config: dict[str, Any] | None = None,
    ) -> "AccountSimulationConfig":
        account_config = account_config or {}
        setup_config = setup_config or {}
        account = account_config.get("account", {})
        risk = account_config.get("risk", {})
        universe = setup_config.get("universe", {})
        return cls(
            setup_id=str(setup_config.get("setup_id", "STOCK_RS_PULLBACK_v1")),
            capital_limit=float(account.get("capital_limit", 30_000.0)),
            max_planned_loss_per_trade=float(risk.get("max_planned_loss_per_trade", 500.0)),
            max_monthly_loss=float(risk.get("max_monthly_loss", 1_500.0)),
            max_total_drawdown=float(risk.get("max_total_drawdown", 3_000.0)),
            max_positions=int(risk.get("max_positions", 1)),
            lot_size=int(universe.get("lot_size", 100)),
            max_one_lot_value=float(universe.get("max_one_lot_value", 15_000.0)),
            initial_equity=float(account.get("capital_limit", 30_000.0)),
        )


ACCOUNT_SIM_OUTPUT_COLUMNS = [
    "simulation_id",
    "trade_id",
    "setup_id",
    "code",
    "name",
    "entry_date",
    "exit_date",
    "entry_price",
    "stop_price",
    "target_price",
    "exit_price",
    "exit_reason",
    "net_return",
    "r_multiple",
    "shares",
    "capital_used",
    "per_share_risk",
    "planned_loss",
    "pnl",
    "cumulative_pnl",
    "account_equity",
    "peak_equity",
    "drawdown_from_peak",
    "monthly_realized_pnl",
    "consecutive_losses",
    "status",
    "invalid_reason",
]


ACCEPTED_STATUS = "accepted_trade"


def run_account_simulation(
    trades: pd.DataFrame,
    *,
    account_config: dict[str, Any] | None = None,
    setup_config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Simulate whether validated trades are executable inside the 30,000 CNY account.

    This layer does not change backtest exits. It only applies account-level constraints:
    position sizing by planned risk, one-lot affordability, max one open position,
    monthly loss budget, and total drawdown freeze.
    """
    cfg = AccountSimulationConfig.from_configs(account_config=account_config, setup_config=setup_config)
    if cfg.max_positions != 1:
        raise ValueError("account simulation currently supports max_positions=1 only")
    prepared = _prepare_trades(trades)

    rows: list[dict[str, Any]] = []
    active_until: pd.Timestamp | None = None
    cumulative_pnl = 0.0
    peak_equity = cfg.initial_equity
    monthly_pnl: dict[str, float] = {}
    monthly_frozen: set[str] = set()
    consecutive_losses = 0
    frozen = False

    for i, trade in prepared.iterrows():
        base = _base_sim_row(trade, cfg, i)

        if trade.get("status") != "valid_trade":
            rows.append(_reject(base, "skipped_invalid_backtest_trade", cumulative_pnl, cfg, monthly_pnl, peak_equity, consecutive_losses))
            continue

        entry_date = _to_ts(trade.get("entry_date"))
        exit_date = _to_ts(trade.get("exit_date"))
        entry_price = _num(trade.get("entry_price"))
        stop_price = _num(trade.get("stop_price"))
        net_return = _num(trade.get("net_return"))
        if entry_date is None or exit_date is None or entry_price is None or stop_price is None or net_return is None:
            rows.append(_reject(base, "rejected_missing_required_trade_fields", cumulative_pnl, cfg, monthly_pnl, peak_equity, consecutive_losses))
            continue

        if frozen or cumulative_pnl <= -cfg.max_total_drawdown:
            frozen = True
            rows.append(_reject(base, "skipped_after_total_drawdown_freeze", cumulative_pnl, cfg, monthly_pnl, peak_equity, consecutive_losses))
            continue

        if active_until is not None and entry_date <= active_until and cfg.max_positions <= 1:
            rows.append(_reject(base, "rejected_max_positions", cumulative_pnl, cfg, monthly_pnl, peak_equity, consecutive_losses))
            continue

        sizing = size_position(
            entry_price=entry_price,
            stop_price=stop_price,
            max_planned_loss=cfg.max_planned_loss_per_trade,
            capital_limit=cfg.capital_limit,
            lot_size=cfg.lot_size,
            max_one_lot_value=cfg.max_one_lot_value,
        )
        if not sizing.valid:
            rows.append(_reject(base, sizing.reason or "rejected_position_sizing", cumulative_pnl, cfg, monthly_pnl, peak_equity, consecutive_losses, sizing=sizing))
            continue

        month_key = entry_date.strftime("%Y-%m")
        current_month_pnl = monthly_pnl.get(month_key, 0.0)
        if month_key in monthly_frozen or current_month_pnl <= -cfg.max_monthly_loss:
            rows.append(_reject(base, "rejected_monthly_loss_limit_reached", cumulative_pnl, cfg, monthly_pnl, peak_equity, consecutive_losses, sizing=sizing))
            continue
        if current_month_pnl - sizing.planned_loss < -cfg.max_monthly_loss:
            rows.append(_reject(base, "rejected_monthly_loss_budget_exceeded", cumulative_pnl, cfg, monthly_pnl, peak_equity, consecutive_losses, sizing=sizing))
            continue
        if cumulative_pnl - sizing.planned_loss < -cfg.max_total_drawdown:
            rows.append(_reject(base, "rejected_total_drawdown_budget_exceeded", cumulative_pnl, cfg, monthly_pnl, peak_equity, consecutive_losses, sizing=sizing))
            continue

        pnl = sizing.capital_used * net_return
        cumulative_pnl += pnl
        monthly_pnl[month_key] = monthly_pnl.get(month_key, 0.0) + pnl
        if monthly_pnl[month_key] <= -cfg.max_monthly_loss:
            monthly_frozen.add(month_key)
        equity = cfg.initial_equity + cumulative_pnl
        peak_equity = max(peak_equity, equity)
        drawdown_from_peak = equity - peak_equity
        consecutive_losses = consecutive_losses + 1 if pnl < 0 else 0
        active_until = exit_date
        if cumulative_pnl <= -cfg.max_total_drawdown:
            frozen = True

        rows.append(
            {
                **base,
                "shares": sizing.shares,
                "capital_used": sizing.capital_used,
                "per_share_risk": sizing.per_share_risk,
                "planned_loss": sizing.planned_loss,
                "pnl": pnl,
                "cumulative_pnl": cumulative_pnl,
                "account_equity": equity,
                "peak_equity": peak_equity,
                "drawdown_from_peak": drawdown_from_peak,
                "monthly_realized_pnl": monthly_pnl[month_key],
                "consecutive_losses": consecutive_losses,
                "status": ACCEPTED_STATUS,
                "invalid_reason": None,
            }
        )

    out = pd.DataFrame(rows)
    for col in ACCOUNT_SIM_OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return out[ACCOUNT_SIM_OUTPUT_COLUMNS].reset_index(drop=True)


def summarize_account_simulation(sim: pd.DataFrame, *, config: AccountSimulationConfig | None = None) -> dict[str, Any]:
    cfg = config or AccountSimulationConfig()
    if sim.empty:
        return {
            "decision": "ACCOUNT_SIMULATION_FAILED",
            "rows": 0,
            "accepted_trades": 0,
            "rejected_or_skipped": 0,
            "final_equity": cfg.initial_equity,
            "realized_pnl": 0.0,
            "max_drawdown_from_start": 0.0,
            "max_drawdown_from_peak": 0.0,
            "worst_monthly_realized_pnl": 0.0,
            "account_frozen": False,
            "risk_breaches": 0,
            "status_counts": {},
            "invalid_reason_counts": {},
        }

    accepted = sim.loc[sim["status"] == ACCEPTED_STATUS].copy()
    final_pnl = float(pd.to_numeric(sim["cumulative_pnl"], errors="coerce").ffill().fillna(0).iloc[-1])
    final_equity = cfg.initial_equity + final_pnl
    equity = pd.to_numeric(sim["account_equity"], errors="coerce").dropna()
    drawdown_peak = pd.to_numeric(sim["drawdown_from_peak"], errors="coerce").dropna()
    monthly = accepted.copy()
    worst_month = 0.0
    if not monthly.empty:
        monthly["entry_month"] = pd.to_datetime(monthly["entry_date"]).dt.strftime("%Y-%m")
        worst_month = float(monthly.groupby("entry_month")["pnl"].sum().min())

    planned_loss = pd.to_numeric(accepted.get("planned_loss"), errors="coerce") if not accepted.empty else pd.Series(dtype="float64")
    capital_used = pd.to_numeric(accepted.get("capital_used"), errors="coerce") if not accepted.empty else pd.Series(dtype="float64")
    shares = pd.to_numeric(accepted.get("shares"), errors="coerce") if not accepted.empty else pd.Series(dtype="float64")
    risk_breaches = 0
    if not accepted.empty:
        risk_breaches += int((planned_loss > cfg.max_planned_loss_per_trade + 1e-9).sum())
        risk_breaches += int((capital_used > cfg.capital_limit + 1e-9).sum())
        risk_breaches += int((shares <= 0).sum())
        risk_breaches += int((accepted["status"] != ACCEPTED_STATUS).sum())

    account_frozen = bool((sim["invalid_reason"] == "skipped_after_total_drawdown_freeze").any() or final_pnl <= -cfg.max_total_drawdown)
    monthly_limit_breached = bool(worst_month < -cfg.max_monthly_loss - 1e-9)
    drawdown_failed = bool(final_pnl < -cfg.max_total_drawdown - 1e-9)
    decision = "ACCOUNT_SIMULATION_PASSED"
    if len(accepted) == 0 or risk_breaches > 0 or account_frozen or monthly_limit_breached or drawdown_failed:
        decision = "ACCOUNT_SIMULATION_FAILED"

    return _json_safe(
        {
            "decision": decision,
            "rows": int(len(sim)),
            "accepted_trades": int(len(accepted)),
            "rejected_or_skipped": int(len(sim) - len(accepted)),
            "rejection_rate": float((len(sim) - len(accepted)) / len(sim)) if len(sim) else 0.0,
            "final_equity": final_equity,
            "realized_pnl": final_pnl,
            "return_on_account": final_pnl / cfg.initial_equity if cfg.initial_equity else 0.0,
            "max_drawdown_from_start": float((equity - cfg.initial_equity).min()) if not equity.empty else 0.0,
            "max_drawdown_from_peak": float(drawdown_peak.min()) if not drawdown_peak.empty else 0.0,
            "worst_monthly_realized_pnl": worst_month,
            "account_frozen": account_frozen,
            "monthly_limit_breached": monthly_limit_breached,
            "risk_breaches": risk_breaches,
            "status_counts": sim["status"].value_counts(dropna=False).to_dict(),
            "invalid_reason_counts": sim.loc[sim["status"] != ACCEPTED_STATUS, "invalid_reason"].value_counts(dropna=False).to_dict(),
            "config": asdict(cfg),
        }
    )


def build_account_simulation_artifacts(
    trades: pd.DataFrame,
    *,
    account_config: dict[str, Any] | None = None,
    setup_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = AccountSimulationConfig.from_configs(account_config=account_config, setup_config=setup_config)
    sim = run_account_simulation(trades, account_config=account_config, setup_config=setup_config)
    summary = summarize_account_simulation(sim, config=cfg)
    return {
        "simulation": sim,
        "summary": summary,
        "report_markdown": render_account_simulation_report(summary),
    }


def write_account_simulation_outputs(
    artifacts: dict[str, Any],
    *,
    simulation_path: str | Path,
    summary_path: str | Path,
    report_path: str | Path,
) -> dict[str, Path]:
    sim_path = _write_table(artifacts["simulation"], simulation_path)

    summary_p = Path(summary_path)
    summary_p.parent.mkdir(parents=True, exist_ok=True)
    summary_p.write_text(json.dumps(artifacts["summary"], ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    report_p = Path(report_path)
    report_p.parent.mkdir(parents=True, exist_ok=True)
    report_p.write_text(artifacts["report_markdown"], encoding="utf-8")
    return {"simulation": sim_path, "summary": summary_p, "report": report_p}


def render_account_simulation_report(summary: dict[str, Any]) -> str:
    cfg = summary.get("config", {})
    lines = [
        "# STOCK_RS_PULLBACK_v1 账户仿真报告",
        "",
        "## 1. 结论",
        "",
        f"Decision: **{summary.get('decision')}**",
        "",
        "账户仿真只检查3万元实验账户能否真实执行通过验证的信号；它不是交易许可。只有账户仿真通过且交易票规则继续通过，才允许进入 Micro Live。",
        "",
        "## 2. 账户约束",
        "",
        "| 约束 | 数值 |",
        "|---|---:|",
        f"| capital_limit | {_fmt(cfg.get('capital_limit'))} |",
        f"| max_planned_loss_per_trade | {_fmt(cfg.get('max_planned_loss_per_trade'))} |",
        f"| max_monthly_loss | {_fmt(cfg.get('max_monthly_loss'))} |",
        f"| max_total_drawdown | {_fmt(cfg.get('max_total_drawdown'))} |",
        f"| max_positions | {_fmt(cfg.get('max_positions'))} |",
        f"| max_one_lot_value | {_fmt(cfg.get('max_one_lot_value'))} |",
        "",
        "## 3. 仿真结果",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
    ]
    for key in [
        "rows",
        "accepted_trades",
        "rejected_or_skipped",
        "rejection_rate",
        "final_equity",
        "realized_pnl",
        "return_on_account",
        "max_drawdown_from_start",
        "max_drawdown_from_peak",
        "worst_monthly_realized_pnl",
        "account_frozen",
        "monthly_limit_breached",
        "risk_breaches",
    ]:
        lines.append(f"| {key} | {_fmt(summary.get(key))} |")

    lines += ["", "## 4. 状态分布", "", "| status | count |", "|---|---:|"]
    for key, value in summary.get("status_counts", {}).items():
        lines.append(f"| {key} | {value} |")

    lines += ["", "## 5. 拒绝/跳过原因", "", "| reason | count |", "|---|---:|"]
    for key, value in summary.get("invalid_reason_counts", {}).items():
        lines.append(f"| {key} | {value} |")
    if not summary.get("invalid_reason_counts"):
        lines.append("| - | 0 |")

    lines += [
        "",
        "## 6. 风控结论",
        "",
        "- `ACCOUNT_SIMULATION_FAILED` 不允许生成正式交易票。",
        "- `ACCOUNT_SIMULATION_PASSED` 只代表账户容量可执行，仍需交易票层继续检查。",
        "- 月度亏损和总回撤规则是硬约束，不允许盘中人工豁免。",
        "",
    ]
    return "\n".join(lines)


def _prepare_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    out = trades.copy()
    for col in ["entry_date", "exit_date", "signal_date"]:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.normalize()
    sort_cols = [c for c in ["entry_date", "exit_date", "code", "trade_id"] if c in out.columns]
    return out.sort_values(sort_cols, na_position="last").reset_index(drop=True)


def _base_sim_row(trade: pd.Series, cfg: AccountSimulationConfig, row_idx: int) -> dict[str, Any]:
    trade_id = str(trade.get("trade_id") or f"row-{row_idx}")
    return {
        "simulation_id": f"ACCOUNT_SIM:{trade_id}",
        "trade_id": trade_id,
        "setup_id": str(trade.get("setup_id") or cfg.setup_id),
        "code": trade.get("code"),
        "name": trade.get("name"),
        "entry_date": _date_str(trade.get("entry_date")),
        "exit_date": _date_str(trade.get("exit_date")),
        "entry_price": _num(trade.get("entry_price")),
        "stop_price": _num(trade.get("stop_price")),
        "target_price": _num(trade.get("target_price")),
        "exit_price": _num(trade.get("exit_price")),
        "exit_reason": trade.get("exit_reason"),
        "net_return": _num(trade.get("net_return")),
        "r_multiple": _num(trade.get("r_multiple")),
    }


def _reject(
    base: dict[str, Any],
    reason: str,
    cumulative_pnl: float,
    cfg: AccountSimulationConfig,
    monthly_pnl: dict[str, float],
    peak_equity: float,
    consecutive_losses: int,
    *,
    sizing: PositionSizeResult | None = None,
) -> dict[str, Any]:
    equity = cfg.initial_equity + cumulative_pnl
    sizing = sizing or PositionSizeResult(0, 0.0, 0.0, 0.0, False, None)
    month_key = None
    entry_date = _to_ts(base.get("entry_date"))
    if entry_date is not None:
        month_key = entry_date.strftime("%Y-%m")
    return {
        **base,
        "shares": sizing.shares,
        "capital_used": sizing.capital_used,
        "per_share_risk": sizing.per_share_risk,
        "planned_loss": sizing.planned_loss,
        "pnl": 0.0,
        "cumulative_pnl": cumulative_pnl,
        "account_equity": equity,
        "peak_equity": peak_equity,
        "drawdown_from_peak": equity - peak_equity,
        "monthly_realized_pnl": monthly_pnl.get(month_key, 0.0) if month_key else 0.0,
        "consecutive_losses": consecutive_losses,
        "status": "rejected_or_skipped",
        "invalid_reason": reason,
    }


def _write_table(df: pd.DataFrame, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".csv":
        df.to_csv(output, index=False, encoding="utf-8-sig")
    else:
        df.to_parquet(output, index=False)
    return output


def _num(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return None
        return out
    except (TypeError, ValueError):
        return None


def _to_ts(value: Any) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    try:
        return pd.Timestamp(value).normalize()
    except Exception:
        return None


def _date_str(value: Any) -> str | None:
    ts = _to_ts(value)
    return None if ts is None else ts.strftime("%Y-%m-%d")


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isinf(value):
            return "inf"
        return f"{value:.6f}"
    return str(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "inf"
        return value
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value
