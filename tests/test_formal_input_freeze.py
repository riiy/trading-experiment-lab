from __future__ import annotations

import hashlib
import json

import pytest

from texperiment.data.formal_input_freeze import FormalInputFreezeError, freeze_audited_core_input_pair


def test_freeze_publishes_verified_read_only_input_pair(tmp_path):
    candidate = _candidate(tmp_path)
    result = freeze_audited_core_input_pair(candidate, tmp_path / "formal")

    assert result.final_root.exists()
    assert result.manifest.exists()
    assert result.manifest_data["permissions"]["formal_recalculation_run_authorized"] is False
    assert (result.final_root / "raw_daily.parquet").stat().st_mode & 0o222 == 0
    assert (result.final_root / "qfq_daily.parquet").stat().st_mode & 0o222 == 0
    assert result.final_root.stat().st_mode & 0o222 == 0


def test_freeze_rejects_hash_drift_without_publishing(tmp_path):
    candidate = _candidate(tmp_path)
    (candidate / "raw_daily.parquet").write_bytes(b"drift")

    with pytest.raises(FormalInputFreezeError, match="raw candidate hash"):
        freeze_audited_core_input_pair(candidate, tmp_path / "formal")

    assert not (tmp_path / "formal").exists()


def test_freeze_never_overwrites_existing_final_directory(tmp_path):
    candidate = _candidate(tmp_path)
    final = tmp_path / "formal"
    final.mkdir()

    with pytest.raises(FileExistsError, match="already exists"):
        freeze_audited_core_input_pair(candidate, final)


def _candidate(root):
    candidate = root / "candidate"
    candidate.mkdir()
    raw, qfq = candidate / "raw_daily.parquet", candidate / "qfq_daily.parquet"
    raw.write_bytes(b"raw")
    qfq.write_bytes(b"qfq")
    audit = {
        "decision": "CORE_INPUT_PAIR_CANDIDATE_ACCEPTED",
        "mapping_validation": {"unevaluable_rows": 0},
        "outputs": {
            "raw_daily": {"sha256": _sha(raw)},
            "qfq_daily": {"sha256": _sha(qfq)},
        },
        "pair_validation": {"accepted": True},
        "scope": {},
    }
    (candidate / "pair_audit.json").write_text(json.dumps(audit), encoding="utf-8")
    return candidate


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
