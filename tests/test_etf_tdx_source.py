from __future__ import annotations

from pathlib import Path

from texperiment.data.etf_tdx_source import is_exchange_etf_tdx_file, write_tdx_paired_exchange_etf_parquet


def test_exchange_etf_file_classifier_uses_exchange_code_bands_not_name():
    assert is_exchange_etf_tdx_file("SH#510050.txt") is True
    assert is_exchange_etf_tdx_file("SZ#159915.txt") is True
    assert is_exchange_etf_tdx_file("SZ#160119.txt") is False  # ETF-linked LOF, not exchange ETF
    assert is_exchange_etf_tdx_file("SH#600000.txt") is False


def test_paired_etf_writer_can_limit_to_one_exchange(tmp_path: Path):
    _write_etf_layers(tmp_path)
    for layer in ("qfq", "raw", "hfq"):
        (tmp_path / layer / "SZ#159915.txt").write_text(
            (tmp_path / layer / "SH#510050.txt").read_text(encoding="gb18030").replace("510050", "159915"),
            encoding="gb18030",
        )

    _, report = write_tdx_paired_exchange_etf_parquet(
        tmp_path / "qfq", tmp_path / "raw", tmp_path / "hfq", tmp_path / "out.parquet", market="SZ"
    )

    assert report.files_seen == 1


def test_paired_etf_writer_can_limit_to_ticker_prefix(tmp_path: Path):
    _write_etf_layers(tmp_path)
    _, report = write_tdx_paired_exchange_etf_parquet(
        tmp_path / "qfq", tmp_path / "raw", tmp_path / "hfq", tmp_path / "out.parquet", code_prefixes=("510",)
    )
    assert report.files_seen == 1


def test_paired_etf_writer_marks_st_not_applicable_and_limit_rules_pending(tmp_path: Path):
    _write_etf_layers(tmp_path)
    output = tmp_path / "processed" / "etf.parquet"

    quality, report = write_tdx_paired_exchange_etf_parquet(
        tmp_path / "qfq", tmp_path / "raw", tmp_path / "hfq", output
    )

    assert quality.ok is True
    assert report.files_ingested == 1
    out = __import__("pandas").read_parquet(output)
    assert out["code"].unique().tolist() == ["510050.SH"]
    assert out["historical_st_status"].eq("NOT_APPLICABLE_ETF").all()
    assert out["limit_rule_status"].eq("ETF_LIMIT_RULE_PENDING").all()


def _write_etf_layers(root: Path) -> None:
    rows = [
        "2026-01-02,1.00,1.10,0.90,1.05,1000,10000.00",
        "2026-01-03,1.05,1.20,1.00,1.15,1200,13000.00",
    ]
    headers = {"raw": "不复权", "qfq": "前复权", "hfq": "后复权"}
    for layer, header in headers.items():
        directory = root / layer
        directory.mkdir()
        path = directory / "SH#510050.txt"
        text = f"510050 示例ETF 日线 {header}\n日期 开盘 最高 最低 收盘 成交量 成交额\n"
        path.write_text(text + "\n".join(rows) + "\n", encoding="gb18030")
