"""Expand QMT's downloaded historical ST/PT event file to daily truth values."""

from __future__ import annotations

import bisect
import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from texperiment.data.historical_st import HistoricalSTError

_CONTRACT = "QMT_HISTORICAL_ST_STATUS_V1"
_QMT_FILE_NAME = "SH_XXXXXX_2011_86400000.csv"
_CODE_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_TRUE_FLAGS = {"1", "2", "3"}  # ST, *ST, PT per xtdata.get_his_st_data
_FALSE_FLAG = "0"
_LAST_DATE = "20380119"
_SCHEMA = pa.schema(
    [
        pa.field("date", pa.timestamp("ns"), nullable=False),
        pa.field("code", pa.string(), nullable=False),
        pa.field("historical_st_status", pa.string(), nullable=False),
    ]
)


@dataclass(frozen=True)
class QmtHistoricalSTReport:
    output: Path
    rows: int
    st_true_rows: int
    st_false_rows: int
    source_event_rows: int
    source_codes: int
    start_date: str
    end_date: str
    source_sha256: str


def write_historical_st_status_from_qmt(
    daily_input: str | Path,
    qmt_input: str | Path,
    output: str | Path,
    *,
    start_date: str,
    end_date: str,
    batch_size: int = 100_000,
) -> QmtHistoricalSTReport:
    """Create exact target-grain status from QMT's complete ST/PT event file."""
    if batch_size <= 0:
        raise HistoricalSTError("batch_size must be positive")
    if start_date > end_date:
        raise HistoricalSTError("start_date must not be later than end_date")
    daily, source, target = Path(daily_input), Path(qmt_input), Path(output)
    if not daily.is_file() or not source.is_file():
        raise HistoricalSTError("daily input and QMT historical-ST input must both be files")
    if target.exists():
        raise FileExistsError(f"historical ST output already exists: {target}")

    intervals, source_rows = _read_qmt_intervals(source)
    if source_rows == 0:
        raise HistoricalSTError("QMT historical-ST input contains no events")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    duplicate_db = temporary.with_suffix(temporary.suffix + ".keys.sqlite")
    writer: pq.ParquetWriter | None = None
    tracker: sqlite3.Connection | None = None
    rows = true_rows = false_rows = 0
    try:
        parquet = pq.ParquetFile(daily)
        if {"date", "code"} - set(parquet.schema_arrow.names):
            raise HistoricalSTError("daily input must contain date and code")
        schema = _SCHEMA.with_metadata(
            {
                b"contract_id": _CONTRACT.encode(),
                b"source": b"QMT.xtdata.download_his_st_data",
                b"source_file_name": source.name.encode(),
                b"source_sha256": _sha256(source).encode(),
                b"daily_input_sha256": _sha256(daily).encode(),
                b"start_date": start_date.encode(),
                b"end_date": end_date.encode(),
            }
        )
        writer = pq.ParquetWriter(temporary, schema, compression="snappy")
        tracker = sqlite3.connect(duplicate_db)
        tracker.execute(
            "CREATE TABLE pairs (trade_date TEXT NOT NULL, code TEXT NOT NULL, "
            "PRIMARY KEY (trade_date, code)) WITHOUT ROWID"
        )
        for batch in parquet.iter_batches(batch_size=batch_size, columns=["date", "code"]):
            output_dates: list[datetime] = []
            output_codes: list[str] = []
            statuses: list[str] = []
            keys: list[tuple[str, str]] = []
            for value, code in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist(), strict=True):
                trade_date = _date_string(value)
                if not (start_date <= trade_date <= end_date):
                    continue
                normalized = str(code).upper()
                if not _CODE_RE.fullmatch(normalized):
                    raise HistoricalSTError(f"invalid daily-input code: {code!r}")
                key = (trade_date, normalized)
                keys.append(key)
                is_st = _is_true_on_date(intervals.get(normalized), trade_date.replace("-", ""))
                output_dates.append(_as_datetime(value))
                output_codes.append(normalized)
                statuses.append("TRUE" if is_st else "FALSE")
                true_rows += int(is_st)
                false_rows += int(not is_st)
            if statuses:
                try:
                    tracker.executemany("INSERT INTO pairs VALUES (?, ?)", keys)
                except sqlite3.IntegrityError as exc:
                    raise HistoricalSTError("duplicate daily-input (date, code)") from exc
                writer.write_table(
                    pa.Table.from_arrays(
                        [pa.array(output_dates, type=pa.timestamp("ns")), pa.array(output_codes), pa.array(statuses)],
                        schema=schema,
                    )
                )
                rows += len(statuses)
        if rows == 0:
            raise HistoricalSTError("daily input has no rows in requested date range")
        writer.close()
        writer = None
        os.replace(temporary, target)
    finally:
        if writer is not None:
            writer.close()
        if tracker is not None:
            tracker.close()
        if temporary.exists():
            temporary.unlink()
        if duplicate_db.exists():
            duplicate_db.unlink()

    return QmtHistoricalSTReport(
        output=target,
        rows=rows,
        st_true_rows=true_rows,
        st_false_rows=false_rows,
        source_event_rows=source_rows,
        source_codes=len(intervals),
        start_date=start_date,
        end_date=end_date,
        source_sha256=_sha256(source),
    )


