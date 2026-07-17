from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from texperiment.metrics.industry import attach_latest_industry, by_industry
from texperiment.metrics.performance import profit_factor, win_rate
from texperiment.metrics.robustness import best_n_removed_mean, worst_n_removed_mean
from texperiment.metrics.top_contribution import (
    bottom_n_contribution_sum,
    top_n_contribution_ratio,
    top_n_contribution_sum,
)
from texperiment.metrics.yearly import by_year


@dataclass(frozen=True)
class ValidationThreshold:
    min_valid_trades: int = 80
    mean_net_return_gt: float = 0.0
    median_net_return_gte: float = 0.0
    profit_factor_gt: float = 1.20
    best_3_removed_mean_gte: float = 0.0
    top3_contribution_ratio_lte: float = 1.0
    min_positive_years_or_regimes: int = 2

    @classmethod
    def from_setup_config(cls, setup_config: dict[str, Any] | None = None) -> "ValidationThreshold":
        cfg = (setup_config or {}).get("validation_threshold", {})
        return cls(
            min_valid_trades=int(cfg.get("min_valid_trades", 80)),
            mean_net_return_gt=float(cfg.get("mean_net_return_gt", 0.0)),
            median_net_return_gte=float(cfg.get("median_net_return_gte", 0.0)),
            profit_factor_gt=float(cfg.get("profit_factor_gt", 1.20)),
            best_3_removed_mean_gte=float(cfg.get("best_3_removed_mean_gte", 0.0)),
            top3_contribution_ratio_lte=float(cfg.get("top3_contribution_ratio_lte", 1.0)),
            min_positive_years_or_regimes=int(cfg.get("min_positive_years_or_regimes", 2)),
        )


