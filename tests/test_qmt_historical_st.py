from __future__ import annotations

from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from texperiment.data.historical_st import HistoricalSTError
from texperiment.data.qmt_historical_st import write_historical_st_status_from_qmt


def test_expands_qmt_st_events_at_exact_daily_pair_grain(tmp_path):
    daily = tmp_path / "daily.parquet"
    pq.write_table(
        pa.table(
            {
                "date": pa.array(
                    [
                        datetime(2022, 7, 18),
                        datetime(2022, 7, 18),
                        datetime(2022, 7, 19),
                        datetime(2022, 7, 20),
                        datetime(2022, 7, 21),
                    ],
                    type=pa.timestamp("ns"),
                ),
                "code": ["000001.SZ", "600000.SH", "000001.SZ", "000001.SZ", "000001.SZ"],
            }
        ),
        daily,
    )
    raw = tmp_path / "SH_XXXXXX_2011_86400000.csv"
    raw.write_text(
        "000001.SZ,unused,20220718,1\n"
        "000001.SZ,unused,20220720,0\n"
        "600000.SH,unused,20190101,0\n",
        encoding="utf-8",
    )

    report = write_historical_st_status_from_qmt(
        daily,
        raw,
        tmp_path / "status.parquet",
        start_date="2022-07-18",
        end_date="2022-07-21",
        batch_size=2,
    )

    result = pq.read_table(report.output).to_pandas()
    assert list(result["historical_st_status"]) == ["TRUE", "FALSE", "TRUE", "TRUE", "FALSE"]
    assert report.rows == 5
    assert report.st_true_rows == 3
    assert report.source_event_rows == 3


def test_qmt_codes_absent_from_event_file_are_explicitly_non_st(tmp_path):
    daily = _one_row_daily(tmp_path)
    raw = tmp_path / "SH_XXXXXX_2011_86400000.csv"
    raw.write_text("600000.SH,unused,20190101,0\n", encoding="utf-8")

    report = write_historical_st_status_from_qmt(
        daily, raw, tmp_path / "status.parquet", start_date="2022-07-18", end_date="2022-07-18"
    )

    assert pq.read_table(report.output).column("historical_st_status").to_pylist() == ["FALSE"]


def test_rejects_unknown_qmt_flag(tmp_path):
    daily = _one_row_daily(tmp_path)
    raw = tmp_path / "SH_XXXXXX_2011_86400000.csv"
    raw.write_text("000001.SZ,unused,20220718,9\n", encoding="utf-8")

    with pytest.raises(HistoricalSTError, match="unknown QMT"):
        write_historical_st_status_from_qmt(
            daily, raw, tmp_path / "status.parquet", start_date="2022-07-18", end_date="2022-07-18"
        )


def test_reads_windows_qmt_gb18030_file_with_chinese_names(tmp_path):
    daily = _one_row_daily(tmp_path)
    raw = tmp_path / "SH_XXXXXX_2011_86400000.csv"
    raw.write_text("000001.SZ,平安银行,20220718,1\n", encoding="gb18030")

    report = write_historical_st_status_from_qmt(
        daily, raw, tmp_path / "status.parquet", start_date="2022-07-18", end_date="2022-07-18"
    )

    assert report.st_true_rows == 1
    assert pq.read_table(report.output).column("historical_st_status").to_pylist() == ["TRUE"]


def test_rejects_impossible_qmt_event_date(tmp_path):
    daily = _one_row_daily(tmp_path)
    raw = tmp_path / "SH_XXXXXX_2011_86400000.csv"
    raw.write_text("000001.SZ,平安银行,20220230,1\n", encoding="gb18030")

    with pytest.raises(HistoricalSTError, match="invalid QMT event date"):
        write_historical_st_status_from_qmt(
            daily, raw, tmp_path / "status.parquet", start_date="2022-07-18", end_date="2022-07-18"
        )


def _one_row_daily(root):
    daily = root / "daily.parquet"
    pq.write_table(
        pa.table(
            {
                "date": pa.array([datetime(2022, 7, 18)], type=pa.timestamp("ns")),
                "code": ["000001.SZ"],
            }
        ),
        daily,
    )
    return daily
