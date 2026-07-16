from __future__ import annotations

import re
import struct
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from texperiment.data.normalizer import normalize_daily_bars
from texperiment.data.quality import DataQualityReport, validate_daily_bars

_RECORD = struct.Struct("<5IfII")
_DAY_DTYPE = np.dtype(
    [
        ("date", "<u4"),
        ("open", "<u4"),
        ("high", "<u4"),
        ("low", "<u4"),
        ("close", "<u4"),
        ("amount", "<f4"),
        ("volume", "<u4"),
        ("reserved", "<u4"),
    ]
)
_FILE_RE = re.compile(r"^(sh|sz|bj)(\d{6})\.day$", re.IGNORECASE)


def ingest_tdx_a_share_daily(
    input_path: str | Path,
    *,
    adj_type: str = "none",
) -> pd.DataFrame:
    """Read TongdaXin vipdoc .day files into canonical A-share daily bars.

    TongdaXin stores prices in cents, amount in CNY, and volume in lots.
    Files are normally under vipdoc/{sh,sz,bj}/lday/.
    """
    if adj_type != "none":
        raise ValueError("TongdaXin .day files are unadjusted; adj_type must be none")

    root = Path(input_path)
    files = sorted(path for path in root.rglob("*.day") if _is_a_share_file(path))
    if not files:
        raise FileNotFoundError(f"No TongdaXin .day files found under {root}")

    frames = list(_iter_tdx_frames(files, adj_type=adj_type))

    if not frames:
        raise ValueError(f"TongdaXin .day files contained no records under {root}")
    return (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(["date", "code"], keep="last")
        .sort_values(["code", "date"])
        .reset_index(drop=True)
    )


def write_tdx_parquet(
    input_path: str | Path,
    output_path: str | Path,
    *,
    adj_type: str = "none",
    strict: bool = True,
) -> DataQualityReport:
    """Stream TDX files into Parquet without retaining the full market in memory."""
    root = Path(input_path)
    files = sorted(path for path in root.rglob("*.day") if _is_a_share_file(path))
    if not files:
        raise FileNotFoundError(f"No TongdaXin .day files found under {root}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    writer: pq.ParquetWriter | None = None
    total = _empty_quality_report()
    wrote_rows = False
    try:
        for frame in _iter_tdx_frames(files, adj_type=adj_type):
            report = validate_daily_bars(frame, strict=False)
            total = _combine_quality_reports(total, report)
            table = pa.Table.from_pandas(frame, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temp_output, table.schema, compression="snappy")
            writer.write_table(table)
            wrote_rows = True
        if not wrote_rows:
            raise ValueError(f"TongdaXin .day files contained no records under {root}")
        if strict and not total.ok:
            raise ValueError(f"daily bars quality check failed: {total}")
        os.replace(temp_output, output)
        return total
    finally:
        if writer is not None:
            writer.close()
        if temp_output.exists():
            temp_output.unlink()


def _iter_tdx_frames(files: list[Path], *, adj_type: str):
    for path in files:
        match = _FILE_RE.fullmatch(path.name)
        assert match is not None
        market, digits = match.group(1).upper(), match.group(2)
        raw = _read_day_file(path, market, digits)
        if raw.empty:
            continue
        yield normalize_daily_bars(
            raw,
            provider="canonical",
            adj_type=adj_type,
            source="tongdaxin",
            source_file=path,
        ).drop_duplicates(["date", "code"], keep="last")


def _empty_quality_report() -> DataQualityReport:
    return DataQualityReport(0, 0, None, None, 0, 0, 0, 0, 0)


def _combine_quality_reports(left: DataQualityReport, right: DataQualityReport) -> DataQualityReport:
    dates = [date for date in (left.min_date, right.min_date) if date is not None]
    max_dates = [date for date in (left.max_date, right.max_date) if date is not None]
    return DataQualityReport(
        rows=left.rows + right.rows,
        code_count=left.code_count + right.code_count,
        min_date=min(dates) if dates else None,
        max_date=max(max_dates) if max_dates else None,
        duplicate_bars=left.duplicate_bars + right.duplicate_bars,
        null_required_cells=left.null_required_cells + right.null_required_cells,
        non_positive_price_rows=left.non_positive_price_rows + right.non_positive_price_rows,
        negative_volume_rows=left.negative_volume_rows + right.negative_volume_rows,
        negative_amount_rows=left.negative_amount_rows + right.negative_amount_rows,
    )


def _read_day_file(path: Path, market: str, digits: str) -> pd.DataFrame:
    payload = path.read_bytes()
    if len(payload) % _RECORD.size:
        raise ValueError(f"Invalid TongdaXin .day file size: {path}")

    records = np.frombuffer(payload, dtype=_DAY_DTYPE)
    records = records[records["date"] != 0]
    out = pd.DataFrame(
        {
            "date": records["date"].astype(str),
            "code": f"{digits}.{market}",
            "open": records["open"] / 100,
            "high": records["high"] / 100,
            "low": records["low"] / 100,
            "close": records["close"] / 100,
            "volume": records["volume"] * 100,
            "amount": records["amount"],
        }
    )
    if not out.empty:
        out["pre_close"] = out["close"].shift(1)
        out["is_suspended"] = out["volume"] == 0
    return out


def _is_a_share_file(path: Path) -> bool:
    match = _FILE_RE.fullmatch(path.name)
    if match is None:
        return False
    market, digits = match.group(1).lower(), match.group(2)
    if market == "sh":
        return digits.startswith(("600", "601", "603", "605", "688", "689", "900"))
    if market == "sz":
        return digits.startswith(("000", "001", "002", "003", "200", "300", "301"))
    return digits.startswith(("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "920"))
