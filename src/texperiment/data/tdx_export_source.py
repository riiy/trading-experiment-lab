from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from texperiment.data.normalizer import normalize_daily_bars
from texperiment.data.quality import DataQualityReport, validate_daily_bars

_FILE_RE = re.compile(r"^(SH|SZ|BJ)#(\d{6})\.txt$", re.IGNORECASE)
_RAW_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]
_ST_RE = re.compile(r"\*?ST", re.IGNORECASE)


@dataclass(frozen=True)
class TdxExportReport:
    files_seen: int
    files_ingested: int
    files_skipped: int
    rows: int
    stock_count: int


def iter_tdx_export_files(input_path: str | Path):
    root = Path(input_path)
    for path in sorted(root.rglob("*.txt")):
        match = _FILE_RE.fullmatch(path.name)
        if match and _is_a_share_code(match.group(1).upper(), match.group(2)):
            yield path


def read_tdx_export_file(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    match = _FILE_RE.fullmatch(path.name)
    if match is None or not _is_a_share_code(match.group(1).upper(), match.group(2)):
        raise ValueError(f"Not an A-share TDX export file: {path}")

    market, raw_code = match.group(1).upper(), match.group(2)
    with path.open("r", encoding="gb18030", errors="replace") as handle:
        header = handle.readline()
    header_parts = header.strip().split()
    name = header_parts[1] if len(header_parts) > 1 else ""
    adj_type = "qfq" if "前复权" in header else "hfq" if "后复权" in header else "none"

    try:
        raw = pd.read_csv(
            path,
            encoding="gb18030",
            skiprows=2,
            header=None,
            names=_RAW_COLUMNS,
            usecols=range(7),
            comment="#",
        )
    except pd.errors.EmptyDataError:
        return _empty_canonical_frame()
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    for column in _RAW_COLUMNS[1:]:
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw = raw.dropna(subset=["date", "open", "high", "low", "close", "volume", "amount"])
    raw = raw.sort_values("date").drop_duplicates("date", keep="last")
    raw = raw.loc[(raw[["open", "high", "low", "close"]] > 0).all(axis=1)].copy()
    if raw.empty:
        return _empty_canonical_frame()

    raw["code"] = f"{raw_code}.{market}"
    raw["name"] = name
    raw["pre_close"] = raw["close"].shift(1)
    raw["pct_chg"] = (raw["close"] / raw["pre_close"] - 1) * 100
    raw["trade_status"] = raw.apply(
        lambda row: "0" if row["volume"] <= 0 or row["amount"] <= 0 else "1", axis=1
    )
    raw["is_suspended"] = raw["trade_status"] == "0"
    raw["is_st"] = name != "" and bool(_ST_RE.search(name))
    first_date = raw["date"].min()
    raw["listing_days"] = (raw["date"] - first_date).dt.days + 1
    return normalize_daily_bars(
        raw,
        provider="canonical",
        adj_type=adj_type,
        source="tongdaxin_export",
        source_file=path,
    )


def write_tdx_export_parquet(
    input_path: str | Path,
    output_path: str | Path,
    *,
    strict: bool = True,
) -> tuple[DataQualityReport, TdxExportReport]:
    root = Path(input_path)
    files = list(iter_tdx_export_files(root))
    if not files:
        raise FileNotFoundError(f"No A-share TDX export files found under {root}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    writer: pq.ParquetWriter | None = None
    quality = _empty_quality_report()
    files_ingested = 0
    rows = 0
    codes: set[str] = set()
    try:
        for path in files:
            frame = read_tdx_export_file(path)
            if frame.empty:
                continue
            report = validate_daily_bars(frame, strict=False)
            quality = _combine_quality_reports(quality, report)
            table = pa.Table.from_pandas(frame, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temp_output, table.schema, compression="snappy")
            writer.write_table(table)
            files_ingested += 1
            rows += len(frame)
            codes.update(frame["code"].unique())

        if writer is None:
            raise ValueError(f"A-share TDX export files contained no valid rows under {root}")
        if strict and not quality.ok:
            raise ValueError(f"daily bars quality check failed: {quality}")
        os.replace(temp_output, output)
        return quality, TdxExportReport(len(files), files_ingested, len(files) - files_ingested, rows, len(codes))
    finally:
        if writer is not None:
            writer.close()
        if temp_output.exists():
            temp_output.unlink()


def read_tdx_index_export_file(path: str | Path, *, code: str = "000300.SH") -> pd.DataFrame:
    """Read one TDX index text export into canonical daily-bar fields."""
    path = Path(path)
    with path.open("r", encoding="gb18030", errors="replace") as handle:
        header = handle.readline()
    parts = header.strip().split()
    name = parts[1] if len(parts) > 1 else ""
    adj_type = "qfq" if "前复权" in header else "hfq" if "后复权" in header else "none"
    try:
        raw = pd.read_csv(
            path, encoding="gb18030", skiprows=2, header=None,
            names=_RAW_COLUMNS, usecols=range(7), comment="#",
        )
    except pd.errors.EmptyDataError:
        return _empty_canonical_frame()
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    for column in _RAW_COLUMNS[1:]:
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw = raw.dropna(subset=_RAW_COLUMNS).sort_values("date").drop_duplicates("date", keep="last")
    raw = raw.loc[(raw[["open", "high", "low", "close"]] > 0).all(axis=1)].copy()
    if raw.empty:
        return _empty_canonical_frame()
    raw["code"] = code
    raw["name"] = name
    raw["pre_close"] = raw["close"].shift(1)
    raw["pct_chg"] = (raw["close"] / raw["pre_close"] - 1) * 100
    raw["trade_status"] = "1"
    raw["is_suspended"] = False
    raw["is_limit_up"] = False
    raw["is_limit_down"] = False
    raw["is_st"] = False
    raw["listing_days"] = (raw["date"] - raw["date"].min()).dt.days + 1
    return normalize_daily_bars(
        raw, provider="canonical", adj_type=adj_type,
        source="tongdaxin_export", source_file=path,
    )


def write_tdx_index_parquet(
    input_path: str | Path,
    output_path: str | Path,
    *,
    code: str = "000300.SH",
) -> DataQualityReport:
    frame = read_tdx_index_export_file(input_path, code=code)
    if frame.empty:
        raise ValueError(f"Index export contains no valid rows: {input_path}")
    report = validate_daily_bars(frame)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output, index=False)
    return report


def _is_a_share_code(market: str, code: str) -> bool:
    if market == "SH":
        return code.startswith(("600", "601", "603", "605", "688", "689", "900"))
    if market == "SZ":
        return code.startswith(("000", "001", "002", "003", "300", "301"))
    return code.startswith(("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "920"))


def _empty_canonical_frame() -> pd.DataFrame:
    return pd.DataFrame()


def _empty_quality_report() -> DataQualityReport:
    return DataQualityReport(0, 0, None, None, 0, 0, 0, 0, 0)


def _combine_quality_reports(left: DataQualityReport, right: DataQualityReport) -> DataQualityReport:
    min_dates = [value for value in (left.min_date, right.min_date) if value is not None]
    max_dates = [value for value in (left.max_date, right.max_date) if value is not None]
    return DataQualityReport(
        rows=left.rows + right.rows,
        code_count=left.code_count + right.code_count,
        min_date=min(min_dates) if min_dates else None,
        max_date=max(max_dates) if max_dates else None,
        duplicate_bars=left.duplicate_bars + right.duplicate_bars,
        null_required_cells=left.null_required_cells + right.null_required_cells,
        non_positive_price_rows=left.non_positive_price_rows + right.non_positive_price_rows,
        negative_volume_rows=left.negative_volume_rows + right.negative_volume_rows,
        negative_amount_rows=left.negative_amount_rows + right.negative_amount_rows,
    )
