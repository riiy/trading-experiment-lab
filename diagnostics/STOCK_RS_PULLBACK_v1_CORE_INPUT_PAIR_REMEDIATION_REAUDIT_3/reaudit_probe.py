from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd

from texperiment.data import core_input_pair
from texperiment.data.core_input_pair import CoreInputPairError, prepare_tdx_core_input_pair


HEADERS = {"raw": "不复权", "qfq": "前复权", "hfq": "后复权"}
BASE_ROWS = {
    "raw": [
        "2026-01-02,10.00,11.00,9.00,10.50,1000,10000.00",
        "2026-01-03,10.50,12.00,10.00,11.50,1200,13000.00",
        "2026-01-04,11.50,12.50,11.00,12.00,1300,15000.00",
    ],
    "qfq": [
        "2026-01-02,6.00,6.50,5.50,6.25,1000,10000.00",
        "2026-01-03,6.25,7.00,6.00,6.75,1200,13000.00",
        "2026-01-04,6.75,7.25,6.50,7.00,1300,15000.00",
    ],
    "hfq": [
        "2026-01-02,23.00,25.00,21.00,24.00,1000,10000.00",
        "2026-01-03,24.00,27.00,23.00,26.00,1200,13000.00",
        "2026-01-04,26.00,28.00,25.00,27.00,1300,15000.00",
    ],
}


def write_security(root: Path, market: str, code: str, rows_by_layer=None) -> dict[str, Path]:
    rows_by_layer = rows_by_layer or BASE_ROWS
    paths: dict[str, Path] = {}
    for layer, rows in rows_by_layer.items():
        directory = root / layer
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{market}#{code}.txt"
        path.write_text(
            f"{code} 测试股 日线 {HEADERS[layer]}\n日期 开盘 最高 最低 收盘 成交量 成交额\n"
            + "\n".join(rows)
            + "\n",
            encoding="gb18030",
        )
        paths[layer] = path
    return paths


def replace_row(path: Path, row: int, replacement: str) -> None:
    lines = path.read_text(encoding="gb18030").splitlines()
    lines[row + 2] = replacement
    path.write_text("\n".join(lines) + "\n", encoding="gb18030")


def build(root: Path):
    return prepare_tdx_core_input_pair(
        root / "raw",
        root / "qfq",
        root / "candidate",
        hfq_input=root / "hfq",
        diagnostics_path=root / "failure.json",
    )


