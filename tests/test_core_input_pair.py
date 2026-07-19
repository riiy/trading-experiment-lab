from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from texperiment.data import core_input_pair
from texperiment.data.core_input_pair import CoreInputPairError, prepare_tdx_core_input_pair


def test_pair_synchronously_filters_nonpositive_qfq_without_transforming_prices(tmp_path):
    sources = _write_sources(tmp_path)
    _replace_row(sources["qfq"], 0, "2026-01-02,-1.00,6.50,5.50,6.25,1000,10000.00")
    output = tmp_path / "candidate"

    result = prepare_tdx_core_input_pair(
        sources["raw"].parent,
        sources["qfq"].parent,
        output,
        hfq_input=sources["hfq"].parent,
    )

    raw = pd.read_parquet(result.raw_daily)
    qfq = pd.read_parquet(result.qfq_daily)
    assert raw[["date", "code"]].equals(qfq[["date", "code"]])
    assert len(raw) == 1
    assert raw.loc[0, "open"] == 10.5
    assert qfq.loc[0, "open"] == 6.25
    assert raw.loc[0, "pre_close"] == 10.5
    assert qfq.loc[0, "pre_close"] == 6.25
    assert raw.loc[0, "listing_date"] == pd.Timestamp("2026-01-02")
    assert raw.loc[0, "listing_trading_day"] == 2
    assert raw.loc[0, "listing_days"] == 2
    assert qfq.loc[0, "listing_date"] == pd.Timestamp("2026-01-02")
    assert qfq.loc[0, "listing_trading_day"] == 2
    assert raw.loc[0, "adj_type"] == "none"
    assert qfq.loc[0, "adj_type"] == "qfq"
    assert result.report["paired_filter"]["synchronously_dropped_keys"] == 1
    assert result.report["paired_filter"]["non_positive_or_missing_rows_by_layer"]["qfq"] == 1
    assert result.report["paired_filter"]["price_values_transformed"] is False
    assert result.report["pair_validation"]["exact_primary_key_equality"] is True
    assert result.report["publication"]["candidate_published"] is True
    assert json.loads(result.audit.read_text(encoding="utf-8"))["publication"] == {
        "atomic_rename_completed": True,
        "candidate_published": True,
        "transactional_temporary_output": True,
    }


def test_pair_drops_row_with_unsafe_source_pre_close_without_earlier_fallback(tmp_path):
    sources = _write_sources(tmp_path)
    _append_row(sources["raw"], "2026-01-04,11.50,12.50,11.00,12.00,1300,15000.00")
    _append_row(sources["qfq"], "2026-01-04,5.75,6.25,5.50,6.00,1300,15000.00")
    _append_row(sources["hfq"], "2026-01-04,26.00,28.00,25.00,27.00,1300,15000.00")
    _replace_row(sources["raw"], 0, "2026-01-02,10.00,11.00,9.00,-1.00,1000,10000.00")

    result = prepare_tdx_core_input_pair(
        sources["raw"].parent,
        sources["qfq"].parent,
        tmp_path / "candidate",
        hfq_input=sources["hfq"].parent,
    )

    raw = pd.read_parquet(result.raw_daily)
    qfq = pd.read_parquet(result.qfq_daily)
    assert raw["date"].tolist() == [pd.Timestamp("2026-01-04")]
    assert qfq["date"].tolist() == [pd.Timestamp("2026-01-04")]
    # The actual immediate source predecessor is used even though that positive
    # predecessor was itself synchronously excluded from the paired output.
    assert raw.loc[0, "pre_close"] == 11.5
    assert qfq.loc[0, "pre_close"] == 6.75
    policy = result.report["pre_close_policy"]
    assert policy["non_positive_or_missing_source_pre_close_rows_by_layer"] == {"qfq": 0, "raw": 1}
    assert policy["synchronously_dropped_unsafe_pre_close_rows"] == 1
    assert policy["fallback_to_earlier_retained_close"] is False


def test_fully_filtered_security_reports_stable_no_valid_rows_failure(tmp_path):
    sources = _write_sources(tmp_path)
    _replace_row(sources["qfq"], 0, "2026-01-02,-1.00,-0.50,-1.50,-0.75,1000,10000.00")
    _replace_row(sources["qfq"], 1, "2026-01-03,-1.00,-0.50,-1.50,-0.75,1200,13000.00")

    with pytest.raises(CoreInputPairError, match="validation failed") as caught:
        prepare_tdx_core_input_pair(
            sources["raw"].parent,
            sources["qfq"].parent,
            tmp_path / "candidate",
            hfq_input=sources["hfq"].parent,
        )

    report = caught.value.report
    assert "NO_VALID_PAIRED_ROWS" in report["blocking_errors"]
    assert report["paired_filter"]["fully_filtered_codes"] == ["600000.SH"]
    assert report["paired_filter"]["fully_filtered_code_count"] == 1
    assert report["paired_filter"]["fully_filtered_source_rows"] == 2
    assert not (tmp_path / "candidate").exists()


