from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from texperiment.audit.manifest import sha256_directory, sha256_file
from texperiment.audit.rebuilder import audit_trade, load_parquet_for_codes
from texperiment.backtest.engine import run_stock_rs_pullback_backtest
from texperiment.data.tdx_paired_source import apply_daily_ratio_mapping, refit_affine_adjustment_fields
from texperiment.market_rules.price_limit import enrich_price_limit_fields

DATA_LIMITATION_REASONS = {
    "invalid_exit_fillability_unknown",
    "invalid_inconsistent_price_layers",
    "invalid_missing_adjustment_factor",
    "invalid_missing_price_data",
    "invalid_missing_raw_open",
    "invalid_open_fillability_unknown",
}


def run_locked_sample_remediation(
    project_root: str | Path,
    output_dir: str | Path,
    *,
    daily_ratio_fallback_codes: set[str] | None = None,
    historical_st_overrides: dict[tuple[str, str], str] | None = None,
) -> dict[str, Path]:
    root = Path(project_root)
    output = Path(output_dir)
    samples_path = root / "diagnostics/STOCK_RS_PULLBACK_v1/STOCK_RS_PULLBACK_v1_audit_samples.csv"
    samples = pd.read_csv(samples_path)
    if len(samples) != 50 or samples["signal_id"].nunique() != 50:
        raise ValueError("remediation requires exactly 50 locked unique signal IDs")

    all_signals = pd.read_csv(root / "data/signals/STOCK_RS_PULLBACK_v1_signals.csv")
    signals = samples[["signal_id"]].merge(all_signals, on="signal_id", how="left", validate="one_to_one")
    if signals["status"].isna().any():
        raise ValueError("locked sample signal missing from frozen signals")
    codes = set(samples["code"].astype(str))
    ratio_codes = daily_ratio_fallback_codes or set()
    st_overrides = historical_st_overrides or {}
    bars = _load_remediation_bars(
        root,
        codes,
        daily_ratio_fallback_codes=ratio_codes,
        historical_st_overrides=st_overrides,
    )
    trades = run_stock_rs_pullback_backtest(signals, bars)
    if len(trades) != 50 or trades["signal_id"].nunique() != 50:
        raise ValueError("remediation engine did not return exactly 50 signal outcomes")

    sample_metadata = samples[["signal_id", "trade_id", "audit_category", "status", "invalid_reason"]].rename(columns={
        "trade_id": "original_trade_id",
        "status": "original_status",
        "invalid_reason": "original_invalid_reason",
    })
    trades = trades.merge(sample_metadata, on="signal_id", how="left", validate="one_to_one")

    indicators = load_parquet_for_codes(root / "data/processed/a_share_indicators.parquet", codes)
    universe = load_parquet_for_codes(root / "data/processed/a_share_universe_full.parquet", codes)
    details = []
    for trade in trades.to_dict("records"):
        signal = signals.loc[signals["signal_id"].eq(trade["signal_id"])].iloc[0].to_dict()
        details.append(audit_trade(
            trade,
            signal=signal,
            daily_bars=bars,
            indicators=indicators,
            universe=universe,
        ))
    detail = pd.concat(details, ignore_index=True)
    reviewed_at = datetime.now(timezone.utc).isoformat()
    detail["reviewer"] = "OpenCode (assistant)"
    detail["reviewed_at"] = reviewed_at
    detail["notes"] = detail["verdict"].map(_review_note)

    summary = _summarize(samples, trades, detail)
    summary["daily_ratio_fallback_codes"] = sorted(ratio_codes)
    summary["historical_st_point_overrides"] = len(st_overrides)
    manifest = {
        "task": "ENGINE_REMEDIATION_A_SHARE_EXECUTION_v1",
        "baseline_commit": "1cbfa676459e31075c479826cb68dc58b3beeec8",
        "generated_at": reviewed_at,
        "locked_sample_sha256": sha256_file(samples_path),
        "frozen_qfq_sha256": sha256_file(root / "data/processed/a_share_daily.parquet"),
        "remediation_daily_sha256": sha256_file(root / "data/processed/a_share_daily_remediation.parquet"),
        "engine_source_sha256": sha256_directory(root / "src/texperiment"),
        "sample_count": 50,
        "full_recalculation_performed": False,
        "historical_st_repaired": False,
        "daily_ratio_fallback": {
            "formula": "adj_factor = qfq_close / raw_close; adj_offset = 0",
            "scope": "flat OHLC rows with UNKNOWN_AFFINE_FIT only",
            "codes": sorted(ratio_codes),
        },
        "historical_st_point_overrides": [
            {
                "code": code,
                "date": trade_date,
                "status": status,
                "provenance": "USER_CONFIRMED_DIRECT_DATABASE_QUERY",
            }
            for (code, trade_date), status in sorted(st_overrides.items())
        ],
    }

    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "manifest": output / "STOCK_RS_PULLBACK_v1_remediation_manifest.json",
        "trades": output / "STOCK_RS_PULLBACK_v1_remediation_trades.csv",
        "details": output / "STOCK_RS_PULLBACK_v1_remediation_audit_details.csv",
        "summary": output / "STOCK_RS_PULLBACK_v1_remediation_summary.json",
        "report": output / "REMEDIATION_AUDIT_STOCK_RS_PULLBACK_v1.md",
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    trades.to_csv(paths["trades"], index=False, encoding="utf-8-sig")
    detail.to_csv(paths["details"], index=False, encoding="utf-8-sig")
    paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["report"].write_text(_render_report(summary, trades, detail), encoding="utf-8")
    return paths


