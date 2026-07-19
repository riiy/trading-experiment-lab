from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from texperiment.audit.manifest import profile_table, sha256_file
from texperiment.backtest.cost import COST_MODEL_VERSION
from texperiment.backtest.execution_model import EXECUTION_MODEL_VERSION
from texperiment.config.loader import load_yaml
from texperiment.full_recalculation.contract import (
    CONTRACT_ID,
    EXPECTED_STAGES,
    FORBIDDEN_PIPELINE_INPUTS,
    OUTPUT_SETUP_ID,
    SOURCE_SETUP_ID,
    TIMEZONE,
)
from texperiment.full_recalculation.manifest_canonicalization import attach_manifest_self_hash
from texperiment.market_rules.price_limit import PRICE_LIMIT_RULE_VERSION

AUDITED_ENGINE_COMMIT = "a68770e151238fbf1b8f0050808cc877973dfd13"
ENGINE_AUDIT_RECORD_COMMIT = "bce5ab70fadc23d17d10145e9f7796dfe53eb4e7"
AUDIT_ID = "FULL_PIPELINE_RECALCULATION_IMPLEMENTATION_AUDIT_v2_REAUDIT_2"
ENGINE_FILES = {
    "runner_sha256": "src/texperiment/full_recalculation/runner.py",
    "upstream_sha256": "src/texperiment/full_recalculation/upstream.py",
    "downstream_sha256": "src/texperiment/full_recalculation/downstream.py",
    "contract_sha256": "src/texperiment/full_recalculation/contract.py",
    "schema_sha256": "src/texperiment/full_recalculation/schema.py",
}
MANIFEST_TOOL_FILES = {
    "formal_manifest_sha256": "src/texperiment/full_recalculation/formal_manifest.py",
    "canonicalization_sha256": "src/texperiment/full_recalculation/manifest_canonicalization.py",
    "validation_sha256": "src/texperiment/full_recalculation/manifest_validation.py",
    "formal_cli_sha256": "src/texperiment/full_recalculation/formal_cli.py",
    "root_cli_sha256": "src/texperiment/cli.py",
}


@dataclass(frozen=True)
class FormalManifestSpec:
    run_id: str
    raw_daily: Path
    qfq_daily: Path
    benchmark: Path
    setup_config: Path
    cost_config: Path
    st_overrides: Path
    archive_manifest: Path
    temporary_root: Path
    final_root: Path
    manifest_tool_commit: str
    manifest_tool_audit_record_commit: str
    created_at: str | None = None


