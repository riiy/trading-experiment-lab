"""Independent, read-only audit of the recent-ten-year raw/qfq candidate."""

from __future__ import annotations

import hashlib
import json
from itertools import zip_longest
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from texperiment.config.loader import load_yaml


ROOT = Path(__file__).parents[2]
OUTPUT = Path(__file__).parent
CANDIDATE = ROOT / "data/processed/formal_snapshots/STOCK_RS_PULLBACK_v1_recent_10y_pair_v3"
SETUP = ROOT / "configs/setups/STOCK_RS_PULLBACK_v1.yaml"
BENCHMARK = ROOT / "data/processed/index_daily.parquet"
AUDIT_ID = "STOCK_RS_PULLBACK_v1_CORE_INPUT_PAIR_AUDIT_v1"
OHLC_COLUMNS = ("open", "high", "low", "close")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    audit = json.loads((CANDIDATE / "pair_audit.json").read_text(encoding="utf-8"))
    setup = load_yaml(SETUP)
    window = setup["validation_window"]
    start = pd.Timestamp(window["indicator_warmup_start_date"])
    end = pd.Timestamp(window["end_date"])
    excluded = set(str(code) for code in setup["universe"]["data_quality_excluded_codes"])
    benchmark_dates = pd.to_datetime(pd.read_parquet(BENCHMARK, columns=["date"])["date"]).drop_duplicates().sort_values()
    benchmark_warmup = benchmark_dates.loc[benchmark_dates.lt(pd.Timestamp(window["start_date"]))].tail(60)
    raw_path, qfq_path = CANDIDATE / "raw_daily.parquet", CANDIDATE / "qfq_daily.parquet"
    raw_file, qfq_file = pq.ParquetFile(raw_path), pq.ParquetFile(qfq_path)
    rows = 0
    violations = {"key_order": 0, "adjustment": 0, "ohlc": 0, "pre_close": 0, "volume": 0, "date": 0, "excluded": 0}
    raw_min = raw_max = None
    unique_codes: set[str] = set()
    columns = ["date", "code", "adj_type", *OHLC_COLUMNS, "pre_close", "volume"]
    for raw_batch, qfq_batch in zip_longest(
        raw_file.iter_batches(batch_size=100_000, columns=columns),
        qfq_file.iter_batches(batch_size=100_000, columns=columns),
    ):
        if raw_batch is None or qfq_batch is None:
            violations["key_order"] += 1
            break
        raw, qfq = raw_batch.to_pandas(), qfq_batch.to_pandas()
        rows += len(raw)
        if len(raw) != len(qfq) or not raw[["date", "code"]].equals(qfq[["date", "code"]]):
            violations["key_order"] += 1
        if not raw["adj_type"].eq("none").all() or not qfq["adj_type"].eq("qfq").all():
            violations["adjustment"] += 1
        for frame in (raw, qfq):
            values = frame[list(OHLC_COLUMNS)].to_numpy(dtype=float)
            violations["ohlc"] += int((~np.isfinite(values) | (values <= 0)).any(axis=1).sum())
            pre_close = frame["pre_close"].to_numpy(dtype=float)
            violations["pre_close"] += int((np.isfinite(pre_close) & (pre_close <= 0)).sum())
        violations["volume"] += int((raw["volume"].to_numpy() != qfq["volume"].to_numpy()).sum())
        dates = pd.to_datetime(raw["date"])
        violations["date"] += int((~dates.between(start, end, inclusive="both")).sum())
        codes = set(raw["code"].astype(str))
        violations["excluded"] += len(codes & excluded)
        unique_codes.update(codes)
        raw_min = dates.min() if raw_min is None else min(raw_min, dates.min())
        raw_max = dates.max() if raw_max is None else max(raw_max, dates.max())
    checks = {
        "candidate_audit_accepted": audit["pair_validation"]["accepted"] is True,
        "candidate_published_atomically": audit["publication"]["atomic_rename_completed"] is True,
        "raw_sha256_matches_candidate_audit": sha256(raw_path) == audit["outputs"]["raw_daily"]["sha256"],
        "qfq_sha256_matches_candidate_audit": sha256(qfq_path) == audit["outputs"]["qfq_daily"]["sha256"],
        "row_count_matches_candidate_audit": rows == audit["outputs"]["raw_daily"]["rows"] == audit["outputs"]["qfq_daily"]["rows"],
        "primary_keys_and_order_match": violations["key_order"] == 0,
        "adjustment_types_match": violations["adjustment"] == 0,
        "ohlc_is_finite_positive": violations["ohlc"] == 0,
        "pre_close_is_positive_when_present": violations["pre_close"] == 0,
        "volume_matches": violations["volume"] == 0,
        "dates_within_global_scope": violations["date"] == 0 and raw_min == start and raw_max == end,
        "configured_excluded_codes_absent": violations["excluded"] == 0,
        "unique_codes_match_candidate_audit": len(unique_codes) == audit["outputs"]["raw_daily"]["unique_codes"],
        "mapping_fully_evaluable": audit["mapping_validation"]["unevaluable_rows"] == 0,
        "benchmark_derives_warmup_start": len(benchmark_warmup) == 60 and benchmark_warmup.iloc[0] == start,
        "formal_recalculation_not_performed": audit["safety"]["full_recalculation_performed"] is False,
        "trading_disabled": audit["safety"]["trading_allowed"] is False,
    }
    if not all(checks.values()):
        raise SystemExit(f"core input pair audit failed: {sorted(key for key, value in checks.items() if not value)}")
    payload = {
        "audit_id": AUDIT_ID,
        "candidate_root": str(CANDIDATE.relative_to(ROOT)),
        "candidate_audit_sha256": sha256(CANDIDATE / "pair_audit.json"),
        "checks": checks,
        "profile": {"rows": rows, "unique_codes": len(unique_codes), "min_date": str(raw_min.date()), "max_date": str(raw_max.date())},
        "violations": violations,
        "decision": "CORE_INPUT_PAIR_AUDIT_PASSED",
        "formal_input_published": False,
        "formal_recalculation_run_authorized": False,
        "trading_allowed": False,
    }
    (OUTPUT / "audit_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