def _read_qmt_intervals(path: Path) -> tuple[dict[str, tuple[list[str], list[str]]], int]:
    events: dict[str, list[tuple[str, str]]] = {}
    seen: set[tuple[str, str]] = set()
    rows = 0
    encoding = _qmt_text_encoding(path)
    with path.open(encoding=encoding, errors="strict") as handle:
        for line_number, line in enumerate(handle, start=1):
            columns = [part.strip() for part in line.rstrip("\r\n").split(",")]
            if len(columns) < 4:
                raise HistoricalSTError(f"malformed QMT historical-ST row at line {line_number}")
            code, event_date, flag = columns[0].upper(), columns[2], columns[3]
            if not _CODE_RE.fullmatch(code):
                raise HistoricalSTError(f"invalid QMT code at line {line_number}: {code!r}")
            if len(event_date) != 8 or not event_date.isdigit():
                raise HistoricalSTError(f"invalid QMT event date at line {line_number}: {event_date!r}")
            try:
                datetime.strptime(event_date, "%Y%m%d")
            except ValueError as exc:
                raise HistoricalSTError(
                    f"invalid QMT event date at line {line_number}: {event_date!r}"
                ) from exc
            if flag not in _TRUE_FLAGS | {_FALSE_FLAG}:
                raise HistoricalSTError(f"unknown QMT ST/PT flag at line {line_number}: {flag!r}")
            key = (code, event_date)
            if key in seen:
                raise HistoricalSTError(f"duplicate QMT (code, event_date): {key}")
            seen.add(key)
            events.setdefault(code, []).append((event_date, flag))
            rows += 1

    result: dict[str, tuple[list[str], list[str]]] = {}
    for code, code_events in events.items():
        code_events.sort()
        starts: list[str] = []
        ends: list[str] = []
        for index, (event_date, flag) in enumerate(code_events):
            if flag not in _TRUE_FLAGS:
                continue
            starts.append(event_date)
            ends.append(code_events[index + 1][0] if index + 1 < len(code_events) else _LAST_DATE)
        result[code] = (starts, ends)
    return result, rows


def _qmt_text_encoding(path: Path) -> str:
    """Use QMT's Windows encoding unless the export carries a UTF-8 BOM."""
    with path.open("rb") as handle:
        return "utf-8-sig" if handle.read(3) == b"\xef\xbb\xbf" else "gb18030"


def _is_true_on_date(intervals: tuple[list[str], list[str]] | None, trade_date: str) -> bool:
    if not intervals or not intervals[0]:
        return False
    starts, ends = intervals
    index = bisect.bisect_right(starts, trade_date) - 1
    return index >= 0 and trade_date <= ends[index]


def _date_string(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["QmtHistoricalSTReport", "write_historical_st_status_from_qmt", "_QMT_FILE_NAME"]
