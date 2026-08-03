"""Atomic publication of an audited raw/qfq core-input pair."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FormalInputFreezeResult:
    final_root: Path
    manifest: Path
    manifest_data: dict[str, Any]


class FormalInputFreezeError(ValueError):
    pass


def freeze_audited_core_input_pair(
    candidate_root: str | Path,
    final_root: str | Path,
    *,
    benchmark_path: str | Path | None = None,
    benchmark_code: str = "000300.SH",
) -> FormalInputFreezeResult:
    candidate, final = Path(candidate_root), Path(final_root)
    if final.exists():
        raise FileExistsError(f"formal input output already exists: {final}")
    audit_path = candidate / "pair_audit.json"
    raw_path, qfq_path = candidate / "raw_daily.parquet", candidate / "qfq_daily.parquet"
    if not all(path.is_file() for path in (audit_path, raw_path, qfq_path)):
        raise FormalInputFreezeError("candidate is missing a required pair artifact")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    _validate_candidate(audit, raw_path, qfq_path)
    benchmark = Path(benchmark_path) if benchmark_path is not None else None
    if benchmark is not None and not benchmark.is_file():
        raise FormalInputFreezeError("benchmark input is missing")

    tmp = final.parent / f".{final.name}.{os.getpid()}.tmp"
    if tmp.exists():
        raise FileExistsError(f"formal input temporary output already exists: {tmp}")
    tmp.mkdir(parents=True)
    published = False
    try:
        raw_target, qfq_target = tmp / raw_path.name, tmp / qfq_path.name
        os.link(raw_path, raw_target)
        os.link(qfq_path, qfq_target)
        outputs: dict[str, Any] = {
            "raw_daily": {"path": raw_target.name, "sha256": _sha256(raw_target), "adj_type": "none"},
            "qfq_daily": {"path": qfq_target.name, "sha256": _sha256(qfq_target), "adj_type": "qfq"},
        }
        if benchmark is not None:
            benchmark_target = tmp / "benchmark_daily.parquet"
            os.link(benchmark, benchmark_target)
            outputs["benchmark"] = {
                "path": benchmark_target.name,
                "sha256": _sha256(benchmark_target),
                "code": benchmark_code,
                "return_basis": "price_index",
            }
        manifest = {
            "contract_id": "FORMAL_CORE_INPUT_FREEZE_V1",
            "source_candidate": str(candidate),
            "source_pair_audit_sha256": _sha256(audit_path),
            "outputs": outputs,
            "pair_validation": audit["pair_validation"],
            "mapping_validation": audit["mapping_validation"],
            "scope": audit["scope"],
            "permissions": {
                "formal_recalculation_run_authorized": False,
                "trading_allowed": False,
                "account_simulation_allowed": False,
                "ticket_generation_allowed": False,
            },
            "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        manifest_path = tmp / "formal_input_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _fsync_path(raw_target)
        _fsync_path(qfq_target)
        if benchmark is not None:
            _fsync_path(tmp / "benchmark_daily.parquet")
        _fsync_path(manifest_path)
        _fsync_dir(tmp)
        os.replace(tmp, final)
        published = True
        _fsync_dir(final.parent)
        _seal_read_only(final)
        _fsync_dir(final.parent)
        return FormalInputFreezeResult(final, final / manifest_path.name, manifest)
    except Exception:
        if published and final.exists():
            shutil.rmtree(final)
        raise
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)


def _validate_candidate(audit: dict[str, Any], raw_path: Path, qfq_path: Path) -> None:
    if audit.get("decision") != "CORE_INPUT_PAIR_CANDIDATE_ACCEPTED":
        raise FormalInputFreezeError("candidate pair audit is not accepted")
    if audit.get("mapping_validation", {}).get("unevaluable_rows") != 0:
        raise FormalInputFreezeError("candidate has unevaluable raw/qfq mappings")
    if audit.get("outputs", {}).get("raw_daily", {}).get("sha256") != _sha256(raw_path):
        raise FormalInputFreezeError("raw candidate hash differs from pair audit")
    if audit.get("outputs", {}).get("qfq_daily", {}).get("sha256") != _sha256(qfq_path):
        raise FormalInputFreezeError("qfq candidate hash differs from pair audit")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_path(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _seal_read_only(root: Path) -> None:
    for path in root.iterdir():
        path.chmod(0o444)
    root.chmod(0o555)
