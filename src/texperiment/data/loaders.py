from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from texperiment.data.normalizer import normalize_daily_bars

SUPPORTED_INPUT_SUFFIXES = {".csv", ".parquet"}


def read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        # utf-8-sig handles common Chinese CSV exports with BOM.
        return pd.read_csv(path, encoding="utf-8-sig")
    raise ValueError(f"Unsupported table format: {path.suffix}")


def iter_table_files(path: str | Path) -> Iterable[Path]:
    path = Path(path)
    if path.is_file():
        if path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
            raise ValueError(f"Unsupported input file: {path}")
        yield path
        return
    if not path.exists():
        raise FileNotFoundError(path)
    for file in sorted(path.rglob("*")):
        if file.is_file() and file.suffix.lower() in SUPPORTED_INPUT_SUFFIXES:
            yield file


def read_daily_bars(path: str | Path) -> pd.DataFrame:
    """Read already-standardized daily bars from parquet or csv."""
    return read_table(path)


def ingest_a_share_daily(
    input_path: str | Path,
    *,
    provider: str = "auto",
    adj_type: str = "qfq",
    source: str | None = None,
) -> pd.DataFrame:
    """Read one file or a directory of raw A-share daily bars and return canonical rows."""
    frames: list[pd.DataFrame] = []
    for file in iter_table_files(input_path):
        raw = read_table(file)
        normalized = normalize_daily_bars(
            raw,
            provider=provider,
            adj_type=adj_type,
            source=source,
            source_file=file,
        )
        frames.append(normalized)
    if not frames:
        raise ValueError(f"No supported input files found under {input_path}")
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(["date", "code"], keep="last")
    return out.sort_values(["code", "date"]).reset_index(drop=True)


def write_parquet(df: pd.DataFrame, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, index=False)
    return output