def build_formal_manifest_v2(project_root: str | Path, spec: FormalManifestSpec) -> dict[str, Any]:
    root = Path(project_root).resolve()
    head, dirty = repository_state(root)
    if dirty:
        raise ValueError("Git worktree must be clean before freezing a formal Manifest")
    if len(spec.manifest_tool_commit) != 40:
        raise ValueError("manifest_tool_commit must be a full Git commit")
    if len(spec.manifest_tool_audit_record_commit) != 40:
        raise ValueError("manifest_tool_audit_record_commit must be a full Git commit")
    _validate_run_id(spec.run_id)
    if spec.final_root.exists():
        raise FileExistsError(f"formal output already exists: {spec.final_root}")
    if spec.temporary_root.exists():
        raise FileExistsError(f"temporary output already exists: {spec.temporary_root}")

    audited_hashes = audited_engine_hashes(root)
    current_hashes = current_engine_hashes(root)
    if audited_hashes != current_hashes:
        raise ValueError("RECALCULATION_ABORTED_AUDITED_ENGINE_DRIFT")
    audited_tool_hashes = manifest_tool_hashes_at_commit(root, spec.manifest_tool_commit)
    current_tool = current_manifest_tool_hashes(root)
    if audited_tool_hashes != current_tool:
        raise ValueError("RECALCULATION_ABORTED_MANIFEST_TOOL_DRIFT")

    setup_path = _absolute(root, spec.setup_config)
    setup = load_yaml(setup_path)
    core_inputs = {
        "raw_daily": _market_input(root, spec.raw_daily, "none"),
        "qfq_daily": _market_input(root, spec.qfq_daily, "qfq"),
        "benchmark": _market_input(root, spec.benchmark, "none"),
        "setup_config": _file_input(root, spec.setup_config),
        "cost_config": _file_input(root, spec.cost_config),
        "st_overrides": _file_input(root, spec.st_overrides),
    }
    comparisons = comparison_references_from_archive(root, spec.archive_manifest)
    created_at = spec.created_at or datetime.now(ZoneInfo(TIMEZONE)).isoformat()
    thresholds = setup.get("validation_threshold", {})
    threshold_hash = _json_sha256(thresholds)
    manifest = {
        "manifest": {
            "schema": "FULL_PIPELINE_RECALCULATION_MANIFEST_V2",
            "contract_id": CONTRACT_ID,
            "run_id": spec.run_id,
            "timezone": TIMEZONE,
            "created_at": created_at,
            "run_type": "FULL_PIPELINE_RECALCULATION",
        },
        "contract": {"id": CONTRACT_ID, "timezone": TIMEZONE},
        "repository": {
            "commit": head,
            "head_commit": head,
            "runtime_head_commit": head,
            "audited_engine_commit": AUDITED_ENGINE_COMMIT,
            "engine_audit_record_commit": ENGINE_AUDIT_RECORD_COMMIT,
            "manifest_tool_commit": spec.manifest_tool_commit,
            "git_dirty": False,
        },
        "audit": {
            "decision": "IMPLEMENTATION_AUDIT_PASSED",
            "audit_id": AUDIT_ID,
            "frozen_sample_count": 50,
            "material_blockers": 0,
        },
        "audited_engine": {
            "implementation_commit": AUDITED_ENGINE_COMMIT,
            "audit_record_commit": ENGINE_AUDIT_RECORD_COMMIT,
            "files": current_hashes,
        },
        "audited_manifest_tool": {
            "implementation_commit": spec.manifest_tool_commit,
            "audit_record_commit": spec.manifest_tool_audit_record_commit,
            "files": current_tool,
        },
        "strategy": {
            "source_setup": SOURCE_SETUP_ID,
            "output_setup": OUTPUT_SETUP_ID,
            "config_sha256": core_inputs["setup_config"]["sha256"],
            "rules_changed": False,
            "validation_thresholds_sha256": threshold_hash,
        },
        "authorization_snapshot": {
            "manifest_freeze_authorized": True,
            "formal_recalculation_run_authorized": False,
            "account_simulation_allowed": False,
            "ticket_generation_allowed": False,
            "trading_allowed": False,
        },
        "run_capabilities": {
            "strategy_validation_classification_output": True,
            "account_simulation_output": False,
            "ticket_generation_output": False,
            "trading_output": False,
        },
        # Compatibility view for the audited runner's generic schema. It is an
        # authorization snapshot, not a declaration of future capabilities.
        "permissions": {
            "full_recalculation_allowed": False,
            "strategy_validation_decision_allowed": False,
            "account_simulation_allowed": False,
            "ticket_generation_allowed": False,
            "trading_allowed": False,
        },
        "inputs": core_inputs,
        "core_inputs": core_inputs,
        "comparison_inputs": {
            "source": "frozen_archive_manifest",
            "runtime_verification_stage": "DELTA_AND_DECISION",
            "allowed_consumers": ["DELTA_AND_DECISION"],
            **comparisons,
        },
        "comparison_only_inputs": {
            name: {"path": item["path"], "sha256": item["expected_sha256"]}
            for name, item in comparisons.items()
            if name.startswith("original_")
        },
        "policies": {
            "execution_model_version": EXECUTION_MODEL_VERSION,
            "price_limit_rule_version": PRICE_LIMIT_RULE_VERSION,
            "st_branch_policy_version": "HISTORICAL_ST_BRANCH_V1",
            "close_limit_carry_forward_version": "CLOSE_LIMIT_CARRY_FORWARD_V1",
            "raw_qfq_mapping_version": "AFFINE_WITH_GENERIC_DAILY_RATIO_FALLBACK_V1",
            "cost_model_version": COST_MODEL_VERSION,
        },
        "publication": {
            "temporary_root": _relative(root, spec.temporary_root),
            "final_root": _relative(root, spec.final_root),
            "final_root_must_not_exist": True,
            "atomic_rename_required": True,
            "fsync_required": True,
            "read_only_after_publication": True,
            "completion_record_required": True,
            "artifact_hash_chain_required": True,
        },
        "forbidden_inputs": list(FORBIDDEN_PIPELINE_INPUTS),
        "expected_stages": list(EXPECTED_STAGES),
        "integrity": {},
    }
    # Compatibility alias consumed by the already-audited runner adapter.
    manifest["outputs"] = dict(manifest["publication"])
    manifest["outputs"]["atomic_publication_required"] = manifest["publication"]["atomic_rename_required"]
    return attach_manifest_self_hash(manifest)


