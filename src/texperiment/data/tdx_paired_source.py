from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from texperiment.data.normalizer import normalize_daily_bars
from texperiment.data.quality import DataQualityReport, validate_daily_bars
from texperiment.data.schema import BOOLEAN_COLUMNS, CANONICAL_DAILY_COLUMNS, NUMERIC_COLUMNS
from texperiment.data.tdx_export_source import iter_tdx_export_files

_PRICE_COLUMNS = ["open", "high", "low", "close"]
_RAW_COLUMNS = ["date", *_PRICE_COLUMNS, "volume", "amount"]
_FIT_TOLERANCE = 0.011


@dataclass(frozen=True)
class TdxPairedReport:
    files_seen: int
    files_ingested: int
    rows: int
    stock_count: int
    adjustment_unknown_rows: int
    volume_mismatch_rows: int
    amount_mismatch_rows: int
    dropped_invalid_layer_rows: int


def read_tdx_paired_export_files(
    qfq_path: str | Path,
    raw_path: str | Path,
    hfq_path: str | Path,
) -> pd.DataFrame:
    qfq_file, raw_file, hfq_file = Path(qfq_path), Path(raw_path), Path(hfq_path)
    if not (qfq_file.name == raw_file.name == hfq_file.name):
        raise ValueError("paired TDX filenames must match")
    _validate_adjustment_mode(qfq_file, "qfq")
    _validate_adjustment_mode(raw_file, "none")
    _validate_adjustment_mode(hfq_file, "hfq")
    qfq = _read_price_file(qfq_file, "adj")
    raw = _read_price_file(raw_file, "raw")
    hfq = _read_price_file(hfq_file, "hfq")
    if qfq.empty or raw.empty or hfq.empty:
        return pd.DataFrame()

    if set(raw["date"]) != set(qfq["date"]) or set(raw["date"]) != set(hfq["date"]):
        raise ValueError(f"paired TDX source dates differ: {raw_file.name}")
    frame = raw.merge(qfq, on="date", how="inner", validate="one_to_one").merge(
        hfq, on="date", how="inner", validate="one_to_one"
    ).sort_values("date").reset_index(drop=True)
    if frame.empty:
        raise ValueError(f"paired TDX exports have no common valid dates: {raw_file.name}")

    frame["raw_pre_close"] = frame["raw_close"].shift(1)
    frame["listing_trading_day"] = np.arange(1, len(frame) + 1)
    valid_prices = np.ones(len(frame), dtype=bool)
    for prefix in ("raw", "adj", "hfq"):
        valid_prices &= (frame[[f"{prefix}_{field}" for field in _PRICE_COLUMNS]] > 0).all(axis=1).to_numpy()
    dropped_invalid_layer_rows = int((~valid_prices).sum())
    frame = frame.loc[valid_prices].copy().reset_index(drop=True)

    frame = refit_affine_adjustment_fields(frame)

    market, raw_code = raw_file.stem.split("#", maxsplit=1)
    frame["code"] = f"{raw_code}.{market.upper()}"
    frame["name"] = _read_name(raw_file)
    frame["open"] = frame["adj_open"]
    frame["high"] = frame["adj_high"]
    frame["low"] = frame["adj_low"]
    frame["close"] = frame["adj_close"]
    frame["pre_close"] = frame["adj_close"].shift(1)
    frame["pct_chg"] = (frame["adj_close"] / frame["pre_close"] - 1.0) * 100.0
    frame["volume"] = frame["raw_volume"]
    frame["amount"] = frame["raw_amount"]
    frame["volume_layer_match"] = frame["raw_volume"].eq(frame["adj_volume"]) & frame["raw_volume"].eq(frame["hfq_volume"])
    frame["amount_layer_match"] = frame["raw_amount"].eq(frame["adj_amount"]) & frame["raw_amount"].eq(frame["hfq_amount"])
    frame["trade_status"] = np.where((frame["volume"] > 0) & (frame["amount"] > 0), "1", "0")
    frame["is_suspended"] = frame["trade_status"].eq("0")
    frame["historical_st_status"] = "UNKNOWN"
    frame["limit_rule_status"] = "UNKNOWN_MISSING_HISTORICAL_ST"
    frame["limit_rule_reason"] = "TDX exports do not provide point-in-time historical ST status"
    frame["listing_date"] = frame["date"].min()
    frame["listing_date_status"] = "INFERRED_FIRST_OBSERVATION"
    frame["listing_days"] = (frame["date"] - frame["date"].min()).dt.days + 1

    result = normalize_daily_bars(
        frame,
        provider="canonical",
        adj_type="qfq",
        source="tongdaxin_export_paired",
        source_file=f"qfq={qfq_file};raw={raw_file};hfq={hfq_file}",
    )
    result.attrs["dropped_invalid_layer_rows"] = dropped_invalid_layer_rows
    return result


