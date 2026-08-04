from __future__ import annotations

import os
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from texperiment.data.tdx_paired_source import apply_daily_ratio_mapping, fit_raw_qfq_mapping


@dataclass(frozen=True)
class VcbPairedInputReport:
    rows: int
    mapping_evaluable_rows: int
    mapping_unknown_rows: int


def write_vcb_paired_input(
    raw_path: str | Path,
    qfq_path: str | Path,
    output_path: str | Path,
    *,
    batch_size: int = 250_000,
) -> VcbPairedInputReport:
    """Join immutable raw/qfq snapshots into the VCB research input.

    The pair audit guarantees equal `(date, code)` keys. This adapter verifies
    that contract batch by batch, copies raw execution prices and qfq structural
    prices without altering either, then derives the already-approved daily
    mapping fields required by the VCB execution model.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    raw, qfq, output = Path(raw_path), Path(qfq_path), Path(output_path)
    if output.exists():
        raise FileExistsError(f"VCB paired input output already exists: {output}")
    raw_file, qfq_file = pq.ParquetFile(raw), pq.ParquetFile(qfq)
    required = {"date", "code", "open", "high", "low", "close", "pre_close", "volume", "amount"}
    for label, source in (("raw", raw_file), ("qfq", qfq_file)):
        missing = sorted(required - set(source.schema_arrow.names))
        if missing:
            raise ValueError(f"{label} snapshot missing required fields: {missing}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    writer: pq.ParquetWriter | None = None
    rows = evaluable = unknown = 0
    try:
        raw_batches = raw_file.iter_batches(batch_size=batch_size)
        qfq_batches = qfq_file.iter_batches(batch_size=batch_size)
        for raw_batch, qfq_batch in zip_longest(raw_batches, qfq_batches):
            if raw_batch is None or qfq_batch is None:
                raise ValueError("raw/qfq snapshot batch counts differ")
            raw_frame, qfq_frame = raw_batch.to_pandas(), qfq_batch.to_pandas()
            if len(raw_frame) != len(qfq_frame) or not raw_frame[["date", "code"]].equals(qfq_frame[["date", "code"]]):
                raise ValueError("raw/qfq snapshot primary keys differ")
            paired = _pair_batch(raw_frame, qfq_frame)
            unknown_rows = int(paired["adjustment_status"].ne("KNOWN_AFFINE_RAW_QFQ_VALIDATED").sum())
            unknown_rows += int(paired["adjustment_status"].eq("UNKNOWN_AFFINE_FIT").sum())
            if unknown_rows:
                paired = apply_daily_ratio_mapping(paired)
            still_unknown = int(paired["adjustment_status"].eq("UNKNOWN_AFFINE_FIT").sum())
            if still_unknown:
                raise ValueError(f"VCB paired input contains {still_unknown} unevaluable raw/qfq mappings")
            table = pa.Table.from_pandas(paired, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temp, table.schema, compression="snappy")
            writer.write_table(table)
            rows += len(paired)
            evaluable += len(paired) - still_unknown
            unknown += still_unknown
        if writer is None:
            raise ValueError("raw/qfq snapshots contain no rows")
        writer.close()
        writer = None
        os.replace(temp, output)
        return VcbPairedInputReport(rows=rows, mapping_evaluable_rows=evaluable, mapping_unknown_rows=unknown)
    finally:
        if writer is not None:
            writer.close()
        if temp.exists():
            temp.unlink()


def _pair_batch(raw: pd.DataFrame, qfq: pd.DataFrame) -> pd.DataFrame:
    out = qfq.copy()
    for field in ("open", "high", "low", "close", "pre_close"):
        out[f"raw_{field}"] = pd.to_numeric(raw[field], errors="coerce")
        out[f"adj_{field}"] = pd.to_numeric(qfq[field], errors="coerce")
        out[field] = out[f"adj_{field}"]
    for field in ("volume", "amount"):
        out[field] = pd.to_numeric(raw[field], errors="coerce")
    out = fit_raw_qfq_mapping(out)
    return out