def main() -> None:
    cases: dict[str, dict[str, object]] = {}
    with tempfile.TemporaryDirectory(prefix="core-pair-reaudit3-") as temp:
        base = Path(temp)

        # One fully filtered security must not stop later securities.
        root = base / "one_code_fully_filtered"
        first = write_security(root, "SH", "600000")
        write_security(root, "SZ", "000001")
        for row in range(3):
            replace_row(
                first["qfq"],
                row,
                f"2026-01-0{row + 2},-1.00,-0.50,-1.50,-0.75,{1000 + row * 100},10000.00",
            )
        result = build(root)
        raw = pd.read_parquet(result.raw_daily)
        qfq = pd.read_parquet(result.qfq_daily)
        assert set(raw["code"]) == {"000001.SZ"}
        assert raw[["code", "date"]].equals(qfq[["code", "date"]])
        paired = result.report["paired_filter"]
        assert paired["fully_filtered_codes"] == ["600000.SH"]
        assert paired["fully_filtered_code_count"] == 1
        assert paired["fully_filtered_source_rows"] == 3
        cases["fully_filtered_security_does_not_stop_stream"] = {
            "passed": True,
            "retained_codes": sorted(set(raw["code"])),
            "retained_rows": len(raw),
            "fully_filtered_codes": paired["fully_filtered_codes"],
            "fully_filtered_code_count": paired["fully_filtered_code_count"],
            "fully_filtered_source_rows": paired["fully_filtered_source_rows"],
        }

        # If every security is filtered, failure is stable and unpublished.
        root = base / "all_codes_fully_filtered"
        only = write_security(root, "SH", "600000")
        for row in range(3):
            replace_row(
                only["qfq"],
                row,
                f"2026-01-0{row + 2},-1.00,-0.50,-1.50,-0.75,{1000 + row * 100},10000.00",
            )
        try:
            build(root)
        except CoreInputPairError as exc:
            failure = exc.report
        else:
            raise AssertionError("all-filtered input did not fail closed")
        persisted_failure = json.loads((root / "failure.json").read_text(encoding="utf-8"))
        assert "NO_VALID_PAIRED_ROWS" in failure["blocking_errors"]
        assert failure["paired_filter"]["fully_filtered_codes"] == ["600000.SH"]
        assert failure["paired_filter"]["fully_filtered_source_rows"] == 3
        assert persisted_failure["decision"] == "CORE_INPUT_PAIR_VALIDATION_FAILED"
        assert persisted_failure["publication"]["candidate_published"] is False
        assert not (root / "candidate").exists()
        assert not list(root.glob(".candidate.*.tmp"))
        cases["all_inputs_filtered_fail_closed"] = {
            "passed": True,
            "blocking_errors": failure["blocking_errors"],
            "fully_filtered_codes": failure["paired_filter"]["fully_filtered_codes"],
            "fully_filtered_source_rows": failure["paired_filter"]["fully_filtered_source_rows"],
            "candidate_published": False,
            "temporary_exists": False,
        }

        # Immediate source predecessor semantics from Reaudit 2.
        root = base / "qfq_filter_pre_close"
        sources = write_security(root, "SH", "600000")
        replace_row(sources["qfq"], 0, "2026-01-02,-1.00,6.50,5.50,6.25,1000,10000.00")
        result = build(root)
        raw, qfq = pd.read_parquet(result.raw_daily), pd.read_parquet(result.qfq_daily)
        assert raw.iloc[0]["pre_close"] == 10.5
        assert qfq.iloc[0]["pre_close"] == 6.25
        assert int(raw.iloc[0]["listing_trading_day"]) == 2
        cases["immediate_source_pre_close_preserved"] = {
            "passed": True,
            "raw_pre_close": float(raw.iloc[0]["pre_close"]),
            "qfq_pre_close": float(qfq.iloc[0]["pre_close"]),
        }

        for layer, replacement in (
            ("raw", "2026-01-02,-1.00,11.00,9.00,10.50,1000,10000.00"),
            ("qfq", "2026-01-02,-1.00,6.50,5.50,6.25,1000,10000.00"),
            ("hfq", "2026-01-02,-1.00,25.00,21.00,24.00,1000,10000.00"),
        ):
            root = base / f"sync_filter_{layer}"
            sources = write_security(root, "SH", "600000")
            replace_row(sources[layer], 0, replacement)
            result = build(root)
            raw, qfq = pd.read_parquet(result.raw_daily), pd.read_parquet(result.qfq_daily)
            assert raw[["code", "date"]].equals(qfq[["code", "date"]])
            assert raw["date"].tolist() == [pd.Timestamp("2026-01-03"), pd.Timestamp("2026-01-04")]
            assert result.report["paired_filter"]["price_values_transformed"] is False
            cases[f"three_layer_sync_filter_{layer}"] = {"passed": True, "retained_rows": len(raw)}

        # Batch verification must remain streaming across boundaries.
        root = base / "streaming_verification"
        write_security(root, "SH", "600000")
        original_batches = core_input_pair.pq.ParquetFile.iter_batches

        def one_row_batches(parquet_file, *args, **kwargs):
            kwargs["batch_size"] = 1
            return original_batches(parquet_file, *args, **kwargs)

        core_input_pair.pq.ParquetFile.iter_batches = one_row_batches
        try:
            result = build(root)
        finally:
            core_input_pair.pq.ParquetFile.iter_batches = original_batches
        assert result.report["outputs"]["raw_daily"]["rows"] == 3
        assert result.report["pair_validation"]["raw_duplicate_keys"] == 0
        cases["streaming_output_verification"] = {"passed": True, "batch_size": 1}

        # Publication success/failure semantics from Reaudit 2.
        root = base / "publication_success"
        write_security(root, "SH", "600000")
        result = build(root)
        persisted = json.loads(result.audit.read_text(encoding="utf-8"))
        assert persisted["publication"]["atomic_rename_completed"] is True
        assert persisted["publication"]["candidate_published"] is True
        assert not list(root.glob(".candidate.*.tmp"))
        cases["publication_success"] = {"passed": True, **persisted["publication"]}

        root = base / "publication_failure"
        write_security(root, "SH", "600000")
        original_replace = core_input_pair.os.replace

        def fail_final_rename(source, destination):
            if Path(destination) == root / "candidate":
                raise OSError("reaudit3 forced rename failure")
            return original_replace(source, destination)

        core_input_pair.os.replace = fail_final_rename
        try:
            try:
                build(root)
            except CoreInputPairError:
                pass
            else:
                raise AssertionError("rename failure did not fail closed")
        finally:
            core_input_pair.os.replace = original_replace
        failure = json.loads((root / "failure.json").read_text(encoding="utf-8"))
        assert failure["publication"]["candidate_published"] is False
        assert failure["publication"]["atomic_rename_completed"] is False
        assert not (root / "candidate").exists()
        assert not list(root.glob(".candidate.*.tmp"))
        cases["publication_failure"] = {"passed": True, **failure["publication"]}

    output = Path(__file__).with_name("semantic_probe_results.json")
    output.write_text(
        json.dumps({"decision": "PROBE_PASSED", "cases": cases}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
