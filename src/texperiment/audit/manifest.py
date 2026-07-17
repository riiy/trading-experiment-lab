from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow.parquet as pq

from texperiment.audit.sampler import AUDIT_PLAN_VERSION, AUDIT_RANDOM_SEED


def sha256_file(path: str | Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_directory(path: str | Path) -> str:
    root = Path(path)
    digest = hashlib.sha256()
    for file in sorted(item for item in root.rglob("*") if item.is_file() and "__pycache__" not in item.parts):
        digest.update(file.relative_to(root).as_posix().encode())
        digest.update(bytes.fromhex(sha256_file(file)))
    return digest.hexdigest()


def profile_table(
    path: str | Path,
    *,
    key_fields: tuple[str, ...],
    critical_fields: tuple[str, ...],
    batch_size: int = 250_000,
) -> dict[str, Any]:
    source = Path(path)
    if source.suffix.lower() == ".parquet":
        parquet = pq.ParquetFile(source)
        columns = parquet.schema_arrow.names
        batches = (batch.to_pandas() for batch in parquet.iter_batches(batch_size=batch_size))
        expected_rows = parquet.metadata.num_rows
    else:
        header = pd.read_csv(source, nrows=0)
        columns = header.columns.tolist()
        batches = pd.read_csv(source, chunksize=batch_size)
        expected_rows = None

    state = _ProfileState(columns, key_fields=key_fields, critical_fields=critical_fields)
    for frame in batches:
        state.consume(frame)
    result = state.result()
    if expected_rows is not None and result["rows"] != expected_rows:
        raise ValueError(f"row count mismatch while profiling {source}")
    return result


def build_audit_manifest(
    project_root: str | Path,
    inputs: dict[str, dict[str, Any]],
    *,
    batch_size: int = 250_000,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    git_commit = _git(root, "rev-parse", "HEAD")
    git_dirty = bool(_git(root, "status", "--porcelain"))
    profiled_inputs = []
    for relative_path, spec in inputs.items():
        path = root / relative_path
        item = {
            "path": relative_path,
            "sha256": sha256_file(path),
        }
        if path.suffix.lower() in {".csv", ".parquet"}:
            item.update(profile_table(
                path,
                key_fields=tuple(spec.get("key_fields", ())),
                critical_fields=tuple(spec.get("critical_fields", ())),
                batch_size=batch_size,
            ))
        else:
            item["bytes"] = path.stat().st_size
        profiled_inputs.append(item)
    return {
        "audit_plan_version": AUDIT_PLAN_VERSION,
        "random_seed": AUDIT_RANDOM_SEED,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "timezone": "Asia/Shanghai",
        "environment": {
            "python": sys.version.split()[0],
            "operating_system": platform.platform(),
            "uv_lock_sha256": sha256_file(root / "uv.lock"),
            "config_sha256": sha256_directory(root / "configs"),
            "backtest_engine_sha256": sha256_directory(root / "src" / "texperiment" / "backtest"),
        },
        "inputs": profiled_inputs,
    }


def verify_audit_manifest(project_root: str | Path, manifest: dict[str, Any]) -> None:
    root = Path(project_root)
    changed = []
    for item in manifest.get("inputs", []):
        actual = sha256_file(root / item["path"])
        if actual != item["sha256"]:
            changed.append(item["path"])
    if changed:
        raise ValueError(f"audit inputs changed: {changed}")


def write_manifest(manifest: dict[str, Any], path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


class _ProfileState:
    def __init__(self, columns: list[str], *, key_fields: tuple[str, ...], critical_fields: tuple[str, ...]):
        self.columns = columns
        self.key_fields = key_fields
        self.critical_fields = critical_fields
        self.missing_columns = sorted((set(key_fields) | set(critical_fields)) - set(columns))
        self.missing_key_columns = sorted(set(key_fields) - set(columns))
        self.rows = 0
        self.codes: set[str] = set()
        self.sources: set[str] = set()
        self.adj_types: set[str] = set()
        self.duplicate_keys = 0
        self.missing = {field: 0 for field in self.critical_fields}
        self.min_date: pd.Timestamp | None = None
        self.max_date: pd.Timestamp | None = None
        self.previous_key: tuple[Any, ...] | None = None

    def consume(self, frame: pd.DataFrame) -> None:
        self.rows += len(frame)
        if "code" in frame:
            self.codes.update(frame["code"].dropna().astype(str))
        for column, target in (("source", self.sources), ("adj_type", self.adj_types)):
            if column in frame:
                target.update(value for value in frame[column].dropna().astype(str).str.strip() if value)
        date_column = next((name for name in ("date", "signal_date", "entry_date", "exit_date") if name in frame), None)
        if date_column:
            dates = pd.to_datetime(frame[date_column], errors="coerce").dropna()
            if not dates.empty:
                batch_min, batch_max = dates.min(), dates.max()
                self.min_date = batch_min if self.min_date is None else min(self.min_date, batch_min)
                self.max_date = batch_max if self.max_date is None else max(self.max_date, batch_max)
        for field in self.critical_fields:
            self.missing[field] += len(frame) if field not in frame else int(frame[field].isna().sum())
        if self.key_fields and not self.missing_key_columns and not frame.empty:
            keys = frame[list(self.key_fields)].astype("string")
            self.duplicate_keys += int(keys.duplicated().sum())
            first = tuple(keys.iloc[0].tolist())
            if self.previous_key == first:
                self.duplicate_keys += 1
            self.previous_key = tuple(keys.iloc[-1].tolist())

    def result(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "columns": len(self.columns),
            "min_date": None if self.min_date is None else self.min_date.date().isoformat(),
            "max_date": None if self.max_date is None else self.max_date.date().isoformat(),
            "code_count": len(self.codes),
            "source_values": sorted(self.sources),
            "adj_type_values": sorted(self.adj_types),
            "duplicate_primary_keys": self.duplicate_keys,
            "duplicate_check_method": "adjacent_sorted_stream",
            "missing_critical_fields": self.missing,
            "missing_columns": self.missing_columns,
        }


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=False, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""
