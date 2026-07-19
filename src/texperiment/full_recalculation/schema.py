from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from texperiment.full_recalculation.contract import (
    CONTRACT_ID,
    EXPECTED_STAGES,
    FORBIDDEN_PIPELINE_INPUTS,
    OUTPUT_SETUP_ID,
    REQUIRED_AUXILIARY_INPUTS,
    REQUIRED_MARKET_INPUTS,
    REQUIRED_POLICY_FIELDS,
    SOURCE_SETUP_ID,
    TIMEZONE,
)


class ManifestValidationError(ValueError):
    pass


def validate_manifest_v2(manifest: Mapping[str, Any]) -> None:
    """Validate the frozen public contract without reading the filesystem."""
    _require_fields(
        manifest,
        {
            "contract",
            "repository",
            "permissions",
            "strategy",
            "inputs",
            "policies",
            "forbidden_inputs",
            "expected_stages",
        },
        "manifest",
    )

    contract = _mapping(manifest["contract"], "contract")
    _require_fields(contract, {"id", "timezone"}, "contract")
    if contract["id"] != CONTRACT_ID or contract["timezone"] != TIMEZONE:
        raise ManifestValidationError("contract id or timezone does not match V2")

    repository = _mapping(manifest["repository"], "repository")
    _require_fields(repository, {"commit", "git_dirty"}, "repository")
    if not _is_sha256(repository["commit"], length=40) or repository["git_dirty"] is not False:
        raise ManifestValidationError("repository requires a clean 40-character commit")

    permissions = _mapping(manifest["permissions"], "permissions")
    _require_fields(
        permissions,
        {
            "trading_allowed",
            "account_simulation_allowed",
            "ticket_generation_allowed",
            "full_recalculation_allowed",
        },
        "permissions",
    )
    if any(permissions[name] is not False for name in (
        "trading_allowed",
        "account_simulation_allowed",
        "ticket_generation_allowed",
    )):
        raise ManifestValidationError("trading, account simulation, and tickets must remain disabled")
    if not isinstance(permissions["full_recalculation_allowed"], bool):
        raise ManifestValidationError("full_recalculation_allowed must be boolean")

    strategy = _mapping(manifest["strategy"], "strategy")
    _require_fields(strategy, {"source_setup", "output_setup", "config_sha256", "rules_changed"}, "strategy")
    if strategy["source_setup"] != SOURCE_SETUP_ID or strategy["output_setup"] != OUTPUT_SETUP_ID:
        raise ManifestValidationError("strategy setup IDs do not match V2")
    if not _is_sha256(strategy["config_sha256"]) or strategy["rules_changed"] is not False:
        raise ManifestValidationError("strategy config hash is invalid or rules_changed is not false")

    inputs = _mapping(manifest["inputs"], "inputs")
    expected_inputs = set(REQUIRED_MARKET_INPUTS) | set(REQUIRED_AUXILIARY_INPUTS)
    _require_fields(inputs, expected_inputs, "inputs")
    for name in REQUIRED_MARKET_INPUTS:
        _validate_market_input(name, _mapping(inputs[name], f"inputs.{name}"))
    for name in REQUIRED_AUXILIARY_INPUTS:
        item = _mapping(inputs[name], f"inputs.{name}")
        _require_fields(item, {"path", "sha256"}, f"inputs.{name}")
        if not str(item["path"]).strip() or not _is_sha256(item["sha256"]):
            raise ManifestValidationError(f"inputs.{name} path or hash is invalid")
    if strategy["config_sha256"] != inputs["setup_config"]["sha256"]:
        raise ManifestValidationError("strategy config hash does not match setup_config input")

    policies = _mapping(manifest["policies"], "policies")
    _require_fields(policies, set(REQUIRED_POLICY_FIELDS), "policies")
    if any(not str(policies[field]).strip() for field in REQUIRED_POLICY_FIELDS):
        raise ManifestValidationError("policy versions must be non-empty")

    if tuple(manifest["forbidden_inputs"]) != FORBIDDEN_PIPELINE_INPUTS:
        raise ManifestValidationError("forbidden_inputs does not match the frozen contract")
    if tuple(manifest["expected_stages"]) != EXPECTED_STAGES:
        raise ManifestValidationError("expected_stages does not match the frozen order")


def _validate_market_input(name: str, item: Mapping[str, Any]) -> None:
    required = {"path", "sha256", "rows", "min_date", "max_date", "codes", "adj_type", "source"}
    _require_fields(item, required, f"inputs.{name}")
    if not str(item["path"]).strip() or not _is_sha256(item["sha256"]):
        raise ManifestValidationError(f"inputs.{name} path or hash is invalid")
    if not isinstance(item["rows"], int) or item["rows"] <= 0:
        raise ManifestValidationError(f"inputs.{name}.rows must be positive")
    if not isinstance(item["codes"], int) or item["codes"] <= 0:
        raise ManifestValidationError(f"inputs.{name}.codes must be positive")
    min_date = _parse_date(item["min_date"], f"inputs.{name}.min_date")
    max_date = _parse_date(item["max_date"], f"inputs.{name}.max_date")
    if min_date > max_date:
        raise ManifestValidationError(f"inputs.{name} date range is reversed")
    if not str(item["adj_type"]).strip() or not str(item["source"]).strip():
        raise ManifestValidationError(f"inputs.{name} adjustment type and source are required")


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestValidationError(f"{field} must be a mapping")
    return value


def _require_fields(value: Mapping[str, Any], required: set[str], field: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise ManifestValidationError(f"{field} missing required fields: {missing}")


def _parse_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ManifestValidationError(f"{field} must be YYYY-MM-DD") from exc


def _is_sha256(value: Any, *, length: int = 64) -> bool:
    text = str(value)
    return len(text) == length and all(character in "0123456789abcdef" for character in text.lower())
