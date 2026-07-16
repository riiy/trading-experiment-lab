from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from texperiment.data.normalizer import normalize_daily_bars


@dataclass(frozen=True)
class AkShareFetchReport:
    symbols_requested: int
    symbols_succeeded: int
    symbols_failed: int
    failed_symbols: dict[str, str]


def fetch_a_share_daily(
    start_date: str,
    end_date: str,
    *,
    adj_type: str = "qfq",
    pause_seconds: float = 0.2,
    max_retries: int = 3,
    api: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[pd.DataFrame, AkShareFetchReport]:
    """Fetch full-market A-share daily bars from AkShare.

    AkShare exposes history one symbol at a time, so transient failures are
    isolated and reported instead of discarding the rest of the market.
    """
    if adj_type not in {"none", "qfq", "hfq"}:
        raise ValueError(f"Unsupported adjustment type: {adj_type}")
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")

    api = api or _load_akshare()
    symbols = _load_symbol_list(api, max_retries=max_retries, sleep=sleep)
    code_col = _find_column(symbols, "股票代码", "代码", "code")
    name_col = _find_column(symbols, "股票简称", "名称", "name", required=False)
    code_index = symbols.columns.get_loc(code_col)
    name_index = symbols.columns.get_loc(name_col) if name_col else None
    adjustment = "" if adj_type == "none" else adj_type
    frames: list[pd.DataFrame] = []
    failures: dict[str, str] = {}

    for row in symbols.itertuples(index=False, name=None):
        code = str(row[code_index]).zfill(6)
        name = str(row[name_index]) if name_index is not None else ""
        try:
            raw = _fetch_with_retries(
                api,
                code,
                start_date,
                end_date,
                adjustment,
                max_retries,
                sleep,
            )
            if raw.empty:
                continue
            raw["名称"] = name
            frames.append(
                normalize_daily_bars(
                    raw,
                    provider="akshare",
                    adj_type=adj_type,
                    source="akshare",
                )
            )
        except Exception as exc:  # keep one provider failure from aborting market fetch
            failures[code] = str(exc)
        if pause_seconds > 0:
            sleep(pause_seconds)

    if not frames:
        raise RuntimeError(f"AkShare returned no usable daily bars; failures={failures}")

    data = pd.concat(frames, ignore_index=True)
    data = data.drop_duplicates(["date", "code"], keep="last")
    data = data.sort_values(["code", "date"]).reset_index(drop=True)
    report = AkShareFetchReport(
        symbols_requested=len(symbols),
        symbols_succeeded=len(frames),
        symbols_failed=len(failures),
        failed_symbols=failures,
    )
    return data, report


def _fetch_with_retries(
    api: Any,
    code: str,
    start_date: str,
    end_date: str,
    adjustment: str,
    max_retries: int,
    sleep: Callable[[float], None],
) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            return api.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust=adjustment,
            )
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max_retries:
                sleep(2**attempt)
    assert last_error is not None
    raise last_error


def _load_symbol_list(api: Any, *, max_retries: int, sleep: Callable[[float], None]) -> pd.DataFrame:
    """Load symbols, falling back when AkShare's exchange list endpoint is down."""
    errors: list[str] = []
    for method_name in ("stock_info_a_code_name", "stock_zh_a_spot_em"):
        method = getattr(api, method_name, None)
        if method is None:
            continue
        for attempt in range(max_retries):
            try:
                symbols = method()
                if not symbols.empty:
                    return symbols
                errors.append(f"{method_name}: empty response")
                break
            except Exception as exc:
                errors.append(f"{method_name}: {exc}")
                if attempt + 1 < max_retries:
                    sleep(2**attempt)
    raise RuntimeError("AkShare could not load A-share symbol list; " + " | ".join(errors))


def _load_akshare() -> Any:
    try:
        import akshare  # type: ignore
    except ImportError as exc:
        raise RuntimeError("AkShare is required; install with `uv sync --extra akshare`") from exc
    return akshare


def _find_column(df: pd.DataFrame, *names: str, required: bool = True) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    if required:
        raise ValueError(f"AkShare stock list missing one of columns: {names}")
    return None
