from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

CANONICALIZATION_VERSION = "MANIFEST_CANONICAL_JSON_V1"


def canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    payload = json.loads(json.dumps(manifest))
    payload.setdefault("integrity", {})["manifest_self_sha256"] = None
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def manifest_self_sha256(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def attach_manifest_self_hash(manifest: Mapping[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(manifest))
    payload.setdefault("integrity", {}).update({
        "canonicalization_version": CANONICALIZATION_VERSION,
        "hash_algorithm": "SHA256",
        "manifest_self_sha256": None,
    })
    payload["integrity"]["manifest_self_sha256"] = manifest_self_sha256(payload)
    return payload


def verify_manifest_self_hash(manifest: Mapping[str, Any]) -> None:
    integrity = manifest.get("integrity", {})
    if integrity.get("canonicalization_version") != CANONICALIZATION_VERSION:
        raise ValueError("unsupported Manifest canonicalization version")
    expected = integrity.get("manifest_self_sha256")
    if not isinstance(expected, str) or expected != manifest_self_sha256(manifest):
        raise ValueError("Manifest self hash mismatch")
