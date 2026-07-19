from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from texperiment.data.normalizer import normalize_daily_bars
from texperiment.data.schema import BOOLEAN_COLUMNS, CANONICAL_DAILY_COLUMNS, NUMERIC_COLUMNS
from texperiment.data.tdx_paired_source import (
    _read_price_file,
    _read_name,
    _unique_file_map,
    _validate_adjustment_mode,
    apply_daily_ratio_mapping,
    fit_raw_qfq_mapping,
)

_PRICE_COLUMNS = ("open", "high", "low", "close")
PAIRING_POLICY = "PAIRED_NON_POSITIVE_OHLC_FILTER_V1"
PRE_CLOSE_POLICY = "IMMEDIATE_SOURCE_PREDECESSOR_V1"


@dataclass(frozen=True)
class CoreInputPairResult:
    output_root: Path
    raw_daily: Path
    qfq_daily: Path
    audit: Path
    report: dict[str, Any]


class CoreInputPairError(ValueError):
    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


def prepare_tdx_core_input_pair(
    raw_input: str | Path,
    qfq_input: str | Path,
    output_root: str | Path,
    *,
    hfq_input: str | Path | None = None,
    diagnostics_path: str | Path | None = None,
) -> CoreInputPairResult:
    """Build a transactionally published canonical raw/qfq input pair.

    Source rows are never price-transformed. A key is retained only when every
    supplied price layer has finite, positive OHLC. Source key mismatches,
    duplicate output keys, volume mismatches, or unevaluable raw/qfq mappings
    prevent candidate publication.
    """
    raw_root, qfq_root = Path(raw_input), Path(qfq_input)
    hfq_root = Path(hfq_input) if hfq_input is not None else None
    final_root = Path(output_root)
    if final_root.exists():
        raise FileExistsError(f"core input pair output already exists: {final_root}")

    tmp_root = final_root.parent / f".{final_root.name}.{os.getpid()}.tmp"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_root.mkdir(parents=True)
    report = _base_report(raw_root, qfq_root, hfq_root)
    raw_writer: pq.ParquetWriter | None = None
    qfq_writer: pq.ParquetWriter | None = None
    raw_path = tmp_root / "raw_daily.parquet"
    qfq_path = tmp_root / "qfq_daily.parquet"
    audit_path = tmp_root / "pair_audit.json"
    renamed_to_final = False

    try:
        roots = {"raw": raw_root, "qfq": qfq_root}
        if hfq_root is not None:
            roots["hfq"] = hfq_root
        files = {layer: _unique_file_map(root) for layer, root in roots.items()}
        _record_source_identities(report, roots, files)
        common_names = set(files["raw"]) & set(files["qfq"])
        all_layer_common_names = set.intersection(*(set(layer_files) for layer_files in files.values()))
        report["source_key_diagnostics"]["file_sets"] = {
            "raw_qfq_common_files": len(common_names),
            "all_layer_common_files": len(all_layer_common_names),
            **{
                f"{layer}_outside_all_layer_common_files": len(set(layer_files) - all_layer_common_names)
                for layer, layer_files in files.items()
            },
        }
        if not common_names or any(set(layer_files) != common_names for layer_files in files.values()):
            report["blocking_errors"].append("SOURCE_FILE_SET_MISMATCH")

        raw_profile = _empty_profile("none")
        qfq_profile = _empty_profile("qfq")
        source_common = source_raw_only = source_qfq_only = 0
        raw_only_year: Counter[str] = Counter()
        raw_only_exchange: Counter[str] = Counter()
        raw_only_all_dates: list[pd.Timestamp] = []
        codes_not_in_qfq: set[str] = set()
        common_codes_extra_dates: set[str] = set()
        filtered_year: Counter[str] = Counter()
        filtered_exchange: Counter[str] = Counter()
        nonpositive = Counter()
        unsafe_pre_close = Counter()
        unsafe_pre_close_rows = 0
        fully_filtered_codes: list[str] = []
        fully_filtered_source_rows = 0
        rows_written = mapping_evaluable = mapping_unknown = volume_mismatches = 0
        raw_source_duplicates = qfq_source_duplicates = hfq_source_duplicates = 0

        for name in sorted(set(files["raw"]) - common_names):
            raw_source = _read_price_file(files["raw"][name], "raw")
            market, raw_code = files["raw"][name].stem.split("#", maxsplit=1)
            code = f"{raw_code}.{market.upper()}"
            _update_profile(raw_profile, raw_source, code)
            raw_source_duplicates += int(raw_source.attrs.get("duplicate_source_rows", 0))
            dates = set(pd.to_datetime(raw_source["date"]))
            source_raw_only += len(dates)
            raw_only_all_dates.extend(dates)
            raw_only_year.update(str(date.year) for date in dates)
            raw_only_exchange.update([market.upper()] * len(dates))
            codes_not_in_qfq.add(code)
        for name in sorted(set(files["qfq"]) - common_names):
            qfq_source = _read_price_file(files["qfq"][name], "adj")
            market, raw_code = files["qfq"][name].stem.split("#", maxsplit=1)
            _update_profile(qfq_profile, qfq_source, f"{raw_code}.{market.upper()}")
            qfq_source_duplicates += int(qfq_source.attrs.get("duplicate_source_rows", 0))
            source_qfq_only += len(qfq_source)

        for name in sorted(common_names):
            raw_file, qfq_file = files["raw"][name], files["qfq"][name]
            _validate_adjustment_mode(raw_file, "none")
            _validate_adjustment_mode(qfq_file, "qfq")
            raw_source = _read_price_file(raw_file, "raw")
            qfq_source = _read_price_file(qfq_file, "adj")
            raw_source_duplicates += int(raw_source.attrs.get("duplicate_source_rows", 0))
            qfq_source_duplicates += int(qfq_source.attrs.get("duplicate_source_rows", 0))
            market, raw_code = raw_file.stem.split("#", maxsplit=1)
            code = f"{raw_code}.{market.upper()}"
            _update_profile(raw_profile, raw_source, code)
            _update_profile(qfq_profile, qfq_source, code)

            raw_dates = set(pd.to_datetime(raw_source["date"]))
            qfq_dates = set(pd.to_datetime(qfq_source["date"]))
            common_dates = raw_dates & qfq_dates
            raw_only_dates = raw_dates - qfq_dates
            qfq_only_dates = qfq_dates - raw_dates
            source_common += len(common_dates)
            source_raw_only += len(raw_only_dates)
            source_qfq_only += len(qfq_only_dates)
            if raw_only_dates:
                common_codes_extra_dates.add(code)
                raw_only_all_dates.extend(raw_only_dates)
                raw_only_year.update(str(date.year) for date in raw_only_dates)
                raw_only_exchange.update([market.upper()] * len(raw_only_dates))
            if not qfq_dates and raw_dates:
                codes_not_in_qfq.add(code)
            if raw_only_dates or qfq_only_dates:
                report["blocking_errors"].append(f"SOURCE_KEY_MISMATCH:{code}")
                continue

            if hfq_root is not None:
                if name not in files["hfq"]:
                    report["blocking_errors"].append(f"HFQ_SOURCE_FILE_MISSING:{code}")
                    continue
                hfq_file = files["hfq"][name]
                _validate_adjustment_mode(hfq_file, "hfq")
                hfq_source = _read_price_file(hfq_file, "hfq")
                hfq_source_duplicates += int(hfq_source.attrs.get("duplicate_source_rows", 0))
                if set(pd.to_datetime(hfq_source["date"])) != raw_dates:
                    report["blocking_errors"].append(f"HFQ_SOURCE_KEY_MISMATCH:{code}")
                    continue
            else:
                hfq_file = None

            merged = raw_source.merge(qfq_source, on="date", how="inner", validate="one_to_one")
            if hfq_file is not None:
                merged = merged.merge(hfq_source, on="date", how="inner", validate="one_to_one")
            merged["raw_pre_close"] = merged["raw_close"].shift(1)
            merged["adj_pre_close"] = merged["adj_close"].shift(1)
            merged["listing_trading_day"] = range(1, len(merged) + 1)
            merged["source_listing_date"] = raw_source["date"].min()
            valid = pd.Series(True, index=merged.index)
            for layer, prefix in (("raw", "raw"), ("qfq", "adj"), ("hfq", "hfq")):
                if layer not in files:
                    continue
                layer_valid = (
                    merged[[f"{prefix}_{field}" for field in _PRICE_COLUMNS]].notna().all(axis=1)
                    & (merged[[f"{prefix}_{field}" for field in _PRICE_COLUMNS]] > 0).all(axis=1)
                )
                nonpositive[layer] += int((~layer_valid).sum())
                valid &= layer_valid
            # Price-limit semantics require the immediate source predecessor.
            # Never substitute an earlier retained row after synchronous filtering.
            ohlc_valid = valid.copy()
            has_source_predecessor = pd.Series(range(len(merged)), index=merged.index).gt(0)
            any_unsafe_pre_close = pd.Series(False, index=merged.index)
            for layer, column in (("raw", "raw_pre_close"), ("qfq", "adj_pre_close")):
                pre_close_valid = merged[column].notna() & (merged[column] > 0)
                layer_unsafe = ohlc_valid & has_source_predecessor & ~pre_close_valid
                unsafe_pre_close[layer] += int(layer_unsafe.sum())
                any_unsafe_pre_close |= layer_unsafe
            valid &= ~any_unsafe_pre_close
            unsafe_pre_close_rows += int(any_unsafe_pre_close.sum())
            rejected = merged.loc[~valid, "date"]
            filtered_year.update(str(date.year) for date in rejected)
            filtered_exchange.update([market.upper()] * len(rejected))

            retained = merged.loc[valid].copy()
            if retained.empty:
                fully_filtered_codes.append(code)
                fully_filtered_source_rows += len(merged)
                continue
            raw_frame, qfq_frame = _split_canonical_pair(retained, raw_file, qfq_file)
            mappings = _evaluate_mapping(raw_frame, qfq_frame)
            mapping_evaluable += int(mappings["evaluable"].sum())
            mapping_unknown += int((~mappings["evaluable"]).sum())
            volume_mismatches += int((raw_frame["volume"].to_numpy() != qfq_frame["volume"].to_numpy()).sum())

            raw_table = pa.Table.from_pandas(raw_frame, schema=_canonical_arrow_schema(), preserve_index=False, safe=False)
            qfq_table = pa.Table.from_pandas(qfq_frame, schema=_canonical_arrow_schema(), preserve_index=False, safe=False)
            if raw_writer is None:
                raw_writer = pq.ParquetWriter(raw_path, raw_table.schema, compression="snappy")
                qfq_writer = pq.ParquetWriter(qfq_path, qfq_table.schema, compression="snappy")
            raw_writer.write_table(raw_table)
            assert qfq_writer is not None
            qfq_writer.write_table(qfq_table)
            rows_written += len(raw_frame)

        report["source_profiles"] = {"raw": _finish_profile(raw_profile), "qfq": _finish_profile(qfq_profile)}
        report["source_key_diagnostics"].update(
            {
                "common_keys": source_common,
                "raw_only_keys": source_raw_only,
                "qfq_only_keys": source_qfq_only,
                "raw_only_breakdown": {
                    "dates_before_qfq_min": _count_before(raw_only_all_dates, qfq_profile["min_date"]),
                    "dates_after_qfq_max": _count_after(raw_only_all_dates, qfq_profile["max_date"]),
                    "codes_not_in_qfq": sorted(codes_not_in_qfq),
                    "common_codes_extra_dates": sorted(common_codes_extra_dates),
                    "by_year": dict(sorted(raw_only_year.items())),
                    "by_exchange": dict(sorted(raw_only_exchange.items())),
                },
            }
        )
        report["paired_filter"] = {
            "method": PAIRING_POLICY,
            "layers": list(files),
            "rule": "drop key from both outputs when any supplied layer has non-positive or missing OHLC",
            "non_positive_or_missing_rows_by_layer": dict(nonpositive),
            "synchronously_dropped_keys": sum(filtered_year.values()),
            "dropped_by_year": dict(sorted(filtered_year.items())),
            "dropped_by_exchange": dict(sorted(filtered_exchange.items())),
            "fully_filtered_codes": fully_filtered_codes,
            "fully_filtered_code_count": len(fully_filtered_codes),
            "fully_filtered_source_rows": fully_filtered_source_rows,
            "price_values_transformed": False,
        }
        report["pre_close_policy"] = {
            "method": PRE_CLOSE_POLICY,
            "rule": "use the immediate source predecessor close; never substitute an earlier retained close",
            "source_pre_close_fields": {
                "raw": "raw_close.shift(1)",
                "qfq": "adj_close.shift(1)",
            },
            "first_source_observation_may_have_missing_pre_close": True,
            "non_positive_or_missing_source_pre_close_rows_by_layer": dict(unsafe_pre_close),
            "synchronously_dropped_unsafe_pre_close_rows": unsafe_pre_close_rows,
            "fallback_to_earlier_retained_close": False,
        }
        report["mapping_validation"] = {
            "method": "AFFINE_THEN_DAILY_RATIO_FALLBACK",
            "evaluable_rows": mapping_evaluable,
            "unevaluable_rows": mapping_unknown,
            "evaluable_ratio": mapping_evaluable / rows_written if rows_written else 0.0,
        }
        report["pair_validation"]["volume_mismatch_rows"] = volume_mismatches
        report["pair_validation"]["raw_source_duplicate_rows"] = raw_source_duplicates
        report["pair_validation"]["qfq_source_duplicate_rows"] = qfq_source_duplicates
        report["pair_validation"]["hfq_source_duplicate_rows"] = hfq_source_duplicates
        if source_raw_only or source_qfq_only:
            report["blocking_errors"].append("SOURCE_PRIMARY_KEYS_DIFFER")
        if mapping_unknown:
            report["blocking_errors"].append("RAW_QFQ_MAPPING_NOT_EVALUABLE")
        if volume_mismatches:
            report["blocking_errors"].append("RAW_QFQ_VOLUME_MISMATCH")
        if raw_source_duplicates or qfq_source_duplicates or hfq_source_duplicates:
            report["blocking_errors"].append("SOURCE_DUPLICATE_KEYS")
        if rows_written == 0:
            report["blocking_errors"].append("NO_VALID_PAIRED_ROWS")

        if raw_writer is not None:
            raw_writer.close()
            raw_writer = None
        if qfq_writer is not None:
            qfq_writer.close()
            qfq_writer = None
        report["blocking_errors"] = sorted(set(report["blocking_errors"]))
        if report["blocking_errors"]:
            raise CoreInputPairError("core input pair validation failed", report)

        output_profiles = _verify_outputs(raw_path, qfq_path, report)
        report["decision"] = "CORE_INPUT_PAIR_CANDIDATE_ACCEPTED"
        report["pair_validation"]["accepted"] = True
        report["outputs"] = {
            "raw_daily": _output_profile(raw_path, "none", output_profiles["raw"]),
            "qfq_daily": _output_profile(qfq_path, "qfq", output_profiles["qfq"]),
        }
        report["projection"] = {
            "method": PAIRING_POLICY,
            "source_raw_sha256": report["source_snapshots"]["raw"]["tree_sha256"],
            "source_qfq_sha256": report["source_snapshots"]["qfq"]["tree_sha256"],
            "projected_raw_rows": rows_written,
            "dropped_raw_only_rows": 0,
            "raw_only_keys_after_projection": 0,
            "qfq_only_keys_after_projection": 0,
            "price_values_transformed": False,
        }
        _write_json(audit_path, report)
        os.replace(tmp_root, final_root)
        renamed_to_final = True
        report["publication"]["atomic_rename_completed"] = True
        report["publication"]["candidate_published"] = True
        _write_json(final_root / audit_path.name, report)
        return CoreInputPairResult(
            output_root=final_root,
            raw_daily=final_root / raw_path.name,
            qfq_daily=final_root / qfq_path.name,
            audit=final_root / audit_path.name,
            report=report,
        )
    except Exception as exc:
        if renamed_to_final and final_root.exists():
            shutil.rmtree(final_root)
        report["publication"]["atomic_rename_completed"] = False
        report["publication"]["candidate_published"] = False
        if not isinstance(exc, CoreInputPairError):
            report["blocking_errors"] = sorted(set([*report["blocking_errors"], type(exc).__name__]))
            failure = CoreInputPairError(str(exc), report)
        else:
            failure = exc
        if diagnostics_path is not None:
            report["decision"] = "CORE_INPUT_PAIR_VALIDATION_FAILED"
            _write_json(Path(diagnostics_path), report)
        if failure is exc:
            raise
        raise failure from exc
    finally:
        if raw_writer is not None:
            raw_writer.close()
        if qfq_writer is not None:
            qfq_writer.close()
        if tmp_root.exists():
            shutil.rmtree(tmp_root)


