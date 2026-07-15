from __future__ import annotations

from pathlib import Path

import pandas as pd


def read_daily_bars(path: str | Path) -> pd.DataFrame:
    """Read daily bars from parquet or csv and return a normalized DataFrame."""
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        raise ValueError(f"Unsupported daily bars format: {path.suffix}")
    return df
