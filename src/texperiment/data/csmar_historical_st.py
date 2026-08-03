"""Build exact point-in-time ST status from a licensed CSMAR daily export."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from texperiment.data.historical_st import HistoricalSTError

_CONTRACT = "CSMAR_TRDSTA_HISTORICAL_ST_V1"
_TRUE_STATUSES = {2, 3, 5, 6, 8, 9, 11, 12, 14, 15}
_FALSE_STATUSES = {1, 4, 7, 10, 13, 16}
_SCHEMA = pa.schema(
    [
        pa.field("date", pa.timestamp("ns"), nullable=False),
        pa.field("code", pa.string(), nullable=False),
        pa.field("historical_st_status", pa.string(), nullable=False),
    ]
)


@dataclass(frozen=True)
class CsmarHistoricalSTReport:
    output: Path
    rows: int
    st_true_rows: int
    st_false_rows: int
    source_rows: int
    start_date: str
    end_date: str
    source_sha256: str


def write_historical_st_status_from_csmar(
    daily_input: str | Path,
    csmar_input: str | Path,
    output: str | Path,
    *,
    start_date: str,
    end_date: str,
    batch_size: int = 100_000,
) -> CsmarHistoricalSTReport:
    """Write an exact daily-bar status table from CSMAR ``Trdsta`` records.

    ``Trdsta`` values 2, 3, 5, 6, 8, 9, 11, 12, 14 and 15 are ST variants;
    its documented non-ST values 1, 4, 7, 10, 13 and 16 map to ``FALSE``.
    Any unknown value or missing target key is a hard failure.
    """
    if batch_size <= 0:
        raise HistoricalSTError("batch_size must be positive")
    daily, source, target = Path(daily_input), Path(csmar_input), Path(output)
    if not daily.is_file() or not source.is_file():
        raise HistoricalSTError("daily input and CSMAR input must both be files")
    if target.exists():
        raise FileExistsError(f"historical ST output already exists: {target}")
    if start_date > end_date:
        raise HistoricalSTError("start_date must not be later than end_date")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    database = temporary.with_suffix(temporary.suffix + ".csmar.sqlite")
    conn: sqlite3.Connection | None = None
    writer: pq.ParquetWriter | None = None
    source_rows = output_rows = true_rows = false_rows = 0
    try:
        conn = sqlite3.connect(database)
        conn.execute("CREATE TABLE csmar_status (trade_date TEXT NOT NULL, code6 TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY (trade_date, code6)) WITHOUT ROWID")
        source_rows = _load_csmar_statuses(conn, source, start_date, end_date, batch_size)

        daily_file = pq.ParquetFile(daily)
        if {"date", "code"} - set(daily_file.schema_arrow.names):
            raise HistoricalSTError("daily input must contain date and code")
        metadata = {
            b"contract_id": _CONTRACT.encode(),
            b"source": b"CSMAR_TRD_Dalyr.Trdsta",
            b"source_sha256": _sha256(source).encode(),
            b"daily_input_sha256": _sha256(daily).encode(),
            b"start_date": start_date.encode(),
            b"end_date": end_date.encode(),
        }
        schema = _SCHEMA.with_metadata(metadata)
        writer = pq.ParquetWriter(temporary, schema, compression="snappy")
        seen = sqlite3.connect(":memory:")
        seen.execute("CREATE TABLE pairs (trade_date TEXT NOT NULL, code TEXT NOT NULL, PRIMARY KEY (trade_date, code)) WITHOUT ROWID")
        try:
            for batch in daily_file.iter_batches(batch_size=batch_size, columns=["date", "code"]):
                date_values, code_values = batch.column(0).to_pylist(), batch.column(1).to_pylist()
                records: list[tuple[datetime, str, str]] = []
                duplicate_keys: list[tuple[str, str]] = []
                for value, code in zip(date_values, code_values, strict=True):
                    trade_date = _date_string(value)
                    if not (start_date <= trade_date <= end_date):
                        continue
                    normalized = str(code).upper()
                    if len(normalized) != 9 or normalized[6] != ".":
                        raise HistoricalSTError(f"invalid daily-input code: {code!r}")
                    duplicate_keys.append((trade_date, normalized))
                    status = _lookup_status(conn, trade_date, normalized[:6])
                    if status is None:
                        raise HistoricalSTError(f"CSMAR Trdsta missing target (date, code): {(trade_date, normalized)}")
                    records.append((_as_datetime(value), normalized, status))
                if records:
                    try:
                        seen.executemany("INSERT INTO pairs VALUES (?, ?)", duplicate_keys)
                    except sqlite3.IntegrityError as exc:
                        raise HistoricalSTError("duplicate daily-input (date, code)") from exc
                    statuses = [record[2] for record in records]
                    writer.write_table(
                        pa.Table.from_arrays(
                            [
                                pa.array([record[0] for record in records], type=pa.timestamp("ns")),
                                pa.array([record[1] for record in records]),
                                pa.array(statuses),
                            ],
                            schema=schema,
                        )
                    )
                    output_rows += len(records)
                    true_rows += statuses.count("TRUE")
                    false_rows += statuses.count("FALSE")
        finally:
            seen.close()
        if output_rows == 0:
            raise HistoricalSTError("daily input has no rows in requested date range")
        writer.close()
        writer = None
        os.replace(temporary, target)
    finally:
        if writer is not None:
            writer.close()
        if conn is not None:
            conn.close()
        if temporary.exists():
            temporary.unlink()
        if database.exists():
            database.unlink()

    return CsmarHistoricalSTReport(
        output=target,
        rows=output_rows,
        st_true_rows=true_rows,
        st_false_rows=false_rows,
        source_rows=source_rows,
        start_date=start_date,
        end_date=end_date,
        source_sha256=_sha256(source),
    )


def _load_csmar_statuses(conn: sqlite3.Connection, source: Path, start_date: str, end_date: str, batch_size: int) -> int:
    rows = 0
    for frame in _read_source_chunks(source, batch_size):
        columns = {str(column).lower(): str(column) for column in frame.columns}
        required = {"stkcd", "trddt", "trdsta"}
        if missing := required - set(columns):
            raise HistoricalSTError(f"CSMAR input missing required columns: {sorted(missing)}")
        records: list[tuple[str, str, str]] = []
        for code, trade_date, value in zip(frame[columns["stkcd"]], frame[columns["trddt"]], frame[columns["trdsta"]], strict=True):
            date_text = _date_string(trade_date)
            if not (start_date <= date_text <= end_date):
                continue
            code6 = str(code).split(".")[0].zfill(6)
            if len(code6) != 6 or not code6.isdigit():
                raise HistoricalSTError(f"invalid CSMAR Stkcd: {code!r}")
            try:
                parsed = int(float(value))
            except (TypeError, ValueError) as exc:
                raise HistoricalSTError(f"invalid CSMAR Trdsta: {value!r}") from exc
            if parsed in _TRUE_STATUSES:
                status = "TRUE"
            elif parsed in _FALSE_STATUSES:
                status = "FALSE"
            else:
                raise HistoricalSTError(f"unknown CSMAR Trdsta code: {parsed}")
            records.append((date_text, code6, status))
        try:
            conn.executemany("INSERT INTO csmar_status VALUES (?, ?, ?)", records)
        except sqlite3.IntegrityError as exc:
            raise HistoricalSTError("duplicate CSMAR (Trddt, Stkcd)") from exc
        rows += len(records)
    if rows == 0:
        raise HistoricalSTError("CSMAR input has no rows in requested date range")
    return rows


def _read_source_chunks(source: Path, batch_size: int):
    if source.suffix.lower() == ".parquet":
        parquet = pq.ParquetFile(source)
        for batch in parquet.iter_batches(batch_size=batch_size):
            yield batch.to_pandas()
        return
    for frame in pd.read_csv(source, chunksize=batch_size, dtype=str):
        yield frame


def _lookup_status(conn: sqlite3.Connection, trade_date: str, code6: str) -> str | None:
    row = conn.execute("SELECT status FROM csmar_status WHERE trade_date = ? AND code6 = ?", (trade_date, code6)).fetchone()
    return str(row[0]) if row is not None else None


def _date_string(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return pd.Timestamp(value).date().isoformat()


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return pd.Timestamp(value).to_pydatetime().replace(tzinfo=None)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
