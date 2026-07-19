from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from texperiment.audit.manifest import sha256_file
from texperiment.config.loader import load_yaml
from texperiment.full_recalculation.contract import CONTRACT_ID, EXPECTED_STAGES
from texperiment.full_recalculation.formal_manifest import (
    AUDITED_ENGINE_COMMIT,
    ENGINE_AUDIT_RECORD_COMMIT,
    current_engine_hashes,
    current_manifest_tool_hashes,
    repository_state,
)
from texperiment.full_recalculation.manifest_canonicalization import verify_manifest_self_hash
from texperiment.full_recalculation.schema import validate_manifest_v2

FORMAL_SCHEMA = "FULL_PIPELINE_RECALCULATION_MANIFEST_V2"


def validate_formal_manifest_v2(
    project_root: str | Path,
    manifest: Mapping[str, Any],
    *,
    require_clean_repository: bool = True,
    verify_core_files: bool = True,
    require_manifest_tool_audited: bool = True,
) -> None:
    root = Path(project_root).resolve()
    verify_manifest_self_hash(manifest)
    validate_manifest_v2(manifest)
    formal = _mapping(manifest.get("manifest"), "manifest")
    if formal.get("schema") != FORMAL_SCHEMA or formal.get("contract_id") != CONTRACT_ID:
        raise ValueError("formal Manifest schema or contract is not V2")
    if formal.get("run_type") != "FULL_PIPELINE_RECALCULATION":
        raise ValueError("SIGNAL_EXECUTION_REPLAY cannot enter the formal V2 path")
    if tuple(manifest.get("expected_stages", ())) != EXPECTED_STAGES:
        raise ValueError("formal Manifest stage order mismatch")

    repository = _mapping(manifest.get("repository"), "repository")
    if repository.get("audited_engine_commit") != AUDITED_ENGINE_COMMIT:
        raise ValueError("formal Manifest is not bound to the audited engine")
    if repository.get("engine_audit_record_commit") != ENGINE_AUDIT_RECORD_COMMIT:
        raise ValueError("formal Manifest engine audit record mismatch")
    if repository.get("commit") != repository.get("head_commit"):
        raise ValueError("runtime repository commit alias mismatch")
    head, dirty = repository_state(root)
    if require_clean_repository and dirty:
        raise ValueError("Git worktree must be clean")
    if head != repository.get("head_commit"):
        raise ValueError("Manifest HEAD does not match runtime HEAD")

    audited = _mapping(manifest.get("audited_engine"), "audited_engine")
    if dict(audited) != current_engine_hashes(root):
        raise ValueError("RECALCULATION_ABORTED_AUDITED_ENGINE_DRIFT")
    tool = _mapping(manifest.get("manifest_tool"), "manifest_tool")
    if tool.get("commit") != repository.get("manifest_tool_commit"):
        raise ValueError("Manifest tool commit binding mismatch")
    if dict(_mapping(tool.get("files"), "manifest_tool.files")) != current_manifest_tool_hashes(root):
        raise ValueError("RECALCULATION_ABORTED_MANIFEST_TOOL_DRIFT")
    _validate_permissions(_mapping(manifest.get("permissions"), "permissions"))
    _validate_comparison_boundary(_mapping(manifest.get("comparison_inputs"), "comparison_inputs"))
    _validate_outputs(root, _mapping(manifest.get("outputs"), "outputs"))

    if require_manifest_tool_audited:
        registry = load_yaml(root / "experiment_registry.yaml")
        task = registry.get("full_pipeline_recalculation_tasks", {}).get(
            "FULL_PIPELINE_RECALCULATION_MANIFEST_V2_IMPLEMENTATION", {}
        )
        if not (
            task.get("status") in {"manifest_freeze_authorized", "formal_run_authorized"}
            and task.get("manifest_v2_audited") is True
            and task.get("manifest_v2_audit_decision") == "MANIFEST_V2_AUDIT_PASSED"
            and task.get("manifest_tool_commit") == repository.get("manifest_tool_commit")
        ):
            raise PermissionError("Manifest V2 tool is not audited and authorized")

    if verify_core_files:
        for name, item in _mapping(manifest.get("inputs"), "inputs").items():
            path = root / str(item["path"])
            if not path.is_file() or sha256_file(path) != item["sha256"]:
                raise ValueError(f"core input hash drift: {name}")


def read_and_validate_formal_manifest_v2(
    project_root: str | Path,
    path: str | Path,
    **kwargs,
) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.is_absolute():
        manifest_path = Path(project_root) / manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_formal_manifest_v2(project_root, manifest, **kwargs)
    return manifest


def _validate_permissions(permissions: Mapping[str, Any]) -> None:
    if permissions.get("full_recalculation_allowed") is not True:
        raise ValueError("formal Manifest must authorize the full recalculation engine")
    if permissions.get("strategy_validation_decision_allowed") is not True:
        raise ValueError("formal Manifest must explicitly allow validation classification")
    for name in ("account_simulation_allowed", "ticket_generation_allowed", "trading_allowed"):
        if permissions.get(name) is not False:
            raise ValueError(f"formal Manifest permission must remain false: {name}")


def _validate_comparison_boundary(comparison: Mapping[str, Any]) -> None:
    if comparison.get("source") != "frozen_archive_manifest":
        raise ValueError("comparison inputs require a frozen archive manifest")
    if comparison.get("runtime_verification_stage") != "DELTA_AND_DECISION":
        raise ValueError("comparison verification must occur in Delta")
    if comparison.get("allowed_consumers") != ["DELTA_AND_DECISION"]:
        raise ValueError("Delta must be the only comparison consumer")
    for name in ("original_signals", "original_trades", "original_metrics", "archive_manifest"):
        item = _mapping(comparison.get(name), f"comparison_inputs.{name}")
        if not item.get("path") or not _sha256(item.get("expected_sha256")):
            raise ValueError(f"invalid comparison reference: {name}")


def _validate_outputs(root: Path, outputs: Mapping[str, Any]) -> None:
    required_true = (
        "final_root_must_not_exist",
        "atomic_publication_required",
        "read_only_after_publication",
    )
    if any(outputs.get(name) is not True for name in required_true):
        raise ValueError("formal output atomic publication contract is incomplete")
    final_root = root / str(outputs.get("final_root", ""))
    temporary_root = root / str(outputs.get("temporary_root", ""))
    if final_root.exists() or temporary_root.exists():
        raise FileExistsError("formal or temporary output root already exists")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _sha256(value: Any) -> bool:
    text = str(value)
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text.lower())