def _split_canonical_pair(paired: pd.DataFrame, raw_file: Path, qfq_file: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    market, raw_code = raw_file.stem.split("#", maxsplit=1)
    common = paired[["date", "listing_trading_day"]].copy()
    common["code"] = f"{raw_code}.{market.upper()}"
    common["name"] = _read_name(raw_file)
    source_listing_date = pd.Timestamp(paired["source_listing_date"].iloc[0])
    common["listing_date"] = source_listing_date
    common["listing_date_status"] = "INFERRED_FIRST_OBSERVATION"
    common["listing_days"] = (paired["date"] - source_listing_date).dt.days + 1
    raw = common.copy()
    qfq = common.copy()
    for field in _PRICE_COLUMNS:
        raw[field] = paired[f"raw_{field}"]
        qfq[field] = paired[f"adj_{field}"]
    raw["pre_close"] = paired["raw_pre_close"]
    qfq["pre_close"] = paired["adj_pre_close"]
    raw["volume"] = paired["raw_volume"]
    qfq["volume"] = paired["adj_volume"]
    raw["amount"] = paired["raw_amount"]
    qfq["amount"] = paired["adj_amount"]
    raw["pct_chg"] = (raw["close"] / raw["pre_close"] - 1.0) * 100.0
    qfq["pct_chg"] = (qfq["close"] / qfq["pre_close"] - 1.0) * 100.0
    for frame in (raw, qfq):
        frame["trade_status"] = ((frame["volume"] > 0) & (frame["amount"] > 0)).astype(int).astype(str)
        frame["is_suspended"] = frame["trade_status"].eq("0")
    return (
        normalize_daily_bars(raw, provider="canonical", adj_type="none", source="tongdaxin_export_raw", source_file=raw_file),
        normalize_daily_bars(qfq, provider="canonical", adj_type="qfq", source="tongdaxin_export_qfq", source_file=qfq_file),
    )


def _evaluate_mapping(raw: pd.DataFrame, qfq: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame({"code": raw["code"]})
    for field in _PRICE_COLUMNS:
        frame[f"raw_{field}"] = raw[field]
        frame[f"adj_{field}"] = qfq[field]
    frame = apply_daily_ratio_mapping(fit_raw_qfq_mapping(frame))
    frame["evaluable"] = frame["adjustment_status"].isin({"KNOWN_AFFINE_RAW_QFQ_VALIDATED", "DAILY_RATIO_FALLBACK"})
    return frame


def _verify_outputs(raw_path: Path, qfq_path: Path, report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_file = pq.ParquetFile(raw_path)
    qfq_file = pq.ParquetFile(qfq_path)
    profiles = {"raw": _empty_stream_profile(), "qfq": _empty_stream_profile()}
    validation = report["pair_validation"]
    duplicate_counts = {"raw": 0, "qfq": 0}
    exact_keys = True
    columns = ["date", "code", "adj_type"]
    raw_batches = raw_file.iter_batches(batch_size=100_000, columns=columns)
    qfq_batches = qfq_file.iter_batches(batch_size=100_000, columns=columns)
    for raw_batch, qfq_batch in zip_longest(raw_batches, qfq_batches):
        if raw_batch is None or qfq_batch is None:
            exact_keys = False
            break
        raw = raw_batch.to_pandas()
        qfq = qfq_batch.to_pandas()
        if len(raw) != len(qfq):
            exact_keys = False
            break
        if not raw["date"].equals(qfq["date"]) or not raw["code"].equals(qfq["code"]):
            exact_keys = False
        if not raw["adj_type"].eq("none").all() or not qfq["adj_type"].eq("qfq").all():
            raise CoreInputPairError("published pair adjustment identity mismatch", report)
        duplicate_counts["raw"] += _update_stream_profile(profiles["raw"], raw)
        duplicate_counts["qfq"] += _update_stream_profile(profiles["qfq"], qfq)
    validation.update(
        {
            "raw_duplicate_keys": duplicate_counts["raw"],
            "qfq_duplicate_keys": duplicate_counts["qfq"],
            "raw_only_keys": 0 if exact_keys else None,
            "qfq_only_keys": 0 if exact_keys else None,
            "exact_primary_key_equality": exact_keys,
        }
    )
    if any(validation[key] for key in ("raw_duplicate_keys", "qfq_duplicate_keys", "raw_only_keys", "qfq_only_keys")):
        raise CoreInputPairError("published pair key validation failed", report)
    if not validation["exact_primary_key_equality"]:
        raise CoreInputPairError("published pair row order differs", report)
    if profiles["raw"]["rows"] != profiles["qfq"]["rows"]:
        raise CoreInputPairError("published pair row counts differ", report)
    return profiles


def _empty_stream_profile() -> dict[str, Any]:
    return {
        "rows": 0,
        "codes": set(),
        "completed_codes": set(),
        "current_code": None,
        "last_date": None,
        "min_date": None,
        "max_date": None,
    }


def _update_stream_profile(profile: dict[str, Any], frame: pd.DataFrame) -> int:
    duplicate_keys = 0
    profile["rows"] += len(frame)
    if frame.empty:
        return 0
    profile["min_date"] = _min_optional(profile["min_date"], frame["date"].min())
    profile["max_date"] = _max_optional(profile["max_date"], frame["date"].max())
    profile["codes"].update(frame["code"].astype(str).unique())
    change = frame["code"].astype(str).ne(frame["code"].astype(str).shift())
    starts = list(frame.index[change])
    ends = [*starts[1:], frame.index[-1] + 1]
    for start, end in zip(starts, ends):
        segment = frame.loc[start:end - 1]
        code = str(segment.iloc[0]["code"])
        if code != profile["current_code"]:
            if profile["current_code"] is not None:
                profile["completed_codes"].add(profile["current_code"])
            if code in profile["completed_codes"]:
                duplicate_keys += len(segment)
            profile["current_code"] = code
            profile["last_date"] = None
        dates = pd.to_datetime(segment["date"])
        duplicate_keys += int(dates.duplicated().sum())
        if profile["last_date"] is not None and dates.iloc[0] <= profile["last_date"]:
            duplicate_keys += 1
        if not dates.is_monotonic_increasing:
            duplicate_keys += 1
        profile["last_date"] = dates.iloc[-1]
    return duplicate_keys


def _min_optional(left: Any, right: Any) -> Any:
    return right if left is None else min(left, right)


def _max_optional(left: Any, right: Any) -> Any:
    return right if left is None else max(left, right)


def _base_report(raw: Path, qfq: Path, hfq: Path | None) -> dict[str, Any]:
    return {
        "task": "STOCK_RS_PULLBACK_v1_CORE_INPUT_PAIR_REMEDIATION_2",
        "decision": "CORE_INPUT_PAIR_VALIDATION_IN_PROGRESS",
        "source_snapshots": {},
        "source_profiles": {},
        "source_key_diagnostics": {},
        "paired_filter": {},
        "mapping_validation": {},
        "pair_validation": {"accepted": False},
        "publication": {
            "transactional_temporary_output": True,
            "atomic_rename_completed": False,
            "candidate_published": False,
        },
        "safety": {
            "raw_source": str(raw),
            "qfq_source": str(qfq),
            "hfq_cross_validation_source": str(hfq) if hfq else None,
            "qfq_prices_used_to_infer_raw": False,
            "price_values_transformed": False,
            "formal_manifest_generated": False,
            "full_recalculation_performed": False,
            "trading_allowed": False,
        },
        "blocking_errors": [],
    }


def _record_source_identities(report: dict[str, Any], roots: dict[str, Path], files: dict[str, dict[str, Path]]) -> None:
    for layer, root in roots.items():
        report["source_snapshots"][layer] = {
            "path": str(root),
            "files": len(files[layer]),
            "tree_sha256": _tree_sha256(root, files[layer].values()),
        }


def _tree_sha256(root: Path, paths: Any) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _empty_profile(adjustment: str) -> dict[str, Any]:
    return {"adjustment": adjustment, "rows": 0, "codes": set(), "min_date": None, "max_date": None}


def _update_profile(profile: dict[str, Any], frame: pd.DataFrame, code: str) -> None:
    dates = list(pd.to_datetime(frame["date"]))
    profile["rows"] += len(frame)
    profile["codes"].add(code)
    if dates:
        profile["min_date"] = min([date for date in (profile["min_date"], min(dates)) if date is not None])
        profile["max_date"] = max([date for date in (profile["max_date"], max(dates)) if date is not None])


def _finish_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "adjustment": profile["adjustment"],
        "rows": profile["rows"],
        "unique_codes": len(profile["codes"]),
        "min_date": _date_text(profile["min_date"]),
        "max_date": _date_text(profile["max_date"]),
    }


def _count_before(dates: list[pd.Timestamp], boundary: pd.Timestamp | None) -> int:
    return sum(date < boundary for date in dates) if boundary is not None else len(dates)


def _count_after(dates: list[pd.Timestamp], boundary: pd.Timestamp | None) -> int:
    return sum(date > boundary for date in dates) if boundary is not None else len(dates)


def _date_text(value: Any) -> str | None:
    return str(pd.Timestamp(value).date()) if value is not None else None


def _output_profile(path: Path, adjustment: str, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": path.name,
        "sha256": _sha256(path),
        "rows": profile["rows"],
        "unique_codes": len(profile["codes"]),
        "min_date": _date_text(profile["min_date"]),
        "max_date": _date_text(profile["max_date"]),
        "adjustment": adjustment,
    }


def _canonical_arrow_schema() -> pa.Schema:
    fields = []
    for column in CANONICAL_DAILY_COLUMNS:
        if column in {"date", "listing_date"}:
            data_type = pa.timestamp("ns")
        elif column in BOOLEAN_COLUMNS:
            data_type = pa.bool_()
        elif column in NUMERIC_COLUMNS:
            data_type = pa.float64()
        else:
            data_type = pa.string()
        fields.append(pa.field(column, data_type, nullable=True))
    return pa.schema(fields)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)