def write_tdx_paired_export_parquet(
    qfq_path: str | Path,
    raw_path: str | Path,
    hfq_path: str | Path,
    output_path: str | Path,
    *,
    strict: bool = True,
) -> tuple[DataQualityReport, TdxPairedReport]:
    roots = {"qfq": Path(qfq_path), "raw": Path(raw_path), "hfq": Path(hfq_path)}
    files = {key: _unique_file_map(root) for key, root in roots.items()}
    names = set(files["qfq"])
    if not names or names != set(files["raw"]) or names != set(files["hfq"]):
        raise ValueError("qfq/raw/hfq TDX file sets must be non-empty and identical")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    writer: pq.ParquetWriter | None = None
    reports: list[DataQualityReport] = []
    rows = unknown = volume_mismatch = amount_mismatch = dropped_invalid = 0
    ingested = 0
    try:
        for name in sorted(names):
            frame = read_tdx_paired_export_files(files["qfq"][name], files["raw"][name], files["hfq"][name])
            if frame.empty:
                continue
            report = validate_daily_bars(frame, strict=False)
            reports.append(report)
            dropped_invalid += int(frame.attrs.get("dropped_invalid_layer_rows", 0))
            schema = _canonical_arrow_schema()
            table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False, safe=False)
            if writer is None:
                writer = pq.ParquetWriter(temp_output, schema, compression="snappy")
            writer.write_table(table)
            rows += len(frame)
            unknown += int(frame["adjustment_status"].ne("KNOWN_AFFINE_TDX_QFQ_HFQ_VALIDATED").sum())
            volume_mismatch += int((~frame["volume_layer_match"]).sum())
            amount_mismatch += int((~frame["amount_layer_match"]).sum())
            ingested += 1
        if writer is None:
            raise ValueError("paired TDX exports contained no valid A-share rows")
        quality = _combine_quality_reports(reports)
        if strict and not quality.ok:
            raise ValueError(f"paired daily bars quality check failed: {quality}")
        writer.close()
        writer = None
        os.replace(temp_output, output)
        return quality, TdxPairedReport(
            files_seen=len(names),
            files_ingested=ingested,
            rows=rows,
            stock_count=ingested,
            adjustment_unknown_rows=unknown,
            volume_mismatch_rows=volume_mismatch,
            amount_mismatch_rows=amount_mismatch,
            dropped_invalid_layer_rows=dropped_invalid,
        )
    finally:
        if writer is not None:
            writer.close()
        if temp_output.exists():
            temp_output.unlink()


