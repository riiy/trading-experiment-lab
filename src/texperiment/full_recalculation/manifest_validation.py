from __future__ import annotations

import json
import subprocess
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
    if not (
        repository.get("commit")
        == repository.get("head_commit")
        == repository.get("runtime_head_commit")
        == repository.get("freeze_head_commit")
        and _sha1(repository.get("freeze_head_commit"))
    ):
        raise ValueError("runtime repository commit alias mismatch")
    head, dirty = repository_state(root)
    if require_clean_repository and dirty:
        raise ValueError("Git worktree must be clean")
    if not _is_ancestor(root, str(repository["freeze_head_commit"]), head):
        raise ValueError("runtime HEAD is not a descendant of the Manifest freeze HEAD")

    audited = _mapping(manifest.get("audited_engine"), "audited_engine")
    if audited.get("implementation_commit") != AUDITED_ENGINE_COMMIT:
        raise ValueError("audited engine implementation identity mismatch")
    if audited.get("audit_record_commit") != ENGINE_AUDIT_RECORD_COMMIT:
        raise ValueError("audited engine audit identity mismatch")
    if dict(_mapping(audited.get("files"), "audited_engine.files")) != current_engine_hashes(root):
        raise ValueError("RECALCULATION_ABORTED_AUDITED_ENGINE_DRIFT")
    tool = _mapping(manifest.get("audited_manifest_tool"), "audited_manifest_tool")
    if tool.get("implementation_commit") != repository.get("manifest_tool_commit"):
        raise ValueError("Manifest tool commit binding mismatch")
    if not _sha1(tool.get("audit_record_commit")):
        raise ValueError("Manifest tool audit record binding is invalid")
    if dict(_mapping(tool.get("files"), "audited_manifest_tool.files")) != current_manifest_tool_hashes(root):
        raise ValueError("RECALCULATION_ABORTED_MANIFEST_TOOL_DRIFT")
    _validate_authorization_and_capabilities(manifest)
    _validate_comparison_boundary(_mapping(manifest.get("comparison_inputs"), "comparison_inputs"))
    _validate_publication(
        root,
        _mapping(manifest.get("publication"), "publication"),
        _mapping(manifest.get("outputs"), "outputs"),
    )

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
            and task.get("manifest_tool_audit_record_commit") == tool.get("audit_record_commit")
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


def _validate_authorization_and_capabilities(manifest: Mapping[str, Any]) -> None:
    authorization = _mapping(manifest.get("authorization_snapshot"), "authorization_snapshot")
    if authorization.get("manifest_freeze_authorized") is not True:
        raise ValueError("Manifest freeze authorization must be recorded")
    for name in (
        "formal_recalculation_run_authorized",
        "account_simulation_allowed",
        "ticket_generation_allowed",
        "trading_allowed",
    ):
        if authorization.get(name) is not False:
            raise ValueError(f"frozen Manifest authorization must remain false: {name}")

    capabilities = _mapping(manifest.get("run_capabilities"), "run_capabilities")
    if capabilities.get("strategy_validation_classification_output") is not True:
        raise ValueError("validation classification capability must be explicit")
    for name in ("account_simulation_output", "ticket_generation_output", "trading_output"):
        if capabilities.get(name) is not False:
            raise ValueError(f"formal run capability must remain false: {name}")

    permissions = _mapping(manifest.get("permissions"), "permissions")
    for name in (
        "full_recalculation_allowed",
        "strategy_validation_decision_allowed",
        "account_simulation_allowed",
        "ticket_generation_allowed",
        "trading_allowed",
    ):
        if permissions.get(name) is not False:
            raise ValueError(f"legacy permission snapshot must remain false: {name}")


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


def _validate_publication(
    root: Path,
    publication: Mapping[str, Any],
    outputs: Mapping[str, Any],
) -> None:
    required_true = (
        "final_root_must_not_exist",
        "atomic_rename_required",
        "fsync_required",
        "read_only_after_publication",
        "completion_record_required",
        "artifact_hash_chain_required",
    )
    if any(publication.get(name) is not True for name in required_true):
        raise ValueError("formal output atomic publication contract is incomplete")
    if outputs.get("atomic_publication_required") is not True:
        raise ValueError("runner publication compatibility contract is incomplete")
    for name in ("temporary_root", "final_root"):
        if outputs.get(name) != publication.get(name):
            raise ValueError("publication and runner output paths differ")
    final_root = root / str(publication.get("final_root", ""))
    temporary_root = root / str(publication.get("temporary_root", ""))
    if final_root.exists() or temporary_root.exists():
        raise FileExistsError("formal or temporary output root already exists")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _sha256(value: Any) -> bool:
    text = str(value)
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text.lower())




def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=root,
        check=False,
        capture_output=True,
    ).returncode == 0


def _sha1(value: Any) -> bool:
    text = str(value)
    return len(text) == 40 and all(character in "0123456789abcdef" for character in text.lower())
