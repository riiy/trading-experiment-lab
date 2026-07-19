from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd

from texperiment.data import core_input_pair
from texperiment.data.core_input_pair import CoreInputPairError, prepare_tdx_core_input_pair


ROWS = {
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
HEADERS = {"raw": "不复权", "qfq": "前复权", "hfq": "后复权"}


def write_sources(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True)
    paths = {}
    for layer, rows in ROWS.items():
        directory = root / layer
        directory.mkdir()
        path = directory / "SH#600000.txt"
        path.write_text(
            f"600000 测试股 日线 {HEADERS[layer]}\n日期 开盘 最高 最低 收盘 成交量 成交额\n"
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
    with tempfile.TemporaryDirectory(prefix="core-pair-reaudit2-") as temp:
        base = Path(temp)

        # A rejected qfq row with a positive close remains the true immediate
        # predecessor for the retained next row.
        root = base / "qfq_filter"
        sources = write_sources(root)
        replace_row(sources["qfq"], 0, "2026-01-02,-1.00,6.50,5.50,6.25,1000,10000.00")
        result = build(root)
        raw, qfq = pd.read_parquet(result.raw_daily), pd.read_parquet(result.qfq_daily)
        assert raw.iloc[0]["pre_close"] == 10.5
        assert qfq.iloc[0]["pre_close"] == 6.25
        assert int(raw.iloc[0]["listing_trading_day"]) == 2
        cases["qfq_only_filter_keeps_positive_immediate_pre_close"] = {
            "passed": True,
            "retained_date": str(raw.iloc[0]["date"].date()),
            "raw_pre_close": float(raw.iloc[0]["pre_close"]),
            "qfq_pre_close": float(qfq.iloc[0]["pre_close"]),
        }

        # A non-positive immediate source close rejects the following row. The
        # next retained row uses day 2, never a more distant retained fallback.
        for layer, replacement, expected in (
            ("raw", "2026-01-02,10.00,11.00,9.00,-1.00,1000,10000.00", (11.5, 6.75)),
            ("qfq", "2026-01-02,6.00,6.50,5.50,-1.00,1000,10000.00", (11.5, 6.75)),
        ):
            root = base / f"unsafe_{layer}_pre_close"
            sources = write_sources(root)
            replace_row(sources[layer], 0, replacement)
            result = build(root)
            raw, qfq = pd.read_parquet(result.raw_daily), pd.read_parquet(result.qfq_daily)
            assert raw["date"].tolist() == [pd.Timestamp("2026-01-04")]
            assert qfq["date"].tolist() == [pd.Timestamp("2026-01-04")]
            assert (raw.iloc[0]["pre_close"], qfq.iloc[0]["pre_close"]) == expected
            assert result.report["pre_close_policy"]["fallback_to_earlier_retained_close"] is False
            cases[f"unsafe_{layer}_pre_close_synchronously_rejected"] = {
                "passed": True,
                "retained_date": "2026-01-04",
                "raw_pre_close": float(raw.iloc[0]["pre_close"]),
                "qfq_pre_close": float(qfq.iloc[0]["pre_close"]),
            }

        # Every supplied layer participates in the same key filter. Raw and qfq
        # values for retained rows must remain byte-for-source numeric values.
        for layer, replacement in (
            ("raw", "2026-01-02,-1.00,11.00,9.00,10.50,1000,10000.00"),
            ("qfq", "2026-01-02,-1.00,6.50,5.50,6.25,1000,10000.00"),
            ("hfq", "2026-01-02,-1.00,25.00,21.00,24.00,1000,10000.00"),
        ):
            root = base / f"filter_{layer}"
            sources = write_sources(root)
            replace_row(sources[layer], 0, replacement)
            result = build(root)
            raw, qfq = pd.read_parquet(result.raw_daily), pd.read_parquet(result.qfq_daily)
            assert raw["date"].equals(qfq["date"])
            assert raw["date"].tolist() == [pd.Timestamp("2026-01-03"), pd.Timestamp("2026-01-04")]
            assert raw["open"].tolist() == [10.5, 11.5]
            assert qfq["open"].tolist() == [6.25, 6.75]
            assert result.report["paired_filter"]["price_values_transformed"] is False
            cases[f"three_layer_sync_filter_{layer}"] = {
                "passed": True,
                "retained_rows": len(raw),
                "prices_transformed": False,
            }

        # Successful atomic publication must persist true only after rename.
        root = base / "publication_success"
        write_sources(root)
        result = build(root)
        persisted = json.loads(result.audit.read_text(encoding="utf-8"))
        assert persisted["publication"]["atomic_rename_completed"] is True
        assert persisted["publication"]["candidate_published"] is True
        assert result.output_root.exists()
        assert not list(root.glob(".candidate.*.tmp"))
        cases["successful_publication"] = {"passed": True, **persisted["publication"]}

        # Rename failure must persist false and leave neither final nor temp.
        root = base / "publication_failure"
        write_sources(root)
        original_replace = core_input_pair.os.replace

        def fail_final_rename(source, destination):
            if Path(destination) == root / "candidate":
                raise OSError("reaudit forced rename failure")
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
        assert failure["publication"]["atomic_rename_completed"] is False
        assert failure["publication"]["candidate_published"] is False
        assert not (root / "candidate").exists()
        assert not list(root.glob(".candidate.*.tmp"))
        cases["failed_publication"] = {
            "passed": True,
            **failure["publication"],
            "final_exists": False,
            "temporary_exists": False,
        }

    output = Path(__file__).with_name("semantic_probe_results.json")
    output.write_text(
        json.dumps({"decision": "PROBE_PASSED", "cases": cases}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
