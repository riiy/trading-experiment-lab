"""Produce a frozen, row-level explanation of raw/qfq mapping failures.

This is diagnostics-only code. It mirrors the audited source pairing filter and
never writes to a production input or changes a mapping decision.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from texperiment.data.core_input_pair import _PRICE_COLUMNS, _read_price_file, _unique_file_map
from texperiment.data.tdx_paired_source import apply_daily_ratio_mapping, fit_raw_qfq_mapping


ROOT = Path(__file__).parent
RAW_ROOT = Path("data/raw/tdx_text/raw")
QFQ_ROOT = Path("data/raw/tdx_text/qfq")
HFQ_ROOT = Path("data/raw/tdx_text/hfq")
TICK = 0.01
HALF_TICK = TICK / 2


def interval_affine(raw: np.ndarray, qfq: np.ndarray) -> tuple[bool, float, float, float, float]:
    """Prove whether displayed raw/qfq price intervals admit one a*x+b map."""
    raw_low, raw_high = raw - HALF_TICK, raw + HALF_TICK
    qfq_low, qfq_high = qfq - HALF_TICK, qfq + HALF_TICK
    lower, upper = 0.0, np.inf
    # max_i(qlo_i-a*rhi_i) <= min_j(qhi_j-a*rlo_j). Intersect all
    # linear constraints exactly; this handles interval-valued raw prices too.
    for qlo, rhi in zip(qfq_low, raw_high):
        for qhi, rlo in zip(qfq_high, raw_low):
            coefficient = rlo - rhi
            rhs = qhi - qlo
            if abs(coefficient) < 1e-15:
                if rhs < 0:
                    return False, np.nan, np.nan, np.nan, np.nan
            elif coefficient > 0:
                upper = min(upper, rhs / coefficient)
            else:
                lower = max(lower, rhs / coefficient)
    lower = max(lower, np.nextafter(0.0, 1.0))
    if not np.isfinite(upper) or lower > upper:
        return False, np.nan, np.nan, np.nan, np.nan
    scale = (lower + upper) / 2 if np.isfinite(lower + upper) else lower
    b_low = max(qfq_low - scale * raw_high)
    b_high = min(qfq_high - scale * raw_low)
    if b_low > b_high:
        return False, np.nan, np.nan, np.nan, np.nan
    return True, lower, upper, b_low, b_high


def interval_ratio(raw: np.ndarray, qfq: np.ndarray) -> tuple[bool, float, float]:
    """Prove whether displayed price intervals admit one strictly positive ratio."""
    raw_low, raw_high = raw - HALF_TICK, raw + HALF_TICK
    qfq_low, qfq_high = qfq - HALF_TICK, qfq + HALF_TICK
    lower = max(qfq_low / raw_high)
    upper = min(qfq_high / raw_low)
    lower = max(lower, np.nextafter(0.0, 1.0))
    return bool(lower <= upper), float(lower), float(upper)


def _cause(row: pd.Series) -> str:
    qfq_flat = int(row["qfq_unique_ohlc_count"]) == 1
    if qfq_flat and bool(row["rounding_interval_affine_feasible"]) and bool(row["rounding_interval_ratio_feasible"]):
        return "DEGENERATE_QFQ_OHLC_AFFINE_AND_RATIO_INTERVAL_FEASIBLE"
    if qfq_flat and bool(row["rounding_interval_affine_feasible"]):
        return "DEGENERATE_QFQ_OHLC_AFFINE_INTERVAL_FEASIBLE"
    if qfq_flat and bool(row["rounding_interval_ratio_feasible"]):
        return "DEGENERATE_QFQ_OHLC_RATIO_INTERVAL_FEASIBLE"
    if int(row["raw_unique_ohlc_count"]) == 1 or qfq_flat:
        return "DEGENERATE_OHLC_PARAMETER_UNIDENTIFIABLE"
    if bool(row["neighbor_mapping_changed"]):
        return "CORPORATE_ACTION_ADJACENT_SUSPECTED"
    return "SOURCE_MAPPING_INCONSISTENT_OR_PRECISION_UNPROVEN"


def main() -> None:
    raw_files = _unique_file_map(RAW_ROOT)
    qfq_files = _unique_file_map(QFQ_ROOT)
    hfq_files = _unique_file_map(HFQ_ROOT)
    names = sorted(set(raw_files) & set(qfq_files) & set(hfq_files))
    failures: list[pd.DataFrame] = []
    total_retained = 0

    for name in names:
        raw_source = _read_price_file(raw_files[name], "raw")
        qfq_source = _read_price_file(qfq_files[name], "adj")
        hfq_source = _read_price_file(hfq_files[name], "hfq")
        merged = raw_source.merge(qfq_source, on="date", how="inner", validate="one_to_one")
        merged = merged.merge(hfq_source, on="date", how="inner", validate="one_to_one")
        if merged.empty:
            continue
        merged["raw_pre_close"] = merged["raw_close"].shift(1)
        merged["adj_pre_close"] = merged["adj_close"].shift(1)
        merged["hfq_pre_close"] = merged["hfq_close"].shift(1)
        valid = pd.Series(True, index=merged.index)
        for prefix in ("raw", "adj", "hfq"):
            cols = [f"{prefix}_{field}" for field in _PRICE_COLUMNS]
            valid &= merged[cols].notna().all(axis=1) & (merged[cols] > 0).all(axis=1)
        has_predecessor = pd.Series(range(len(merged)), index=merged.index).gt(0)
        valid &= ~(valid & has_predecessor & ((merged["raw_pre_close"] <= 0) | merged["raw_pre_close"].isna() | (merged["adj_pre_close"] <= 0) | merged["adj_pre_close"].isna()))
        retained = merged.loc[valid].copy()
        if retained.empty:
            continue
        market, ticker = raw_files[name].stem.split("#", maxsplit=1)
        code = f"{ticker}.{market.upper()}"
        raw_map = pd.DataFrame({"code": [code] * len(retained)})
        qfq_map = pd.DataFrame(index=range(len(retained)))
        for field in _PRICE_COLUMNS:
            raw_map[f"raw_{field}"] = retained[f"raw_{field}"].to_numpy()
            qfq_map[f"adj_{field}"] = retained[f"adj_{field}"].to_numpy()
        mappings = apply_daily_ratio_mapping(fit_raw_qfq_mapping(pd.concat([raw_map, qfq_map], axis=1)))
        total_retained += len(retained)
        unknown = ~mappings["adjustment_status"].isin({"KNOWN_AFFINE_RAW_QFQ_VALIDATED", "DAILY_RATIO_FALLBACK"})
        if not unknown.any():
            continue
        output = pd.DataFrame({"code": code, "date": retained["date"].to_numpy()})
        for prefix in ("raw", "adj", "hfq"):
            for field in (*_PRICE_COLUMNS, "pre_close"):
                output[f"{prefix}_{field}"] = retained[f"{prefix}_{field}"].to_numpy()
        output["volume"] = retained["raw_volume"].to_numpy()
        output["amount"] = retained["raw_amount"].to_numpy()
        output["raw_unique_ohlc_count"] = retained[[f"raw_{field}" for field in _PRICE_COLUMNS]].nunique(axis=1).to_numpy()
        output["qfq_unique_ohlc_count"] = retained[[f"adj_{field}" for field in _PRICE_COLUMNS]].nunique(axis=1).to_numpy()
        output["hfq_unique_ohlc_count"] = retained[[f"hfq_{field}" for field in _PRICE_COLUMNS]].nunique(axis=1).to_numpy()
        raw_prices = retained[[f"raw_{field}" for field in _PRICE_COLUMNS]].to_numpy(dtype=float)
        qfq_prices = retained[[f"adj_{field}" for field in _PRICE_COLUMNS]].to_numpy(dtype=float)
        ratios = qfq_prices / raw_prices
        output["affine_scale"] = mappings["adj_factor"].to_numpy()
        output["affine_offset"] = mappings["adj_offset"].to_numpy()
        output["affine_max_abs_residual"] = mappings["adjustment_fit_error"].to_numpy()
        output["affine_max_tick_residual"] = mappings["adjustment_fit_error"].to_numpy() / TICK
        for index, field in enumerate(_PRICE_COLUMNS):
            output[f"{field}_ratio"] = ratios[:, index]
        output["ratio_min"] = np.nanmin(ratios, axis=1)
        output["ratio_max"] = np.nanmax(ratios, axis=1)
        output["ratio_spread"] = output["ratio_max"] - output["ratio_min"]
        output["ratio_relative_spread"] = output["ratio_spread"] / np.maximum(np.abs(output["ratio_max"]), 1.0)
        output["previous_day_mapping_scale"] = mappings["adj_factor"].shift(1).to_numpy()
        output["previous_day_mapping_offset"] = mappings["adj_offset"].shift(1).to_numpy()
        output["next_day_mapping_scale"] = mappings["adj_factor"].shift(-1).to_numpy()
        output["next_day_mapping_offset"] = mappings["adj_offset"].shift(-1).to_numpy()
        output["mapping_failure_reason"] = mappings["adjustment_status"].to_numpy()
        output = output.loc[unknown].copy()
        interval_results = [interval_affine(raw, qfq) for raw, qfq in zip(raw_prices[unknown], qfq_prices[unknown])]
        ratio_results = [interval_ratio(raw, qfq) for raw, qfq in zip(raw_prices[unknown], qfq_prices[unknown])]
        output[["rounding_interval_affine_feasible", "rounding_affine_scale_min", "rounding_affine_scale_max", "rounding_affine_offset_min", "rounding_affine_offset_max"]] = interval_results
        output[["rounding_interval_ratio_feasible", "rounding_ratio_min", "rounding_ratio_max"]] = ratio_results
        previous_scale = output["previous_day_mapping_scale"]
        next_scale = output["next_day_mapping_scale"]
        previous_offset = output["previous_day_mapping_offset"]
        next_offset = output["next_day_mapping_offset"]
        output["neighbor_mapping_stable"] = (previous_scale.notna() & next_scale.notna() & np.isclose(previous_scale, next_scale, rtol=1e-10, atol=1e-12) & np.isclose(previous_offset, next_offset, rtol=1e-10, atol=1e-12))
        output["neighbor_mapping_changed"] = previous_scale.notna() & next_scale.notna() & ~output["neighbor_mapping_stable"]
        output["mapping_failure_reason"] = output.apply(_cause, axis=1)
        failures.append(output)

    result = pd.concat(failures, ignore_index=True) if failures else pd.DataFrame()
    if len(result) != 3306:
        raise SystemExit(f"frozen count mismatch: expected 3306, got {len(result)}")
    if total_retained != 15925710:
        raise SystemExit(f"frozen retained count mismatch: expected 15925710, got {total_retained}")
    result.to_parquet(ROOT / "unevaluable_rows.parquet", index=False)
    result.to_csv(ROOT / "unevaluable_rows.csv", index=False)
    result.to_parquet(ROOT / "mapping_residuals.parquet", index=False)
    by_code = result.groupby("code", as_index=False).agg(rows=("date", "size"), min_date=("date", "min"), max_date=("date", "max"), causes=("mapping_failure_reason", lambda values: "|".join(sorted(set(values)))))
    by_code.to_csv(ROOT / "distribution_by_code.csv", index=False)
    result.assign(year=pd.to_datetime(result["date"]).dt.year).groupby("year", as_index=False).size().rename(columns={"size": "rows"}).to_csv(ROOT / "distribution_by_year.csv", index=False)
    result.groupby("mapping_failure_reason", as_index=False).size().rename(columns={"size": "rows"}).to_csv(ROOT / "distribution_by_cause.csv", index=False)
    min_qfq_price = result[["adj_open", "adj_high", "adj_low", "adj_close"]].min(axis=1)
    price_buckets = pd.cut(
        min_qfq_price,
        bins=[0, 0.5, 1, 2, 5, 10, np.inf],
        right=False,
        labels=["[0,0.5)", "[0.5,1)", "[1,2)", "[2,5)", "[5,10)", "[10,+inf)"],
    )
    price_buckets.value_counts(sort=False).rename_axis("qfq_min_price_bucket").reset_index(name="rows").to_csv(
        ROOT / "distribution_by_price_bucket.csv", index=False
    )
    counts = Counter(result["mapping_failure_reason"])
    top = result["code"].value_counts()
    summary = {
        "retained_rows": total_retained,
        "unevaluable_rows": len(result),
        "affected_codes": int(result["code"].nunique()),
        "max_rows_per_code": int(top.max()),
        "median_rows_per_code": float(top.median()),
        "single_row_codes": int((top == 1).sum()),
        "concentrated_top_10_ratio": float(top.head(10).sum() / len(result)),
        "flat_ohlc_rows": int(((result["raw_unique_ohlc_count"] == 1) | (result["qfq_unique_ohlc_count"] == 1)).sum()),
        "raw_flat_ohlc_rows": int((result["raw_unique_ohlc_count"] == 1).sum()),
        "qfq_flat_ohlc_rows": int((result["qfq_unique_ohlc_count"] == 1).sum()),
        "low_price_qfq_rows": int((result[["adj_open", "adj_high", "adj_low", "adj_close"]].min(axis=1) < 1).sum()),
        "corporate_action_adjacent_rows": int(result["neighbor_mapping_changed"].sum()),
        "cause_counts": dict(sorted(counts.items())),
    }
    (ROOT / "diagnostics_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cases = result.groupby("mapping_failure_reason", group_keys=False).head(5)
    with (ROOT / "representative_cases.md").open("w", encoding="utf-8") as handle:
        handle.write("# Representative Mapping Failures\n\n")
        for cause, frame in cases.groupby("mapping_failure_reason", sort=True):
            handle.write(f"## {cause}\n\n")
            handle.write(frame[["code", "date", "raw_open", "raw_high", "raw_low", "raw_close", "adj_open", "adj_high", "adj_low", "adj_close", "affine_max_abs_residual", "ratio_spread"]].to_markdown(index=False))
            handle.write("\n\n")
    report = [
        "# Mapping Unevaluable Diagnostics v1",
        "",
        "Diagnostics-only replay of the frozen pairing filter and mapping implementation.",
        "No production file, mapping tolerance, paired output, or strategy rule was modified.",
        "",
        "```yaml",
        "decision: MAPPING_DIAGNOSTICS_MIXED_DETERMINISTIC",
        f"retained_rows: {total_retained}",
        f"unevaluable_rows: {len(result)}",
        f"affected_codes: {summary['affected_codes']}",
        f"flat_ohlc_rows: {summary['flat_ohlc_rows']}",
        f"raw_flat_ohlc_rows: {summary['raw_flat_ohlc_rows']}",
        f"qfq_flat_ohlc_rows: {summary['qfq_flat_ohlc_rows']}",
        f"low_price_qfq_rows: {summary['low_price_qfq_rows']}",
        f"corporate_action_adjacent_rows: {summary['corporate_action_adjacent_rows']}",
        "```",
        "",
        "Every failure has a flat qfq OHLC display. Interval feasibility proves that a displayed-price-consistent map exists, but it does not select one unique affine parameter pair. No inheritance rule was applied.",
        "This is diagnostics evidence only. A future mapping rule must be separately specified, implemented, and audited before any candidate can be regenerated.",
    ]
    (ROOT / "diagnostics_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