def build_validation_artifacts(
    trades: pd.DataFrame,
    *,
    setup_config: dict[str, Any] | None = None,
    metadata: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Compute validation metrics and grouped diagnostics for a backtest output."""
    threshold = ValidationThreshold.from_setup_config(setup_config)
    prepared = prepare_valid_trades(trades)
    enriched = attach_latest_industry(prepared, metadata)

    overall = compute_overall_metrics(trades, enriched)
    yearly = by_year(enriched)
    industry = by_industry(enriched)
    industry_analysis = assess_industry_analysis(enriched)
    gates = evaluate_validation_gates(overall, yearly, threshold)
    decision = decide_validation_status(gates, overall)
    metrics = {
        "setup_id": (setup_config or {}).get("setup_id", "STOCK_RS_PULLBACK_v1"),
        "decision": decision,
        "overall": overall,
        "gates": gates,
        "threshold": asdict(threshold),
        "yearly": yearly.to_dict(orient="records"),
        "industry": industry.to_dict(orient="records"),
        "industry_analysis": industry_analysis,
    }
    return {
        "metrics": _json_safe(metrics),
        "valid_trades": enriched,
        "yearly": yearly,
        "industry": industry,
        "report_markdown": render_validation_report(_json_safe(metrics)),
    }


def prepare_valid_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=list(trades.columns) + ["net_return"])
    out = trades.copy()
    if "status" in out.columns:
        out = out.loc[out["status"] == "valid_trade"].copy()
    out["net_return"] = pd.to_numeric(out.get("net_return"), errors="coerce")
    out = out.dropna(subset=["net_return"])
    return out.reset_index(drop=True)


def compute_overall_metrics(all_trades: pd.DataFrame, valid_trades: pd.DataFrame) -> dict[str, Any]:
    returns = valid_trades["net_return"].astype(float).tolist() if not valid_trades.empty else []
    series = pd.Series(returns, dtype="float64")
    total_rows = int(len(all_trades))
    valid_count = int(len(valid_trades))
    invalid_count = int(total_rows - valid_count)
    invalid_reason_counts = (
        all_trades.loc[all_trades.get("status") != "valid_trade", "invalid_reason"].value_counts(dropna=False).to_dict()
        if total_rows and "status" in all_trades.columns and "invalid_reason" in all_trades.columns
        else {}
    )
    exit_reason_counts = (
        valid_trades["exit_reason"].value_counts(dropna=False).to_dict()
        if valid_count and "exit_reason" in valid_trades.columns
        else {}
    )
    holding = pd.to_numeric(valid_trades.get("holding_days"), errors="coerce") if valid_count else pd.Series(dtype="float64")
    r_multiple = pd.to_numeric(valid_trades.get("r_multiple"), errors="coerce") if valid_count else pd.Series(dtype="float64")

    return {
        "rows": total_rows,
        "valid_trades": valid_count,
        "invalid_trades": invalid_count,
        "mean_net_return": float(series.mean()) if returns else 0.0,
        "median_net_return": float(series.median()) if returns else 0.0,
        "win_rate": win_rate(returns),
        "profit_factor": profit_factor(returns),
        "best_3_removed_mean": best_n_removed_mean(returns, n=3),
        "worst_3_removed_mean": worst_n_removed_mean(returns, n=3),
        "top3_contribution_sum": top_n_contribution_sum(returns, n=3),
        "bottom3_contribution_sum": bottom_n_contribution_sum(returns, n=3),
        "top3_contribution_ratio": top_n_contribution_ratio(returns, n=3),
        "net_return_sum": float(series.sum()) if returns else 0.0,
        "max_gain": float(series.max()) if returns else 0.0,
        "max_loss": float(series.min()) if returns else 0.0,
        "mean_r_multiple": float(r_multiple.mean()) if not r_multiple.empty else 0.0,
        "median_r_multiple": float(r_multiple.median()) if not r_multiple.empty else 0.0,
        "avg_holding_days": float(holding.mean()) if not holding.empty else 0.0,
        "exit_reason_counts": exit_reason_counts,
        "invalid_reason_counts": invalid_reason_counts,
    }


def assess_industry_analysis(valid_trades: pd.DataFrame) -> dict[str, str]:
    if valid_trades.empty or "industry" not in valid_trades.columns:
        return {
            "status": "NOT_EVALUABLE",
            "reason": "missing_industry_metadata",
            "impact_on_final_decision": "none",
        }
    labels = valid_trades["industry"].astype("string").str.strip()
    known = labels.notna() & labels.ne("") & labels.ne("UNKNOWN")
    return {
        "status": "EVALUABLE" if bool(known.any()) else "NOT_EVALUABLE",
        "reason": "available_industry_metadata" if bool(known.any()) else "missing_industry_metadata",
        "impact_on_final_decision": "none",
    }


def evaluate_validation_gates(
    overall: dict[str, Any],
    yearly: pd.DataFrame,
    threshold: ValidationThreshold,
) -> dict[str, dict[str, Any]]:
    positive_years = 0
    if not yearly.empty and "mean_net_return" in yearly.columns:
        positive_years = int((pd.to_numeric(yearly["mean_net_return"], errors="coerce") > 0).sum())
    gates = {
        "min_valid_trades": {
            "value": overall["valid_trades"],
            "threshold": threshold.min_valid_trades,
            "passed": overall["valid_trades"] >= threshold.min_valid_trades,
        },
        "mean_net_return_gt": {
            "value": overall["mean_net_return"],
            "threshold": threshold.mean_net_return_gt,
            "passed": overall["mean_net_return"] > threshold.mean_net_return_gt,
        },
        "median_net_return_gte": {
            "value": overall["median_net_return"],
            "threshold": threshold.median_net_return_gte,
            "passed": overall["median_net_return"] >= threshold.median_net_return_gte,
        },
        "profit_factor_gt": {
            "value": overall["profit_factor"],
            "threshold": threshold.profit_factor_gt,
            "passed": overall["profit_factor"] > threshold.profit_factor_gt,
        },
        "best_3_removed_mean_gte": {
            "value": overall["best_3_removed_mean"],
            "threshold": threshold.best_3_removed_mean_gte,
            "passed": overall["best_3_removed_mean"] >= threshold.best_3_removed_mean_gte,
        },
        "top3_contribution_ratio_lte": {
            "value": overall["top3_contribution_ratio"],
            "threshold": threshold.top3_contribution_ratio_lte,
            "passed": overall["top3_contribution_ratio"] <= threshold.top3_contribution_ratio_lte,
        },
        "min_positive_years_or_regimes": {
            "value": positive_years,
            "threshold": threshold.min_positive_years_or_regimes,
            "passed": positive_years >= threshold.min_positive_years_or_regimes,
        },
    }
    return gates


def decide_validation_status(gates: dict[str, dict[str, Any]], overall: dict[str, Any]) -> str:
    if gates and all(bool(g["passed"]) for g in gates.values()):
        return "VALIDATION_PASSED_NEEDS_ACCOUNT_SIMULATION"
    if overall["valid_trades"] > 0 and (
        overall["mean_net_return"] > 0 or overall["median_net_return"] >= 0 or overall["profit_factor"] > 1.0
    ):
        return "EDGE_NOT_TRADABLE"
    return "FAILED_ARCHIVED"


def render_validation_report(metrics: dict[str, Any]) -> str:
    overall = metrics["overall"]
    gates = metrics["gates"]
    yearly = metrics.get("yearly", [])
    industry = metrics.get("industry", [])
    industry_analysis = metrics.get("industry_analysis", {})
    decision = metrics["decision"]

    lines = [
        f"# {metrics.get('setup_id', 'STOCK_RS_PULLBACK_v1')} 验证报告",
        "",
        "## 1. 结论",
        "",
        f"Decision: **{decision}**",
        "",
        "当前报告只用于研究验证；除非决策为 `VALIDATION_PASSED_NEEDS_ACCOUNT_SIMULATION` 且账户仿真继续通过，否则不得生成正式交易票。",
        "",
        "## 2. 总体指标",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
    ]
    for key in [
        "rows",
        "valid_trades",
        "invalid_trades",
        "mean_net_return",
        "median_net_return",
        "win_rate",
        "profit_factor",
        "best_3_removed_mean",
        "top3_contribution_ratio",
        "net_return_sum",
        "max_gain",
        "max_loss",
        "avg_holding_days",
    ]:
        lines.append(f"| {key} | {_fmt(overall.get(key))} |")

    lines += ["", "## 3. 通过标准", "", "| 检查项 | 实际值 | 门槛 | 是否通过 |", "|---|---:|---:|---|"]
    for key, gate in gates.items():
        lines.append(
            f"| {key} | {_fmt(gate.get('value'))} | {_fmt(gate.get('threshold'))} | {'PASS' if gate.get('passed') else 'FAIL'} |"
        )

    lines += ["", "## 4. 年度表现", "", "| 年份 | 有效交易 | 平均净收益 | 中位净收益 | PF | Top3剔除均值 |", "|---:|---:|---:|---:|---:|---:|"]
    for row in yearly:
        lines.append(
            f"| {row.get('year')} | {row.get('valid_trades')} | {_fmt(row.get('mean_net_return'))} | {_fmt(row.get('median_net_return'))} | {_fmt(row.get('profit_factor'))} | {_fmt(row.get('best_3_removed_mean'))} |"
        )
    if not yearly:
        lines.append("| - | 0 | - | - | - | - |")

    lines += [
        "",
        "## 5. 行业集中度",
        "",
        f"状态: `{industry_analysis.get('status', 'NOT_EVALUABLE')}`。原因: `{industry_analysis.get('reason', 'missing_industry_metadata')}`。该状态不影响最终验证决策。",
        "",
        "| 行业 | 有效交易 | 占比 | 平均净收益 | PF | 净收益合计 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in industry[:20]:
        lines.append(
            f"| {row.get('industry')} | {row.get('valid_trades')} | {_fmt(row.get('trade_share'))} | {_fmt(row.get('mean_net_return'))} | {_fmt(row.get('profit_factor'))} | {_fmt(row.get('net_return_sum'))} |"
        )
    if not industry:
        lines.append("| UNKNOWN | 0 | - | - | - | - |")

    lines += [
        "",
        "## 6. 风控结论",
        "",
        "- `EDGE_NOT_TRADABLE` 不能进入实盘，也不能进入账户仿真。",
        "- `FAILED_ARCHIVED` 必须归档，不能在同一验证集上放宽规则抢救。",
        "- 只有 `VALIDATION_PASSED_NEEDS_ACCOUNT_SIMULATION` 才允许进入3万元账户仿真。",
        "",
    ]
    return "\n".join(lines)


def write_validation_outputs(
    artifacts: dict[str, Any],
    *,
    metrics_path: str | Path,
    report_path: str | Path,
    yearly_path: str | Path | None = None,
    industry_path: str | Path | None = None,
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    metrics_p = Path(metrics_path)
    metrics_p.parent.mkdir(parents=True, exist_ok=True)
    metrics_p.write_text(json.dumps(artifacts["metrics"], ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    paths["metrics"] = metrics_p

    report_p = Path(report_path)
    report_p.parent.mkdir(parents=True, exist_ok=True)
    report_p.write_text(artifacts["report_markdown"], encoding="utf-8")
    paths["report"] = report_p

    if yearly_path is not None:
        yearly_p = Path(yearly_path)
        yearly_p.parent.mkdir(parents=True, exist_ok=True)
        artifacts["yearly"].to_csv(yearly_p, index=False, encoding="utf-8-sig")
        paths["yearly"] = yearly_p
    if industry_path is not None:
        industry_p = Path(industry_path)
        industry_p.parent.mkdir(parents=True, exist_ok=True)
        artifacts["industry"].to_csv(industry_p, index=False, encoding="utf-8-sig")
        paths["industry"] = industry_p
    return paths


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
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
