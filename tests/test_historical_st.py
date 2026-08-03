from __future__ import annotations

import json
from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from texperiment.data.historical_st import (
    HistoricalSTError,
    fetch_tushare_stock_st_raw,
    write_historical_st_status_from_tushare_raw,
)


def test_builds_true_false_status_at_exact_daily_pair_grain(tmp_path):
    daily = _daily(tmp_path)
    raw = tmp_path / "stock_st.jsonl"
    _write_raw(
        raw,
        {
            "2022-07-18": [("000001.SZ", "ST甲")],
            "2022-07-19": [],
        },
    )

    report = write_historical_st_status_from_tushare_raw(
        daily,
        raw,
        tmp_path / "status.parquet",
        start_date="2022-07-18",
        end_date="2022-07-19",
        batch_size=1,
    )

    out = pq.read_table(report.output).to_pandas()
    assert list(out["historical_st_status"]) == ["TRUE", "FALSE", "FALSE"]
    assert out[["date", "code"]].to_dict("records") == [
        {"date": datetime(2022, 7, 18), "code": "000001.SZ"},
        {"date": datetime(2022, 7, 18), "code": "600000.SH"},
        {"date": datetime(2022, 7, 19), "code": "000001.SZ"},
    ]
    assert report.rows == 3
    assert report.st_true_rows == 1
    assert report.st_false_rows == 2


def test_records_membership_without_a_daily_bar_without_breaking_target_grain(tmp_path):
    daily = _daily(tmp_path)
    raw = tmp_path / "stock_st.jsonl"
    _write_raw(raw, {"2022-07-18": [("000001.SZ", "ST甲"), ("300001.SZ", "ST乙")], "2022-07-19": []})

    report = write_historical_st_status_from_tushare_raw(
        daily,
        raw,
        tmp_path / "status.parquet",
        start_date="2022-07-18",
        end_date="2022-07-19",
    )

    assert report.source_membership_rows_unrepresented_in_daily_input == 1
    assert pq.read_table(report.output).num_rows == 3


def test_fetches_one_response_per_input_trade_date_without_writing_token(tmp_path, monkeypatch):
    daily = _daily(tmp_path)
    seen_payloads: list[dict] = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode())
        seen_payloads.append(payload)
        trade_date = payload["params"]["trade_date"]
        return Response({"code": 0, "data": {"fields": ["ts_code", "trade_date"], "items": [["000001.SZ", trade_date]]}})

    monkeypatch.setenv("TEST_TUSHARE_TOKEN", "secret-token")
    monkeypatch.setattr("texperiment.data.historical_st.urllib.request.urlopen", fake_urlopen)
    raw = tmp_path / "stock_st.jsonl"
    report = fetch_tushare_stock_st_raw(
        daily,
        raw,
        start_date="2022-07-18",
        end_date="2022-07-19",
        token_env="TEST_TUSHARE_TOKEN",
    )

    assert report.requested_trade_dates == 2
    assert report.st_membership_rows == 2
    assert [payload["params"]["trade_date"] for payload in seen_payloads] == ["2022-07-18", "2022-07-19"]
    assert "secret-token" not in raw.read_text(encoding="utf-8")


def test_rejects_duplicate_daily_input_pairs(tmp_path):
    daily = tmp_path / "duplicate_daily.parquet"
    pq.write_table(
        pa.table(
            {
                "date": pa.array([datetime(2022, 7, 18), datetime(2022, 7, 18)], type=pa.timestamp("ns")),
                "code": ["000001.SZ", "000001.SZ"],
            }
        ),
        daily,
    )
    raw = tmp_path / "stock_st.jsonl"
    _write_raw(raw, {"2022-07-18": []})

    with pytest.raises(HistoricalSTError, match="duplicate daily-input"):
        write_historical_st_status_from_tushare_raw(
            daily,
            raw,
            tmp_path / "status.parquet",
            start_date="2022-07-18",
            end_date="2022-07-18",
            batch_size=1,
        )


def _daily(root):
    path = root / "daily.parquet"
    table = pa.table(
        {
            "date": pa.array(
                [datetime(2022, 7, 18), datetime(2022, 7, 18), datetime(2022, 7, 19)],
                type=pa.timestamp("ns"),
            ),
            "code": ["000001.SZ", "600000.SH", "000001.SZ"],
        }
    )
    pq.write_table(table, path)
    return path


def _write_raw(path, rows_by_date):
    with path.open("w", encoding="utf-8") as handle:
        for trade_date, members in rows_by_date.items():
            handle.write(
                json.dumps(
                    {
                        "contract_id": "TUSHARE_STOCK_ST_RAW_V1",
                        "api_name": "stock_st",
                        "queried_trade_date": trade_date,
                        "response": {
                            "code": 0,
                            "data": {
                                "fields": ["ts_code", "name", "trade_date", "type", "type_name"],
                                "items": [[code, name, trade_date, "ST", "风险警示板"] for code, name in members],
                            },
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
