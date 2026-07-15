from __future__ import annotations

import pandas as pd


def exclude_risk_flags(df: pd.DataFrame) -> pd.DataFrame:
    if "risk_flag" not in df.columns:
        return df.copy()
    return df.loc[~df["risk_flag"].astype(bool)].reset_index(drop=True)
