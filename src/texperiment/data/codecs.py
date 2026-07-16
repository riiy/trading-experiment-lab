from __future__ import annotations

import re

_A_SHARE_CODE_RE = re.compile(r"(?P<prefix>sh|sz|bj)?[._-]?(?P<digits>\d{1,6})(?:[._-]?(?P<suffix>SH|SZ|BJ))?", re.IGNORECASE)


def _clean_code(code: str) -> str:
    raw = str(code).strip()
    if re.fullmatch(r"\d+\.0", raw):
        raw = raw[:-2]
    return raw


def infer_a_share_market(code: str) -> str:
    """Infer exchange from an A-share code.

    Numeric CSV exports may lose leading zeroes; 1 is treated as 000001.
    Returns SH, SZ or BJ. Raises ValueError when the code cannot be inferred.
    """
    raw = _clean_code(code)
    m = _A_SHARE_CODE_RE.fullmatch(raw)
    if not m:
        raise ValueError(f"Invalid A-share code: {code!r}")

    prefix = (m.group("prefix") or "").upper()
    suffix = (m.group("suffix") or "").upper()
    digits = m.group("digits").zfill(6)

    if suffix in {"SH", "SZ", "BJ"}:
        return suffix
    if prefix in {"SH", "SZ", "BJ"}:
        return prefix

    if digits.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return "SH"
    if digits.startswith(("000", "001", "002", "003", "200", "300", "301")):
        return "SZ"
    if digits.startswith(("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "920")):
        return "BJ"

    raise ValueError(f"Cannot infer A-share market for code: {code!r}")


def normalize_a_share_code(code: str) -> str:
    """Normalize A-share stock code into 000001.SZ / 600000.SH / 833000.BJ."""
    raw = _clean_code(code)
    m = _A_SHARE_CODE_RE.fullmatch(raw)
    if not m:
        raise ValueError(f"Invalid A-share code: {code!r}")
    digits = m.group("digits").zfill(6)
    market = infer_a_share_market(raw)
    return f"{digits}.{market}"


def normalize_date_yyyymmdd(value: object) -> str:
    """Return ISO date string from common provider date formats."""
    raw = str(value).strip()
    if re.fullmatch(r"\d+\.0", raw):
        raw = raw[:-2]
    if not raw or raw.lower() in {"nan", "nat", "none"}:
        raise ValueError("empty date")
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw
