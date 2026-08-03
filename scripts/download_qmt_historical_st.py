"""Run with the Python environment bundled with a Windows QMT installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from xtquant import xtdata


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="Destination for the immutable QMT historical-ST CSV")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    manifest = output.with_suffix(output.suffix + ".manifest.json")
    existing = [path for path in (output, manifest) if path.exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite existing evidence: {existing}")

    result = xtdata.download_his_st_data()
    source = (Path(xtdata.get_data_dir()) / ".." / "data" / "SH_XXXXXX_2011_86400000.csv").resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise RuntimeError(f"QMT download did not publish the expected data file: {source}; result={result!r}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary_manifest = manifest.with_name(f".{manifest.name}.tmp")
    try:
        shutil.copy2(source, temporary)
        source_hash = _sha256(temporary)
        temporary_manifest.write_text(
            json.dumps(
                {
                    "contract_id": "QMT_HISTORICAL_ST_RAW_V1",
                    "source_api": "xtdata.download_his_st_data",
                    "source_path": str(source),
                    "output_path": str(output),
                    "sha256": source_hash,
                    "bytes": temporary.stat().st_size,
                    "download_result": repr(result),
                    "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
        temporary_manifest.replace(manifest)
    finally:
        if temporary.exists():
            temporary.unlink()
        if temporary_manifest.exists():
            temporary_manifest.unlink()
    print(json.dumps({"output": str(output), "manifest": str(manifest), "sha256": _sha256(output)}, ensure_ascii=False))
    return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