def _load_remediation_bars(
    root: Path,
    codes: set[str],
    *,
    daily_ratio_fallback_codes: set[str] | None = None,
    historical_st_overrides: dict[tuple[str, str], str] | None = None,
) -> pd.DataFrame:
    columns = [
        "date", "code", "board", "listing_date", "listing_trading_day", "historical_st_status",
        "raw_pre_close", "raw_open", "raw_high", "raw_low", "raw_close",
        "adj_open", "adj_high", "adj_low", "adj_close", "hfq_open", "hfq_high", "hfq_low", "hfq_close",
        "adj_factor", "adj_offset", "adj_type", "volume", "is_suspended",
        "opening_auction_fill_status", "closing_auction_fill_status", "adjustment_status",
    ]
    bars = load_parquet_for_codes(root / "data/processed/a_share_daily_remediation.parquet", codes, columns=columns)
    frozen = load_parquet_for_codes(
        root / "data/processed/a_share_daily.parquet",
        codes,
        columns=["date", "code", "open", "high", "low", "close"],
    ).rename(columns={field: f"frozen_{field}" for field in ("open", "high", "low", "close")})
    bars = bars.merge(frozen, on=["date", "code"], how="left", validate="one_to_one")
    for field in ("open", "high", "low", "close"):
        frozen_field = f"frozen_{field}"
        bars[f"adj_{field}"] = bars[frozen_field].combine_first(bars[f"adj_{field}"])
        bars[field] = bars[f"adj_{field}"]
    bars = bars.drop(columns=[f"frozen_{field}" for field in ("open", "high", "low", "close")])
    bars = refit_affine_adjustment_fields(bars)
    if daily_ratio_fallback_codes:
        bars = apply_daily_ratio_mapping(bars, daily_ratio_fallback_codes)
    for (code, trade_date), status in (historical_st_overrides or {}).items():
        selected = bars["code"].astype(str).eq(code) & bars["date"].eq(pd.Timestamp(trade_date))
        if int(selected.sum()) != 1:
            raise ValueError(f"historical ST override did not match exactly one row: {code} {trade_date}")
        bars.loc[selected, "historical_st_status"] = status
    bars = enrich_price_limit_fields(bars)
    return bars.sort_values(["code", "date"]).reset_index(drop=True)


def _summarize(samples: pd.DataFrame, trades: pd.DataFrame, details: pd.DataFrame) -> dict[str, Any]:
    original_invalid = set(samples.loc[samples["invalid_reason"].eq("invalid_limit_up_cannot_buy"), "signal_id"])
    resolved = int(trades.loc[trades["signal_id"].isin(original_invalid), "status"].eq("valid_trade").sum())
    invalid = trades.loc[trades["status"].ne("valid_trade")]
    data_limited = int(invalid["invalid_reason"].isin(DATA_LIMITATION_REASONS).sum())
    unexpected_invalid = int(len(invalid) - data_limited)
    critical_failures = int(((details["severity"] == "CRITICAL") & details["verdict"].eq("FAIL")).sum())
    blocking_not_evaluable = int((details["blocking"].astype(bool) & details["verdict"].str.startswith("NOT_EVALUABLE")).sum())
    check_not_evaluable = int(details["verdict"].str.startswith("NOT_EVALUABLE").sum())
    material_blocking_trades = data_limited + unexpected_invalid
    critical_error_remaining = bool(critical_failures or unexpected_invalid or resolved != len(original_invalid))
    if critical_error_remaining:
        decision = "REMEDIATION_ERROR_REMAINS"
    elif material_blocking_trades:
        decision = "REMEDIATION_INCONCLUSIVE_DATA_LIMITATION"
    else:
        decision = "REMEDIATION_AUDIT_PASSED"
    return {
        "decision": decision,
        "sample_count": 50,
        "original_limit_up_invalid_samples": len(original_invalid),
        "original_limit_up_errors_resolved": resolved,
        "remediated_valid_trades": int(trades["status"].eq("valid_trade").sum()),
        "data_limited_trade_outcomes": data_limited,
        "unexpected_invalid_outcomes": unexpected_invalid,
        "critical_failures": critical_failures,
        "critical_engine_error_remaining": critical_error_remaining,
        "check_not_evaluable_count": check_not_evaluable,
        "material_blocking_trade_count": material_blocking_trades,
        "blocking_not_evaluable": blocking_not_evaluable,
        "historical_st_repaired": False,
        "full_recalculation_performed": False,
        "new_setup_started": False,
    }


def _review_note(verdict: str) -> str:
    if verdict.startswith("PASS"):
        return "Reviewed against locked signal, frozen qfq layer, remediation raw/hfq layers, and independent reconstruction."
    if verdict.startswith("NOT_EVALUABLE"):
        return "Confirmed blocking data limitation; no value imputed."
    return "Reviewed failure requires remediation decision precedence."


def _render_report(summary: dict[str, Any], trades: pd.DataFrame, details: pd.DataFrame) -> str:
    invalid = trades.loc[trades["status"].ne("valid_trade"), ["signal_id", "code", "invalid_reason"]]
    lines = [
        "# REMEDIATION_AUDIT_STOCK_RS_PULLBACK_v1",
        "",
        f"Decision: **{summary['decision']}**",
        "",
        "仅重放锁定50个 signal_id；未运行全量回测，未修复历史 ST，未覆盖任何原始产物。",
        "",
        "## Outcomes",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- `{key}`: `{value}`")
    lines += ["", "## Data-limited outcomes", ""]
    if invalid.empty:
        lines.append("- None")
    else:
        for row in invalid.to_dict("records"):
            lines.append(f"- `{row['code']}` `{row['signal_id']}`: `{row['invalid_reason']}`")
    lines += ["", "## Check verdicts", ""]
    for verdict, count in details["verdict"].value_counts().sort_index().items():
        lines.append(f"- `{verdict}`: {count}")
    return "\n".join(lines) + "\n"
