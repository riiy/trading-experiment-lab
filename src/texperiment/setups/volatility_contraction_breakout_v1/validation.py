from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

from texperiment.account.daily_equity import summarize_daily_equity
from texperiment.metrics.validation import build_validation_artifacts


DEVELOPMENT_START = "2016-07-17"
DEVELOPMENT_END = "2022-07-15"
FINAL_START = "2022-07-18"
FINAL_END = "2026-07-17"


def benchmark_cagr(benchmark_bars: pd.DataFrame, equity_curve: pd.DataFrame, *, benchmark_code: str = "000300.SH") -> dict[str, Any]:
    """Calculate price-index CAGR on the exact account-curve endpoints."""
    if equity_curve.empty:
        raise ValueError("benchmark not evaluable: empty account equity curve")
    required = {"date", "code"}
    missing = sorted(required - set(benchmark_bars.columns))
    if missing:
        raise ValueError(f"benchmark missing required columns: {missing}")
    price_col = "raw_close" if "raw_close" in benchmark_bars.columns else "close"
    if price_col not in benchmark_bars:
        raise ValueError("benchmark missing price-index close")
    start, end = pd.Timestamp(equity_curve.iloc[0]["date"]).normalize(), pd.Timestamp(equity_curve.iloc[-1]["date"]).normalize()
    bench = benchmark_bars.copy(); bench["date"] = pd.to_datetime(bench["date"], errors="coerce").dt.normalize(); bench["code"] = bench["code"].astype(str); bench[price_col] = pd.to_numeric(bench[price_col], errors="coerce")
    bench = bench.loc[bench.code.eq(benchmark_code)].drop_duplicates("date", keep="last").set_index("date")
    if start not in bench.index or end not in bench.index:
        raise ValueError("benchmark not evaluable: missing exact account start or end date")
    first, last = float(bench.loc[start, price_col]), float(bench.loc[end, price_col])
    days = (end - start).days
    if not all(math.isfinite(v) and v > 0 for v in (first, last)) or days <= 0:
        raise ValueError("benchmark not evaluable: invalid price or annualization period")
    cagr = (last / first) ** (365.25 / days) - 1.0
    return {"code": benchmark_code, "return_basis": "price_index", "start_date": str(start.date()), "end_date": str(end.date()), "start_price": first, "end_price": last, "days_for_cagr": days, "benchmark_cagr": cagr}


def build_final_validation_artifacts(
    trades: pd.DataFrame,
    equity_curve: pd.DataFrame,
    benchmark_bars: pd.DataFrame,
    *,
    setup_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply all legacy trade gates plus VCB account and benchmark gates.

    Any missing core sequence is intentionally an exception rather than a soft
    warning, so callers cannot classify incomplete final validation as a pass.
    """
    config = setup_config or {}
    trade_artifacts = build_validation_artifacts(trades, setup_config=config)
    account = summarize_daily_equity(equity_curve)
    benchmark = benchmark_cagr(benchmark_bars, equity_curve, benchmark_code=str(config.get("benchmark", {}).get("code", "000300.SH")))
    required_return = max(0.07, float(benchmark["benchmark_cagr"]) + 0.03)
    existing = trade_artifacts["metrics"]["gates"]
    gates = {**existing,
        "account_cagr": {"value": account["account_cagr"], "threshold": required_return, "passed": account["account_cagr"] is not None and account["account_cagr"] >= required_return},
        "account_max_drawdown": {"value": account["max_drawdown_pct"], "threshold": 0.10, "passed": account["max_drawdown_pct"] <= 0.10 + 1e-12},
        "account_not_frozen": {"value": account["account_frozen"], "threshold": False, "passed": not account["account_frozen"]},
    }
    all_passed = bool(gates) and all(bool(g["passed"]) for g in gates.values())
    metrics = {"setup_id": config.get("setup_id", "VOLATILITY_CONTRACTION_BREAKOUT_v1"), "decision": "FINAL_VALIDATION_PASSED_RESEARCH_ONLY" if all_passed else "FINAL_VALIDATION_FAILED", "trade_validation": trade_artifacts["metrics"], "account": account, "benchmark": benchmark, "relative_annualized_advantage": account["account_cagr"] - benchmark["benchmark_cagr"] if account["account_cagr"] is not None else None, "required_account_cagr": required_return, "gates": gates, "final_validation_window": {"start_date": FINAL_START, "end_date": FINAL_END}}
    return {"metrics": metrics, "yearly": trade_artifacts["yearly"], "industry": trade_artifacts["industry"], "report_markdown": render_final_validation_report(metrics)}


def assert_final_window(curve: pd.DataFrame) -> None:
    if curve.empty or str(pd.Timestamp(curve.iloc[0]["date"]).date()) != FINAL_START or str(pd.Timestamp(curve.iloc[-1]["date"]).date()) != FINAL_END:
        raise ValueError(f"final validation must use the fixed {FINAL_START} to {FINAL_END} account dates")


def render_final_validation_report(metrics: dict[str, Any]) -> str:
    lines = [f"# {metrics['setup_id']} 最终验证报告", "", f"Decision: **{metrics['decision']}**", "", "沪深300使用 `000300.SH` 价格指数口径，不是全收益指数。", "", "| Gate | Value | Threshold | Result |", "|---|---:|---:|---|"]
    for name, gate in metrics["gates"].items():
        lines.append(f"| {name} | {_fmt(gate['value'])} | {_fmt(gate['threshold'])} | {'PASS' if gate['passed'] else 'FAIL'} |")
    account, benchmark = metrics["account"], metrics["benchmark"]
    lines += ["", "| Account metric | Value |", "|---|---:|", f"| account CAGR | {_fmt(account['account_cagr'])} |", f"| maximum drawdown | {_fmt(account['max_drawdown_pct'])} |", f"| benchmark CAGR | {_fmt(benchmark['benchmark_cagr'])} |", f"| relative annualized advantage | {_fmt(metrics['relative_annualized_advantage'])} |", "", "通过也只允许进入账户仿真研究状态；不得生成交易票或开启交易。", ""]
    return "\n".join(lines)


def write_final_validation_outputs(artifacts: dict[str, Any], *, metrics_path: str | Path, report_path: str | Path, equity_path: str | Path | None = None, equity_curve: pd.DataFrame | None = None) -> dict[str, Path]:
    import json
    paths: dict[str, Path] = {}; metrics_p, report_p = Path(metrics_path), Path(report_path)
    metrics_p.parent.mkdir(parents=True, exist_ok=True); report_p.parent.mkdir(parents=True, exist_ok=True)
    metrics_p.write_text(json.dumps(artifacts["metrics"], ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"); report_p.write_text(artifacts["report_markdown"], encoding="utf-8")
    paths.update({"metrics": metrics_p, "report": report_p})
    if equity_path is not None and equity_curve is not None:
        target = Path(equity_path); target.parent.mkdir(parents=True, exist_ok=True); equity_curve.to_parquet(target, index=False); paths["equity_curve"] = target
    return paths


def _fmt(value: Any) -> str:
    if value is None: return "-"
    if isinstance(value, bool): return "true" if value else "false"
    return f"{float(value):.6f}" if isinstance(value, (int, float)) else str(value)
