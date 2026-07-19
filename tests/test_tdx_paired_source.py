from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from texperiment.data.tdx_paired_source import (
    apply_daily_ratio_mapping,
    fit_raw_qfq_mapping,
    read_tdx_paired_export_files,
    write_tdx_paired_export_parquet,
)


def test_paired_tdx_exports_build_affine_dual_price_layer(tmp_path):
    paths = _write_layers(tmp_path)

    out = read_tdx_paired_export_files(paths["qfq"], paths["raw"], paths["hfq"])

    assert list(out["code"].unique()) == ["600000.SH"]
    assert out.loc[0, "raw_open"] == 10.0
    assert out.loc[0, "adj_open"] == 6.0
    assert out.loc[0, "hfq_open"] == 23.0
    assert out.loc[0, "adj_factor"] == pytest.approx(0.5)
    assert out.loc[0, "adj_offset"] == pytest.approx(1.0)
    assert out.loc[0, "adjustment_status"] == "KNOWN_AFFINE_TDX_QFQ_HFQ_VALIDATED"
    assert out.loc[0, "historical_st_status"] == "UNKNOWN"
    assert out.loc[0, "limit_rule_status"] == "UNKNOWN_MISSING_HISTORICAL_ST"
    assert bool(out.loc[0, "volume_layer_match"]) is True


def test_paired_tdx_writer_uses_separate_remediation_output(tmp_path):
    paths = _write_layers(tmp_path)
    output = tmp_path / "processed" / "remediation.parquet"

    quality, report = write_tdx_paired_export_parquet(
        paths["qfq"].parent,
        paths["raw"].parent,
        paths["hfq"].parent,
        output,
    )

    assert output.exists()
    assert quality.ok is True
    assert report.files_ingested == 1
    assert report.rows == 2
    assert report.adjustment_unknown_rows == 0


def test_paired_tdx_rejects_swapped_adjustment_headers(tmp_path):
    paths = _write_layers(tmp_path)
    text = paths["qfq"].read_text(encoding="gb18030").replace("前复权", "不复权")
    paths["qfq"].write_text(text, encoding="gb18030")

    with pytest.raises(ValueError, match="adjustment mode mismatch"):
        read_tdx_paired_export_files(paths["qfq"], paths["raw"], paths["hfq"])


def test_paired_tdx_reports_invalid_adjusted_date_without_corrupting_raw_preclose(tmp_path):
    paths = _write_layers(tmp_path)
    lines = paths["qfq"].read_text(encoding="gb18030").splitlines()
    lines[2] = "2026-01-02,0.00,0.00,0.00,0.00,1000,10000.00"
    paths["qfq"].write_text("\n".join(lines) + "\n", encoding="gb18030")

    out = read_tdx_paired_export_files(paths["qfq"], paths["raw"], paths["hfq"])

    assert len(out) == 1
    assert out.attrs["dropped_invalid_layer_rows"] == 1
    assert out.loc[0, "raw_pre_close"] == 10.5
    assert out.loc[0, "listing_trading_day"] == 2


def test_flat_ohlc_adjustment_remains_unknown(tmp_path):
    paths = _write_layers(tmp_path)
    replacements = {
        "raw": "2026-01-02,10.00,10.00,10.00,10.00,1000,10000.00",
        "qfq": "2026-01-02,6.00,6.00,6.00,6.00,1000,10000.00",
        "hfq": "2026-01-02,23.00,23.00,23.00,23.00,1000,10000.00",
    }
    for layer, replacement in replacements.items():
        lines = paths[layer].read_text(encoding="gb18030").splitlines()
        lines[2] = replacement
        paths[layer].write_text("\n".join(lines) + "\n", encoding="gb18030")

    out = read_tdx_paired_export_files(paths["qfq"], paths["raw"], paths["hfq"])

    assert out.loc[0, "adjustment_status"] == "UNKNOWN_AFFINE_FIT"
    assert pd.isna(out.loc[0, "adj_factor"])

    mapped = apply_daily_ratio_mapping(out, {"600000.SH"})
    assert mapped.loc[0, "adj_factor"] == pytest.approx(0.6)
    assert mapped.loc[0, "adj_offset"] == 0.0
    assert mapped.loc[0, "adjustment_status"] == "DAILY_RATIO_FALLBACK"


def test_600114_daily_ratio_fallback_remains_explicit(tmp_path):
    paths = _write_layers(tmp_path)
    frame = read_tdx_paired_export_files(paths["qfq"], paths["raw"], paths["hfq"])
    frame["code"] = "600114.SH"
    for prefix, value in (("raw", 10.0), ("adj", 6.0)):
        for field in ("open", "high", "low", "close"):
            frame.loc[0, f"{prefix}_{field}"] = value
    frame = fit_raw_qfq_mapping(frame)

    mapped = apply_daily_ratio_mapping(frame)

    assert frame.loc[0, "adjustment_status"] == "UNKNOWN_AFFINE_FIT"
    assert mapped.loc[0, "adjustment_status"] == "DAILY_RATIO_FALLBACK"


def test_daily_ratio_fallback_is_code_agnostic(tmp_path):
    paths = _write_layers(tmp_path)
    frame = read_tdx_paired_export_files(paths["qfq"], paths["raw"], paths["hfq"])
    frame["code"] = "999999.SZ"
    for field in ("open", "high", "low", "close"):
        frame[f"raw_{field}"] = [10.0, 12.0]
        frame[f"adj_{field}"] = [5.0, 6.0]
    frame = fit_raw_qfq_mapping(frame)

    mapped = apply_daily_ratio_mapping(frame)

    assert mapped["adjustment_status"].eq("DAILY_RATIO_FALLBACK").all()
    assert mapped["adj_factor"].eq(0.5).all()


def _write_layers(root: Path) -> dict[str, Path]:
    rows = {
        "raw": [
            "2026-01-02,10.00,11.00,9.00,10.50,1000,10000.00",
            "2026-01-03,10.50,12.00,10.00,11.50,1200,13000.00",
        ],
        "qfq": [
            "2026-01-02,6.00,6.50,5.50,6.25,1000,10000.00",
            "2026-01-03,6.25,7.00,6.00,6.75,1200,13000.00",
        ],
        "hfq": [
            "2026-01-02,23.00,25.00,21.00,24.00,1000,10000.00",
            "2026-01-03,24.00,27.00,23.00,26.00,1200,13000.00",
        ],
    }
    headers = {"raw": "不复权", "qfq": "前复权", "hfq": "后复权"}
    paths = {}
    for layer in rows:
        directory = root / layer
        directory.mkdir()
        path = directory / "SH#600000.txt"
        text = "600000 测试股 日线 " + headers[layer] + "\n日期 开盘 最高 最低 收盘 成交量 成交额\n"
        path.write_text(text + "\n".join(rows[layer]) + "\n", encoding="gb18030")
        paths[layer] = path
    return paths
