"""Independent re-audit of the fixed global warmup boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from texperiment.config.loader import load_yaml


ROOT = Path(__file__).parents[2]
OUTPUT = Path(__file__).parent
SETUP_PATH = ROOT / "configs/setups/STOCK_RS_PULLBACK_v1.yaml"
BENCHMARK_PATH = ROOT / "data/processed/index_daily.parquet"
DIAGNOSTICS_PATH = ROOT / "diagnostics/STOCK_RS_PULLBACK_v1_MAPPING_UNEVALUABLE_DIAGNOSTICS_v1/unevaluable_rows.parquet"
IMPLEMENTATION_COMMIT = "1dedf1e"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    setup = load_yaml(SETUP_PATH)
    window = setup["validation_window"]
    start = pd.Timestamp(window["start_date"])
    end = pd.Timestamp(window["end_date"])
    warmup_start = pd.Timestamp(window["indicator_warmup_start_date"])
    benchmark_dates = pd.to_datetime(pd.read_parquet(BENCHMARK_PATH, columns=["date"])["date"]).drop_duplicates().sort_values()
    derived_warmup = benchmark_dates.loc[benchmark_dates.lt(start)].tail(int(window["indicator_warmup_trading_days"]))
    source = pd.read_parquet(DIAGNOSTICS_PATH, columns=["code", "date"])
    source["date"] = pd.to_datetime(source["date"])
    in_window = source.loc[source["date"].between(start, end, inclusive="both")]
    expected_codes = tuple(sorted(in_window["code"].astype(str).unique()))
    configured_codes = tuple(sorted(str(code) for code in setup["universe"]["data_quality_excluded_codes"]))
    checks = {
        "window_is_fixed": (str(start.date()), str(end.date())) == ("2016-07-17", "2026-07-17"),
        "warmup_is_60": int(window["indicator_warmup_trading_days"]) == 60,
        "warmup_start_matches_benchmark_calendar": len(derived_warmup) == 60 and warmup_start == derived_warmup.iloc[0],
        "recent_ambiguous_rows_is_30": len(in_window) == 30,
        "recent_ambiguous_codes_is_21": len(expected_codes) == 21,
        "configured_exclusion_codes_match_diagnostics": configured_codes == expected_codes,
        "prior_candidates_absent": not any(
            (ROOT / "data/processed/formal_snapshots" / name).exists()
            for name in ("STOCK_RS_PULLBACK_v1_recent_10y_pair_v1", "STOCK_RS_PULLBACK_v1_recent_10y_pair_v2")
        ),
        "formal_recalculation_output_absent": not (ROOT / "data/recalculations/STOCK_RS_PULLBACK_v1_RECALCULATED").exists(),
    }
    if not all(checks.values()):
        raise SystemExit(f"recent-ten-year validation scope re-audit failed: {sorted(key for key, value in checks.items() if not value)}")
    payload = {
        "audit_id": "STOCK_RS_PULLBACK_RECENT_10Y_VALIDATION_SCOPE_AUDIT_v2",
        "implementation_commit": IMPLEMENTATION_COMMIT,
        "checks": checks,
        "validation_window": {
            "start_date": str(start.date()),
            "end_date": str(end.date()),
            "indicator_warmup_trading_days": int(window["indicator_warmup_trading_days"]),
            "indicator_warmup_start_date": str(warmup_start.date()),
            "benchmark_calendar": str(BENCHMARK_PATH.relative_to(ROOT)),
        },
        "mapping_ambiguity": {
            "rows_in_window": len(in_window),
            "excluded_code_count": len(configured_codes),
            "excluded_codes": list(configured_codes),
        },
        "hashes": {
            "setup_config_sha256": sha256(SETUP_PATH),
            "core_input_pair_sha256": sha256(ROOT / "src/texperiment/data/core_input_pair.py"),
            "cli_sha256": sha256(ROOT / "src/texperiment/cli.py"),
            "validator_sha256": sha256(ROOT / "src/texperiment/config/validator.py"),
        },
        "decision": "RECENT_10Y_VALIDATION_SCOPE_REAUDIT_PASSED",
        "formal_input_published": False,
        "formal_recalculation_performed": False,
        "strategy_decision_generated": False,
        "trading_allowed": False,
    }
    (OUTPUT / "audit_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