def test_atomic_rename_failure_reports_candidate_not_published(tmp_path, monkeypatch):
    sources = _write_sources(tmp_path)
    output = tmp_path / "candidate"
    diagnostic = tmp_path / "diagnostics" / "failure.json"
    original_replace = core_input_pair.os.replace

    def fail_candidate_rename(source, destination):
        if Path(destination) == output:
            raise OSError("forced candidate rename failure")
        return original_replace(source, destination)

    monkeypatch.setattr(core_input_pair.os, "replace", fail_candidate_rename)

    with pytest.raises(CoreInputPairError, match="forced candidate rename failure"):
        prepare_tdx_core_input_pair(
            sources["raw"].parent,
            sources["qfq"].parent,
            output,
            hfq_input=sources["hfq"].parent,
            diagnostics_path=diagnostic,
        )

    report = json.loads(diagnostic.read_text(encoding="utf-8"))
    assert report["publication"]["candidate_published"] is False
    assert report["publication"]["atomic_rename_completed"] is False
    assert not output.exists()
    assert not list(tmp_path.glob(".candidate.*.tmp"))


def test_pair_audit_contains_profiles_breakdowns_and_frozen_source_hashes(tmp_path):
    sources = _write_sources(tmp_path)
    result = prepare_tdx_core_input_pair(
        sources["raw"].parent,
        sources["qfq"].parent,
        tmp_path / "candidate",
        hfq_input=sources["hfq"].parent,
    )

    audit = json.loads(result.audit.read_text(encoding="utf-8"))
    assert audit["source_profiles"]["raw"] == {
        "adjustment": "none",
        "rows": 2,
        "unique_codes": 1,
        "min_date": "2026-01-02",
        "max_date": "2026-01-03",
    }
    assert audit["source_key_diagnostics"]["common_keys"] == 2
    assert audit["source_key_diagnostics"]["raw_only_keys"] == 0
    assert audit["source_key_diagnostics"]["qfq_only_keys"] == 0
    assert audit["source_key_diagnostics"]["raw_only_breakdown"]["by_year"] == {}
    assert audit["source_snapshots"]["raw"]["tree_sha256"]
    assert audit["source_snapshots"]["qfq"]["tree_sha256"]
    assert audit["projection"]["method"] == "PAIRED_NON_POSITIVE_OHLC_FILTER_V1"
    assert audit["projection"]["price_values_transformed"] is False


def test_source_key_mismatch_fails_without_publishing_candidate(tmp_path):
    sources = _write_sources(tmp_path)
    lines = sources["qfq"].read_text(encoding="gb18030").splitlines()
    sources["qfq"].write_text("\n".join(lines[:-1]) + "\n", encoding="gb18030")
    output = tmp_path / "candidate"
    diagnostic = tmp_path / "diagnostics" / "failure.json"

    with pytest.raises(CoreInputPairError, match="validation failed"):
        prepare_tdx_core_input_pair(
            sources["raw"].parent,
            sources["qfq"].parent,
            output,
            hfq_input=sources["hfq"].parent,
            diagnostics_path=diagnostic,
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".candidate.*.tmp"))
    report = json.loads(diagnostic.read_text(encoding="utf-8"))
    assert report["decision"] == "CORE_INPUT_PAIR_VALIDATION_FAILED"
    assert report["source_key_diagnostics"]["raw_only_keys"] == 1
    assert report["source_key_diagnostics"]["raw_only_breakdown"]["common_codes_extra_dates"] == ["600000.SH"]
    assert "SOURCE_PRIMARY_KEYS_DIFFER" in report["blocking_errors"]


def test_raw_only_file_is_included_in_full_difference_profile(tmp_path):
    sources = _write_sources(tmp_path)
    raw_only = sources["raw"].parent / "SZ#000001.txt"
    raw_only.write_text(
        "000001 仅raw 日线 不复权\n日期 开盘 最高 最低 收盘 成交量 成交额\n"
        "2025-12-31,5.00,5.20,4.90,5.10,800,4000.00\n",
        encoding="gb18030",
    )
    diagnostic = tmp_path / "failure.json"

    with pytest.raises(CoreInputPairError):
        prepare_tdx_core_input_pair(
            sources["raw"].parent,
            sources["qfq"].parent,
            tmp_path / "candidate",
            hfq_input=sources["hfq"].parent,
            diagnostics_path=diagnostic,
        )

    report = json.loads(diagnostic.read_text(encoding="utf-8"))
    assert report["source_profiles"]["raw"]["rows"] == 3
    assert report["source_profiles"]["raw"]["unique_codes"] == 2
    assert report["source_key_diagnostics"]["raw_only_keys"] == 1
    breakdown = report["source_key_diagnostics"]["raw_only_breakdown"]
    assert breakdown["codes_not_in_qfq"] == ["000001.SZ"]
    assert breakdown["dates_before_qfq_min"] == 1
    assert breakdown["by_year"] == {"2025": 1}
    assert breakdown["by_exchange"] == {"SZ": 1}


