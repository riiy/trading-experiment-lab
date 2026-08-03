from __future__ import annotations

from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from texperiment.data.csmar_historical_st import write_historical_st_status_from_csmar
from texperiment.data.historical_st import HistoricalSTError


def test_builds_exact_daily_status_from_csmar_trdsta(tmp_path):
    daily = _daily(tmp_path)
    csmar = tmp_path / "TRD_Dalyr.csv"
    csmar.write_text(
        "Stkcd,Trddt,Trdsta\n000001,2022-07-18,2\n600000,2022-07-18,1\n000001,2022-07-19,3\n",
        encoding="utf-8",
    )

    report = write_historical_st_status_from_csmar(
        daily, csmar, tmp_path / "status.parquet", start_date="2022-07-18", end_date="2022-07-19", batch_size=1
    )

    result = pq.read_table(report.output).to_pandas()
    assert list(result["historical_st_status"]) == ["TRUE", "FALSE", "TRUE"]
    assert report.rows == 3
    assert report.st_true_rows == 2
    assert report.st_false_rows == 1


def test_fails_closed_when_csmar_misses_a_daily_pair(tmp_path):
    daily = _daily(tmp_path)
    csmar = tmp_path / "TRD_Dalyr.csv"
    csmar.write_text("Stkcd,Trddt,Trdsta\n000001,2022-07-18,1\n", encoding="utf-8")

    with pytest.raises(HistoricalSTError, match="missing target"):
        write_historical_st_status_from_csmar(
            daily, csmar, tmp_path / "status.parquet", start_date="2022-07-18", end_date="2022-07-19"
        )


def test_rejects_unknown_csmar_status_code(tmp_path):
    daily = _daily(tmp_path)
    csmar = tmp_path / "TRD_Dalyr.csv"
    csmar.write_text(
        "Stkcd,Trddt,Trdsta\n000001,2022-07-18,17\n600000,2022-07-18,1\n000001,2022-07-19,1\n",
        encoding="utf-8",
    )

    with pytest.raises(HistoricalSTError, match="unknown CSMAR Trdsta"):
        write_historical_st_status_from_csmar(
            daily, csmar, tmp_path / "status.parquet", start_date="2022-07-18", end_date="2022-07-19"
        )


def _daily(root):
    path = root / "daily.parquet"
    pq.write_table(
        pa.table(
            {
                "date": pa.array(
                    [datetime(2022, 7, 18), datetime(2022, 7, 18), datetime(2022, 7, 19)], type=pa.timestamp("ns")
                ),
                "code": ["000001.SZ", "600000.SH", "000001.SZ"],
            }
        ),
        path,
    )
    return path