def refit_affine_adjustment_fields(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    raw_prices = out[[f"raw_{field}" for field in _PRICE_COLUMNS]].to_numpy(dtype=float)
    adjusted_prices = out[[f"adj_{field}" for field in _PRICE_COLUMNS]].to_numpy(dtype=float)
    hfq_prices = out[[f"hfq_{field}" for field in _PRICE_COLUMNS]].to_numpy(dtype=float)
    factor, offset, qfq_error, qfq_known = _fit_affine_rows(raw_prices, adjusted_prices)
    _, _, hfq_error, hfq_known = _fit_affine_rows(raw_prices, hfq_prices)
    known = qfq_known & hfq_known
    out["adj_factor"] = factor
    out["adj_offset"] = offset
    out["adjustment_fit_error"] = qfq_error
    out["hfq_fit_error"] = hfq_error
    out["adjustment_status"] = np.where(
        known,
        "KNOWN_AFFINE_TDX_QFQ_HFQ_VALIDATED",
        "UNKNOWN_AFFINE_FIT",
    )
    out.loc[~known, ["adj_factor", "adj_offset"]] = np.nan
    return out


def fit_raw_qfq_mapping(frame: pd.DataFrame) -> pd.DataFrame:
    """Fit the audited affine mapping using raw and qfq OHLC layers only."""
    out = frame.copy()
    required = {
        *(f"raw_{field}" for field in _PRICE_COLUMNS),
        *(f"adj_{field}" for field in _PRICE_COLUMNS),
    }
    missing = sorted(required - set(out.columns))
    if missing:
        raise ValueError(f"raw/qfq mapping missing columns: {missing}")
    raw_prices = out[[f"raw_{field}" for field in _PRICE_COLUMNS]].to_numpy(dtype=float)
    adjusted_prices = out[[f"adj_{field}" for field in _PRICE_COLUMNS]].to_numpy(dtype=float)
    factor, offset, error, known = _fit_affine_rows(raw_prices, adjusted_prices)
    out["adj_factor"] = factor
    out["adj_offset"] = offset
    out["adjustment_fit_error"] = error
    out["adjustment_status"] = np.where(
        known,
        "KNOWN_AFFINE_RAW_QFQ_VALIDATED",
        "UNKNOWN_AFFINE_FIT",
    )
    out.loc[~known, ["adj_factor", "adj_offset"]] = np.nan
    return out


def apply_daily_ratio_mapping(
    frame: pd.DataFrame,
    codes: set[str] | None = None,
    *,
    ratio_tolerance: float = 1e-6,
) -> pd.DataFrame:
    """Recover unknown affine rows when raw/qfq OHLC imply one stable ratio."""
    out = frame.copy()
    raw_prices = out[[f"raw_{field}" for field in _PRICE_COLUMNS]]
    adjusted_prices = out[[f"adj_{field}" for field in _PRICE_COLUMNS]]
    ratios = adjusted_prices.to_numpy(dtype=float) / raw_prices.to_numpy(dtype=float)
    finite_positive = np.isfinite(ratios).all(axis=1) & (ratios > 0).all(axis=1)
    ratio_spread = np.nanmax(ratios, axis=1) - np.nanmin(ratios, axis=1)
    ratio_scale = np.maximum(np.nanmax(np.abs(ratios), axis=1), 1.0)
    stable_ratio = finite_positive & (ratio_spread <= ratio_tolerance * ratio_scale)
    code_selected = pd.Series(True, index=out.index)
    if codes is not None:
        code_selected = out["code"].astype(str).isin(codes)
    selected = (
        code_selected
        & out["adjustment_status"].eq("UNKNOWN_AFFINE_FIT")
        & stable_ratio
    )
    factor = out.loc[selected, "adj_close"] / out.loc[selected, "raw_close"]
    out.loc[selected, "adj_factor"] = factor
    out.loc[selected, "adj_offset"] = 0.0
    out.loc[selected, "adjustment_fit_error"] = 0.0
    out.loc[selected, "adjustment_status"] = "DAILY_RATIO_FALLBACK"
    return out


def _read_price_file(path: Path, prefix: str) -> pd.DataFrame:
    try:
        frame = pd.read_csv(
            path,
            encoding="gb18030",
            skiprows=2,
            header=None,
            names=_RAW_COLUMNS,
            usecols=range(7),
            comment="#",
        )
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in _RAW_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    duplicate_dates = int(frame.duplicated("date", keep=False).sum())
    frame = frame.drop_duplicates("date", keep="last")
    result = frame.rename(columns={column: f"{prefix}_{column}" for column in _RAW_COLUMNS[1:]})
    result.attrs["duplicate_source_rows"] = duplicate_dates
    return result


def _fit_affine_rows(raw: np.ndarray, adjusted: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    raw_mean = raw.mean(axis=1)
    adjusted_mean = adjusted.mean(axis=1)
    centered = raw - raw_mean[:, None]
    variance = np.square(centered).sum(axis=1)
    covariance = (centered * (adjusted - adjusted_mean[:, None])).sum(axis=1)
    factor = np.divide(covariance, variance, out=np.full(len(raw), np.nan), where=variance > 1e-12)
    offset = adjusted_mean - factor * raw_mean
    error = np.max(np.abs(adjusted - (raw * factor[:, None] + offset[:, None])), axis=1)
    known = np.isfinite(factor) & (factor > 0) & np.isfinite(error) & (error <= _FIT_TOLERANCE)

    return factor, offset, error, known


def _read_name(path: Path) -> str:
    with path.open("r", encoding="gb18030", errors="replace") as handle:
        parts = handle.readline().strip().split()
    return parts[1] if len(parts) > 1 else ""


def _validate_adjustment_mode(path: Path, expected: str) -> None:
    with path.open("r", encoding="gb18030", errors="replace") as handle:
        header = handle.readline()
    actual = "qfq" if "前复权" in header else "hfq" if "后复权" in header else "none"
    if actual != expected:
        raise ValueError(f"TDX adjustment mode mismatch for {path}: expected {expected}, got {actual}")


def _unique_file_map(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in iter_tdx_export_files(root):
        if path.name in result:
            raise ValueError(f"duplicate TDX ticker filename under {root}: {path.name}")
        result[path.name] = path
    return result


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


def _combine_quality_reports(reports: list[DataQualityReport]) -> DataQualityReport:
    if not reports:
        return DataQualityReport(0, 0, None, None, 0, 0, 0, 0, 0)
    return DataQualityReport(
        rows=sum(report.rows for report in reports),
        code_count=sum(report.code_count for report in reports),
        min_date=min(report.min_date for report in reports if report.min_date is not None),
        max_date=max(report.max_date for report in reports if report.max_date is not None),
        duplicate_bars=sum(report.duplicate_bars for report in reports),
        null_required_cells=sum(report.null_required_cells for report in reports),
        non_positive_price_rows=sum(report.non_positive_price_rows for report in reports),
        negative_volume_rows=sum(report.negative_volume_rows for report in reports),
        negative_amount_rows=sum(report.negative_amount_rows for report in reports),
    )