def test_volume_mismatch_is_blocking_and_preserves_no_candidate(tmp_path):
    sources = _write_sources(tmp_path)
    _replace_row(sources["qfq"], 1, "2026-01-03,6.25,7.00,6.00,6.75,9999,13000.00")

    with pytest.raises(CoreInputPairError) as caught:
        prepare_tdx_core_input_pair(
            sources["raw"].parent,
            sources["qfq"].parent,
            tmp_path / "candidate",
            hfq_input=sources["hfq"].parent,
        )

    assert caught.value.report["pair_validation"]["volume_mismatch_rows"] == 1
    assert "RAW_QFQ_VOLUME_MISMATCH" in caught.value.report["blocking_errors"]
    assert not (tmp_path / "candidate").exists()


def test_unevaluable_mapping_is_blocking(tmp_path):
    sources = _write_sources(tmp_path)
    _replace_row(sources["qfq"], 0, "2026-01-02,6.00,8.00,5.00,9.00,1000,10000.00")

    with pytest.raises(CoreInputPairError) as caught:
        prepare_tdx_core_input_pair(
            sources["raw"].parent,
            sources["qfq"].parent,
            tmp_path / "candidate",
            hfq_input=sources["hfq"].parent,
        )

    assert caught.value.report["mapping_validation"]["unevaluable_rows"] == 1
    assert "RAW_QFQ_MAPPING_NOT_EVALUABLE" in caught.value.report["blocking_errors"]


def test_duplicate_source_key_is_blocking(tmp_path):
    sources = _write_sources(tmp_path)
    lines = sources["raw"].read_text(encoding="gb18030").splitlines()
    sources["raw"].write_text("\n".join([*lines[:-1], lines[2], lines[-1]]) + "\n", encoding="gb18030")

    with pytest.raises(CoreInputPairError) as caught:
        prepare_tdx_core_input_pair(
            sources["raw"].parent,
            sources["qfq"].parent,
            tmp_path / "candidate",
            hfq_input=sources["hfq"].parent,
        )

    assert caught.value.report["pair_validation"]["raw_source_duplicate_rows"] == 2
    assert "SOURCE_DUPLICATE_KEYS" in caught.value.report["blocking_errors"]


def test_existing_output_is_never_overwritten(tmp_path):
    sources = _write_sources(tmp_path)
    output = tmp_path / "candidate"
    output.mkdir()
    marker = output / "marker"
    marker.write_text("keep", encoding="ascii")

    with pytest.raises(FileExistsError, match="already exists"):
        prepare_tdx_core_input_pair(sources["raw"].parent, sources["qfq"].parent, output)

    assert marker.read_text(encoding="ascii") == "keep"


def test_output_verification_streams_across_batch_boundaries(tmp_path, monkeypatch):
    sources = _write_sources(tmp_path)
    original = core_input_pair.pq.ParquetFile.iter_batches

    def one_row_batches(parquet_file, *args, **kwargs):
        kwargs["batch_size"] = 1
        return original(parquet_file, *args, **kwargs)

    monkeypatch.setattr(core_input_pair.pq.ParquetFile, "iter_batches", one_row_batches)
    result = prepare_tdx_core_input_pair(
        sources["raw"].parent,
        sources["qfq"].parent,
        tmp_path / "candidate",
        hfq_input=sources["hfq"].parent,
    )

    assert result.report["outputs"]["raw_daily"]["rows"] == 2
    assert result.report["outputs"]["qfq_daily"]["rows"] == 2
    assert result.report["pair_validation"]["raw_duplicate_keys"] == 0


def _write_sources(root: Path) -> dict[str, Path]:
    rows = {
        "raw": [
            "2026-01-02,10.00,11.00,9.00,10.50,1000,10000.00",
            "2026-01-03,10.50,12.00,10.00,11.50,1200,13000.00",
        ],
        "qfq": [
            "2026-01-02,6.00,6.50,5.50,6.25,1000,10000.00",
            "2026-01-03,6.25,7.00,6.00,6.75,1200,13000.00",
        ],
        "hfq": [
            "2026-01-02,23.00,25.00,21.00,24.00,1000,10000.00",
            "2026-01-03,24.00,27.00,23.00,26.00,1200,13000.00",
        ],
    }
    headers = {"raw": "不复权", "qfq": "前复权", "hfq": "后复权"}
    paths: dict[str, Path] = {}
    for layer, layer_rows in rows.items():
        directory = root / layer
        directory.mkdir()
        path = directory / "SH#600000.txt"
        header = f"600000 测试股 日线 {headers[layer]}\n日期 开盘 最高 最低 收盘 成交量 成交额\n"
        path.write_text(header + "\n".join(layer_rows) + "\n", encoding="gb18030")
        paths[layer] = path
    return paths


def _replace_row(path: Path, row: int, replacement: str) -> None:
    lines = path.read_text(encoding="gb18030").splitlines()
    lines[row + 2] = replacement
    path.write_text("\n".join(lines) + "\n", encoding="gb18030")


def _append_row(path: Path, row: str) -> None:
    with path.open("a", encoding="gb18030") as handle:
        handle.write(row + "\n")
