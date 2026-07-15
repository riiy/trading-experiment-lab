from __future__ import annotations

import pandas as pd


class PandasAdapter:
    """Default lightweight data adapter."""

    def read_parquet(self, path: str) -> pd.DataFrame:
        return pd.read_parquet(path)

    def write_parquet(self, df: pd.DataFrame, path: str) -> None:
        df.to_parquet(path, index=False)
