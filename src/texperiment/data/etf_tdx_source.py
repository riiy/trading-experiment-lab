"""Point-in-time-neutral ingestion of local exchange ETF TDX exports.

The code-band classifier intentionally does not inspect the current security name:
names can change and a historical export can have an empty name.  It is an input
classifier only, not evidence that the directory contains a complete historical
ETF universe.
"""

from __future__ import annotations

import re
from pathlib import Path
from functools import partial
from typing import Iterator

from texperiment.data.quality import DataQualityReport
from texperiment.data.tdx_paired_source import TdxPairedReport, write_tdx_paired_export_parquet

_TDX_FILE_RE = re.compile(r"^(SH|SZ)#(\d{6})\.txt$", re.IGNORECASE)
_SH_ETF_PREFIXES = frozenset(
    {"510", "511", "512", "513", "515", "516", "517", "518", "520", "521", "522", "525", "526", "527", "528", "530", "551", "560", "561", "562", "563", "566", "588", "589"}
)
_SZ_ETF_PREFIXES = frozenset({"158", "159"})


def is_exchange_etf_tdx_file(path: str | Path) -> bool:
    match = _TDX_FILE_RE.fullmatch(Path(path).name)
    if match is None:
        return False
    return is_exchange_etf_code(match.group(1), match.group(2))


def is_exchange_etf_code(market: str, code: str) -> bool:
    """Classify an exchange ETF from the exchange plus six-digit trading code."""
    return (market.upper() == "SH" and code[:3] in _SH_ETF_PREFIXES) or (
        market.upper() == "SZ" and code[:3] in _SZ_ETF_PREFIXES
    )


def iter_exchange_etf_tdx_files(
    input_path: str | Path, *, market: str | None = None, code_prefixes: tuple[str, ...] | None = None
) -> Iterator[Path]:
    if market is not None and market.upper() not in {"SH", "SZ"}:
        raise ValueError("market must be SH, SZ, or None")
    if code_prefixes is not None and any(not item.isdigit() or not item for item in code_prefixes):
        raise ValueError("code prefixes must be non-empty numeric strings")
    root = Path(input_path)
    for path in sorted(root.rglob("*.txt")):
        code = path.name[3:9]
        if (
            is_exchange_etf_tdx_file(path)
            and (market is None or path.name[:2].upper() == market.upper())
            and (code_prefixes is None or code.startswith(code_prefixes))
        ):
            yield path


def write_tdx_paired_exchange_etf_parquet(
    qfq_path: str | Path,
    raw_path: str | Path,
    hfq_path: str | Path,
    output_path: str | Path,
    *,
    strict: bool = True,
    market: str | None = None,
    code_prefixes: tuple[str, ...] | None = None,
) -> tuple[DataQualityReport, TdxPairedReport]:
    """Write paired ETF bars while marking ST as not applicable, never false."""
    return write_tdx_paired_export_parquet(
        qfq_path,
        raw_path,
        hfq_path,
        output_path,
        strict=strict,
        file_iterator=partial(iter_exchange_etf_tdx_files, market=market, code_prefixes=code_prefixes),
        market_scope="EXCHANGE_ETF",
    )
