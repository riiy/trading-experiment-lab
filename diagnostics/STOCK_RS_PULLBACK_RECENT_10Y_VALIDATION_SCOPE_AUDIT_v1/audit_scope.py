"""Independent audit of the frozen recent-ten-year validation scope."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from texperiment.config.loader import load_yaml


ROOT = Path(__file__).parents[2]
OUTPUT = Path(__file__).parent
SETUP_PATH = ROOT / "configs/setups/STOCK_RS_PULLBACK_v1.yaml"
DIAGNOSTICS_PATH = ROOT / "diagnostics/STOCK_RS_PULLBACK_v1_MAPPING_UNEVALUABLE_DIAGNOSTICS_v1/unevaluable_rows.parquet"
FORMAL_OUTPUT_ROOT = ROOT / "data/recalculations/STOCK_RS_PULLBACK_v1_RECALCULATED"
IMPLEMENTATION_COMMIT = "c3e8b68"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    setup = load_yaml(SETUP_PATH)
    window = setup["validation_window"]
    start = pd.Timestamp(window["start_date"])
    end = pd.Timestamp(window["end_date"])
    warmup_start = pd.Timestamp(window["indicator_warmup_start_date"])
    source = pd.read_parquet(DIAGNOSTICS_PATH, columns=["code", "date"])
    source["date"] = pd.to_datetime(source["date"])
    in_window = source.loc[source["date"].between(start, end, inclusive="both")]
    expected_codes = tuple(sorted(in_window["code"].astype(str).unique()))
    configured_codes = tuple(sorted(str(code) for code in setup["universe"]["data_quality_excluded_codes"]))
    checks = {
        "window_is_fixed": (str(start.date()), str(end.date())) == ("2016-07-17", "2026-07-17"),
        "warmup_is_60": int(window["indicator_warmup_trading_days"]) == 60,
        "warmup_start_is_frozen": str(warmup_start.date()) == "2016-04-20",
        "recent_ambiguous_rows_is_30": len(in_window) == 30,
        "recent_ambiguous_codes_is_21": len(expected_codes) == 21,
        "configured_exclusion_codes_match_diagnostics": configured_codes == expected_codes,
        "paired_candidate_absent": not (ROOT / "data/processed/formal_snapshots/STOCK_RS_PULLBACK_v1_20260717_paired_v2").exists(),
        "formal_manifest_absent": not FORMAL_OUTPUT_ROOT.exists(),
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise SystemExit(f"recent-ten-year validation scope audit failed: {failed}")
    payload = {
        "audit_id": "STOCK_RS_PULLBACK_RECENT_10Y_VALIDATION_SCOPE_AUDIT_v1",
        "implementation_commit": IMPLEMENTATION_COMMIT,
        "production_changes_allowed": False,
        "checks": checks,
        "validation_window": {
            "start_date": str(start.date()),
            "end_date": str(end.date()),
            "indicator_warmup_trading_days": int(window["indicator_warmup_trading_days"]),
            "indicator_warmup_start_date": str(warmup_start.date()),
        },
        "mapping_ambiguity": {
            "rows_in_window": len(in_window),
            "excluded_code_count": len(configured_codes),
            "excluded_codes": list(configured_codes),
            "source_diagnostics": str(DIAGNOSTICS_PATH.relative_to(ROOT)),
        },
        "hashes": {
            "setup_config_sha256": sha256(SETUP_PATH),
            "signal_sha256": sha256(ROOT / "src/texperiment/setups/stock_rs_pullback_v1/signal.py"),
            "universe_sha256": sha256(ROOT / "src/texperiment/universe/a_share.py"),
            "validator_sha256": sha256(ROOT / "src/texperiment/config/validator.py"),
        },
        "decision": "RECENT_10Y_VALIDATION_SCOPE_AUDIT_PASSED",
        "formal_input_published": False,
        "formal_recalculation_performed": False,
        "strategy_decision_generated": False,
        "trading_allowed": False,
    }
    (OUTPUT / "audit_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
