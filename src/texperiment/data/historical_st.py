"""Point-in-time A-share ST history acquisition and coverage validation.

The authoritative input is Tushare Pro's ``stock_st`` endpoint.  It returns
the daily ST membership only, so this module expands the complement against a
specified daily-bar input.  The resulting parquet therefore has exactly the
same (date, code) grain as that input and contains no inferred ``UNKNOWN``
values.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

_TUSHARE_ENDPOINT = "https://api.tushare.pro"
_CODE_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_RAW_CONTRACT = "TUSHARE_STOCK_ST_RAW_V1"
_STATUS_CONTRACT = "HISTORICAL_ST_STATUS_COVERAGE_V1"
_RAW_FIELDS = ("ts_code", "name", "trade_date", "type", "type_name")
_STATUS_SCHEMA = pa.schema(
    [
        pa.field("date", pa.timestamp("ns"), nullable=False),
        pa.field("code", pa.string(), nullable=False),
        pa.field("historical_st_status", pa.string(), nullable=False),
    ]
)


class HistoricalSTError(ValueError):
    """Raised when the ST history cannot satisfy the formal coverage contract."""


@dataclass(frozen=True)
class HistoricalSTFetchReport:
    raw_output: Path
    requested_trade_dates: int
    st_membership_rows: int
    start_date: str
    end_date: str


@dataclass(frozen=True)
class HistoricalSTCoverageReport:
    output: Path
    rows: int
    st_true_rows: int
    st_false_rows: int
    source_membership_rows_unrepresented_in_daily_input: int
    start_date: str
    end_date: str
    source_raw_sha256: str


def fetch_tushare_stock_st_raw(
    daily_input: str | Path,
    raw_output: str | Path,
    *,
    start_date: str,
    end_date: str,
    token_env: str = "TUSHARE_TOKEN",
    timeout_seconds: int = 30,
) -> HistoricalSTFetchReport:
    """Fetch one authenticated ``stock_st`` response for every input trade date.

    The token is read only from the named environment variable and is never
    written to the raw evidence file.
    """
    token = os.environ.get(token_env)
    if not token:
        raise HistoricalSTError(f"missing required Tushare token environment variable: {token_env}")
    if timeout_seconds <= 0:
        raise HistoricalSTError("timeout_seconds must be positive")

    dates = _trade_dates_in_scope(daily_input, start_date=start_date, end_date=end_date)
    output = Path(raw_output)
    if output.exists():
        raise FileExistsError(f"raw ST output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    memberships = 0
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            for trade_date in dates:
                response = _tushare_stock_st_request(token, trade_date, timeout_seconds)
                members = _validate_tushare_response(response, trade_date)
                memberships += len(members)
                record = {
                    "contract_id": _RAW_CONTRACT,
                    "endpoint": _TUSHARE_ENDPOINT,
                    "api_name": "stock_st",
                    "queried_trade_date": trade_date,
                    "response": response,
                }
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()

    return HistoricalSTFetchReport(
        raw_output=output,
        requested_trade_dates=len(dates),
        st_membership_rows=memberships,
        start_date=start_date,
        end_date=end_date,
    )


def write_historical_st_status_from_tushare_raw(
    daily_input: str | Path,
    raw_input: str | Path,
    output: str | Path,
    *,
    start_date: str,
    end_date: str,
    batch_size: int = 100_000,
) -> HistoricalSTCoverageReport:
    """Expand authenticated daily ST lists to exact daily-bar (date, code) grain."""
    if batch_size <= 0:
        raise HistoricalSTError("batch_size must be positive")
    source = Path(daily_input)
    raw = Path(raw_input)
    target = Path(output)
    if not source.is_file():
        raise HistoricalSTError(f"daily input is missing: {source}")
    if not raw.is_file():
        raise HistoricalSTError(f"raw ST input is missing: {raw}")
    if target.exists():
        raise FileExistsError(f"historical ST output already exists: {target}")

    requested_dates = _trade_dates_in_scope(source, start_date=start_date, end_date=end_date)
    st_pairs = _read_tushare_memberships(raw, requested_dates)

    parquet = pq.ParquetFile(source)
    required = {"date", "code"}
    missing = required - set(parquet.schema_arrow.names)
    if missing:
        raise HistoricalSTError(f"daily input missing required columns: {sorted(missing)}")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    writer: pq.ParquetWriter | None = None
    rows = true_rows = false_rows = 0
    seen_st_pairs: set[tuple[str, str]] = set()
    duplicate_db = temporary.with_suffix(temporary.suffix + ".keys.sqlite")
    tracker: sqlite3.Connection | None = None
    try:
        metadata = {
            b"contract_id": _STATUS_CONTRACT.encode(),
            b"source": b"tushare.stock_st",
            b"source_raw_sha256": _sha256(raw).encode(),
            b"daily_input_sha256": _sha256(source).encode(),
            b"start_date": start_date.encode(),
            b"end_date": end_date.encode(),
        }
        schema = _STATUS_SCHEMA.with_metadata(metadata)
        writer = pq.ParquetWriter(temporary, schema, compression="snappy")
        tracker = sqlite3.connect(duplicate_db)
        tracker.execute("CREATE TABLE seen_pairs (trade_date TEXT NOT NULL, code TEXT NOT NULL, PRIMARY KEY (trade_date, code)) WITHOUT ROWID")
        for batch in parquet.iter_batches(batch_size=batch_size, columns=["date", "code"]):
            dates = batch.column(0).to_pylist()
            codes = batch.column(1).to_pylist()
            out_dates: list[datetime] = []
            out_codes: list[str] = []
            statuses: list[str] = []
            batch_keys: list[tuple[str, str]] = []
            for value, code in zip(dates, codes, strict=True):
                trade_date = _as_date_string(value)
                normalized_code = str(code).upper()
                if not (start_date <= trade_date <= end_date):
                    continue
                if not _CODE_RE.fullmatch(normalized_code):
                    raise HistoricalSTError(f"invalid daily-input code: {code!r}")
                key = (trade_date, normalized_code)
                batch_keys.append(key)
                out_dates.append(_as_datetime(value))
                out_codes.append(normalized_code)
                is_st = key in st_pairs
                if is_st:
                    seen_st_pairs.add(key)
                statuses.append("TRUE" if is_st else "FALSE")
                true_rows += int(is_st)
                false_rows += int(not is_st)
            if statuses:
                try:
                    tracker.executemany("INSERT INTO seen_pairs VALUES (?, ?)", batch_keys)
                except sqlite3.IntegrityError as exc:
                    raise HistoricalSTError("duplicate daily-input (date, code)") from exc
                table = pa.Table.from_arrays(
                    [pa.array(out_dates, type=pa.timestamp("ns")), pa.array(out_codes), pa.array(statuses)],
                    schema=schema,
                )
                writer.write_table(table)
                rows += len(statuses)
        writer.close()
        writer = None
        if rows == 0:
            raise HistoricalSTError("daily input has no rows in requested date range")
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

    return HistoricalSTCoverageReport(
        output=target,
        rows=rows,
        st_true_rows=true_rows,
        st_false_rows=false_rows,
        source_membership_rows_unrepresented_in_daily_input=len(st_pairs - seen_st_pairs),
        start_date=start_date,
        end_date=end_date,
        source_raw_sha256=_sha256(raw),
    )


def _trade_dates_in_scope(daily_input: str | Path, *, start_date: str, end_date: str) -> list[str]:
    if start_date > end_date:
        raise HistoricalSTError("start_date must not be later than end_date")
    source = Path(daily_input)
    if not source.is_file():
        raise HistoricalSTError(f"daily input is missing: {source}")
    parquet = pq.ParquetFile(source)
    if "date" not in parquet.schema_arrow.names:
        raise HistoricalSTError("daily input missing required column: date")
    dates = {
        _as_date_string(value)
        for batch in parquet.iter_batches(batch_size=100_000, columns=["date"])
        for value in batch.column(0).to_pylist()
        if start_date <= _as_date_string(value) <= end_date
    }
    if not dates:
        raise HistoricalSTError("daily input has no trade dates in requested date range")
    return sorted(dates)


def _tushare_stock_st_request(token: str, trade_date: str, timeout_seconds: int) -> dict[str, Any]:
    payload = json.dumps(
        {
            "api_name": "stock_st",
            "token": token,
            "params": {"trade_date": trade_date},
            "fields": ",".join(_RAW_FIELDS),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        _TUSHARE_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed HTTPS endpoint
            decoded = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HistoricalSTError(f"Tushare stock_st request failed for {trade_date}: {exc}") from exc
    if not isinstance(decoded, dict):
        raise HistoricalSTError(f"Tushare stock_st response is not an object for {trade_date}")
    return decoded


def _validate_tushare_response(response: dict[str, Any], requested_date: str) -> set[tuple[str, str]]:
    if response.get("code") != 0:
        raise HistoricalSTError(
            f"Tushare stock_st returned code={response.get('code')!r} for {requested_date}: {response.get('msg')!r}"
        )
    data = response.get("data")
    if not isinstance(data, dict):
        raise HistoricalSTError(f"Tushare stock_st missing data payload for {requested_date}")
    fields, items = data.get("fields"), data.get("items")
    if not isinstance(fields, list) or not isinstance(items, list):
        raise HistoricalSTError(f"Tushare stock_st malformed rows for {requested_date}")
    missing = {"ts_code", "trade_date"} - set(fields)
    if missing:
        raise HistoricalSTError(f"Tushare stock_st missing fields for {requested_date}: {sorted(missing)}")
    positions = {str(name): index for index, name in enumerate(fields)}
    memberships: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            raise HistoricalSTError(f"Tushare stock_st malformed item for {requested_date}")
        trade_date = str(item[positions["trade_date"]])
        code = str(item[positions["ts_code"]]).upper()
        if trade_date != requested_date:
            raise HistoricalSTError(f"Tushare stock_st date mismatch: requested {requested_date}, received {trade_date}")
        if not _CODE_RE.fullmatch(code):
            raise HistoricalSTError(f"Tushare stock_st invalid code: {code!r}")
        key = (trade_date, code)
        if key in memberships:
            raise HistoricalSTError(f"Tushare stock_st duplicate membership: {key}")
        memberships.add(key)
    return memberships


def _read_tushare_memberships(raw_input: Path, requested_dates: Iterable[str]) -> set[tuple[str, str]]:
    expected = set(requested_dates)
    seen_dates: set[str] = set()
    memberships: set[tuple[str, str]] = set()
    with raw_input.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise HistoricalSTError(f"invalid raw ST JSON on line {line_number}") from exc
            if record.get("contract_id") != _RAW_CONTRACT or record.get("api_name") != "stock_st":
                raise HistoricalSTError(f"unexpected raw ST contract on line {line_number}")
            trade_date = str(record.get("queried_trade_date", ""))
            if trade_date not in expected:
                raise HistoricalSTError(f"raw ST date outside requested input scope: {trade_date}")
            if trade_date in seen_dates:
                raise HistoricalSTError(f"duplicate raw ST response date: {trade_date}")
            seen_dates.add(trade_date)
            response = record.get("response")
            if not isinstance(response, dict):
                raise HistoricalSTError(f"raw ST response is not an object on line {line_number}")
            records = _validate_tushare_response(response, trade_date)
            overlap = memberships & records
            if overlap:
                raise HistoricalSTError(f"duplicate raw ST membership: {sorted(overlap)[0]}")
            memberships.update(records)
    missing_dates = sorted(expected - seen_dates)
    if missing_dates:
        raise HistoricalSTError(f"raw ST input missing response date: {missing_dates[0]}")
    return memberships


def _as_date_string(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.date().isoformat()


def _as_datetime(value: Any) -> datetime:
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
