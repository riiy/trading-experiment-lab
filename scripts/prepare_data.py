from __future__ import annotations

import argparse
from pathlib import Path

from texperiment.data.loaders import ingest_a_share_daily, write_parquet
from texperiment.data.quality import validate_daily_bars


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare canonical A-share daily bar parquet.")
    parser.add_argument("--input", default="data/raw/market/a_share_daily")
    parser.add_argument("--output", default="data/processed/a_share_daily.parquet")
    parser.add_argument("--provider", default="auto", choices=["auto", "canonical", "akshare", "tushare", "baostock"])
    parser.add_argument("--adj-type", default="qfq", choices=["none", "qfq", "hfq"])
    args = parser.parse_args()

    df = ingest_a_share_daily(args.input, provider=args.provider, adj_type=args.adj_type)
    report = validate_daily_bars(df)
    output = write_parquet(df, args.output)
    print(f"wrote: {Path(output).resolve()}")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
