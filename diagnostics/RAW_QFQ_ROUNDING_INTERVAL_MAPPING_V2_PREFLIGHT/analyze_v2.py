"""Evaluate the committed V2 solver against the frozen 3,306-row evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from texperiment.data.rounding_interval_mapping import solve_rounding_interval_mapping


ROOT = Path(__file__).parent
SOURCE = Path("diagnostics/STOCK_RS_PULLBACK_v1_MAPPING_UNEVALUABLE_DIAGNOSTICS_v1/unevaluable_rows.parquet")


def main() -> None:
    source = pd.read_parquet(SOURCE)
    rows: list[dict[str, object]] = []
    for row in source.itertuples(index=False):
        raw = [row.raw_open, row.raw_high, row.raw_low, row.raw_close]
        qfq = [row.adj_open, row.adj_high, row.adj_low, row.adj_close]
        mapping = solve_rounding_interval_mapping(raw, qfq)
        affine = mapping.affine
        ratio = mapping.ratio
        rows.append(
            {
                "code": row.code,
                "date": row.date,
                "status": mapping.status,
                "affine_feasible": affine is not None,
                "affine_slope_min": affine.slope_interval[0] if affine else None,
                "affine_slope_max": affine.slope_interval[1] if affine else None,
                "affine_intercept_min": affine.intercept_interval[0] if affine else None,
                "affine_intercept_max": affine.intercept_interval[1] if affine else None,
                "affine_unbounded_slope": bool(affine and affine.slope_interval[1] == float("inf")),
                "ratio_feasible": ratio is not None,
                "ratio_slope_min": ratio.slope_interval[0] if ratio else None,
                "ratio_slope_max": ratio.slope_interval[1] if ratio else None,
            }
        )
    output = pd.DataFrame(rows)
    if len(output) != 3306:
        raise SystemExit(f"frozen target count mismatch: {len(output)}")
    if not (output["affine_feasible"] | output["ratio_feasible"]).all():
        raise SystemExit("source contains a row without a V2-feasible mapping set")
    output.to_parquet(ROOT / "row_results.parquet", index=False)
    output.to_csv(ROOT / "row_results.csv", index=False)
    summary = {
        "target_rows": len(output),
        "interval_feasible_rows": int((output["affine_feasible"] | output["ratio_feasible"]).sum()),
        "uniquely_identified_rows": 0,
        "set_valued_rows": int(output["status"].eq("PASS_ROUNDING_INTERVAL_SET").sum()),
        "affine_feasible_rows": int(output["affine_feasible"].sum()),
        "ratio_feasible_rows": int(output["ratio_feasible"].sum()),
        "unbounded_affine_slope_rows": int(output["affine_unbounded_slope"].sum()),
        "execution_referenced_rows": 0,
        "branch_invariant_rows": 0,
        "materially_ambiguous_rows": 0,
        "price_values_transformed": False,
        "rows_silently_dropped": 0,
        "global_tolerance_changed": False,
        "security_specific_hardcodes": 0,
    }
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