def write_formal_manifest_atomic(manifest: Mapping[str, Any], output: str | Path) -> Path:
    path = Path(output)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def comparison_references_from_archive(root: Path, archive_manifest: Path) -> dict[str, dict[str, str]]:
    archive_path = _absolute(root, archive_manifest)
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    indexed = {
        str(item.get("path")): item
        for item in archive.get("inputs", [])
        if isinstance(item, Mapping)
    }
    required = {
        "original_signals": "data/signals/STOCK_RS_PULLBACK_v1_signals.csv",
        "original_trades": "data/trades/STOCK_RS_PULLBACK_v1_backtest_trades.csv",
        "original_metrics": "data/reports/STOCK_RS_PULLBACK_v1_metrics.json",
    }
    result: dict[str, dict[str, str]] = {}
    for name, path in required.items():
        item = indexed.get(path)
        if item is None or not item.get("sha256"):
            raise ValueError(f"archive manifest missing comparison reference: {name}")
        result[name] = {"path": path, "expected_sha256": str(item["sha256"])}
    result["archive_manifest"] = {
        "path": _relative(root, archive_path),
        "expected_sha256": sha256_file(archive_path),
    }
    return result


def audited_engine_hashes(root: Path) -> dict[str, str]:
    hashes = {}
    for name, path in ENGINE_FILES.items():
        content = subprocess.check_output(["git", "show", f"{AUDITED_ENGINE_COMMIT}:{path}"], cwd=root)
        import hashlib
        hashes[name] = hashlib.sha256(content).hexdigest()
    return hashes


def current_engine_hashes(root: Path) -> dict[str, str]:
    return {name: sha256_file(root / path) for name, path in ENGINE_FILES.items()}


def manifest_tool_hashes_at_commit(root: Path, commit: str) -> dict[str, str]:
    import hashlib
    return {
        name: hashlib.sha256(
            subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=root)
        ).hexdigest()
        for name, path in MANIFEST_TOOL_FILES.items()
    }


def current_manifest_tool_hashes(root: Path) -> dict[str, str]:
    return {name: sha256_file(root / path) for name, path in MANIFEST_TOOL_FILES.items()}


def repository_state(root: Path) -> tuple[str, bool]:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip())
    return head, dirty


def _market_input(root: Path, path: Path, adjustment: str) -> dict[str, Any]:
    absolute = _absolute(root, path)
    profile = profile_table(absolute, key_fields=("date", "code"), critical_fields=("date", "code"))
    return {
        "path": _relative(root, absolute),
        "sha256": sha256_file(absolute),
        "rows": int(profile["rows"]),
        "min_date": profile["min_date"],
        "max_date": profile["max_date"],
        "codes": int(profile["code_count"]),
        "unique_codes": int(profile["code_count"]),
        "adj_type": adjustment,
        "adjustment": adjustment,
        "source": "frozen_formal_input",
    }


def _file_input(root: Path, path: Path) -> dict[str, str]:
    absolute = _absolute(root, path)
    return {"path": _relative(root, absolute), "sha256": sha256_file(absolute)}


def _absolute(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _relative(root: Path, path: Path) -> str:
    absolute = path if path.is_absolute() else root / path
    return absolute.resolve().relative_to(root).as_posix()


def _validate_run_id(run_id: str) -> None:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be one path component")


def _json_sha256(value: Any) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
