from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from texperiment.tickets.template import TRADE_TICKET_TEMPLATE
from texperiment.tickets.validator import TicketValidationConfig, validate_account_sim_row_for_ticket, validate_ticket_payload

TICKET_INDEX_COLUMNS = [
    "ticket_id",
    "trade_id",
    "simulation_id",
    "setup_id",
    "code",
    "name",
    "entry_date",
    "shares",
    "planned_loss",
    "capital_used",
    "status",
    "invalid_reason",
    "ticket_path",
]


def generate_ticket(**kwargs: Any) -> str:
    """Backward-compatible direct ticket rendering used by early tests."""
    payload = dict(kwargs)
    if "ticket_id" not in payload:
        payload["ticket_id"] = f"TICKET-{payload.get('setup_id', 'SETUP')}-{payload.get('code', 'UNKNOWN')}"
    payload.setdefault("trade_id", "-")
    payload.setdefault("simulation_id", "-")
    payload.setdefault("name", "")
    payload.setdefault("entry_date", "-")
    payload.setdefault("exit_date", "-")
    payload.setdefault("exit_reason", "-")
    payload.setdefault("per_share_risk", float(payload.get("entry_price", 0)) - float(payload.get("stop_price", 0)))
    payload.setdefault("order_permission", "manual_review_only_no_auto_order")
    payload.setdefault("status", "accepted_trade")
    payload.setdefault("max_holding_days", 10)
    payload.setdefault("time_stop_days", 5)
    payload.setdefault("net_return", "-")
    payload.setdefault("r_multiple", "-")
    payload.setdefault("pnl", "-")
    validate_ticket_payload(payload)
    return TRADE_TICKET_TEMPLATE.format(**payload)


def build_trade_ticket_artifacts(
    account_sim: pd.DataFrame,
    *,
    account_config: dict[str, Any] | None = None,
    setup_config: dict[str, Any] | None = None,
    account_summary: dict[str, Any] | None = None,
    require_account_sim_pass: bool = True,
    selected_trade_id: str | None = None,
    selected_simulation_id: str | None = None,
) -> dict[str, Any]:
    """Generate markdown trade tickets from accepted account-simulation rows.

    The generator is intentionally gated by account simulation. It never creates broker
    orders and it never accepts rows that account simulation rejected.
    """
    if require_account_sim_pass:
        decision = (account_summary or {}).get("decision")
        if decision != "ACCOUNT_SIMULATION_PASSED":
            raise PermissionError(
                "account simulation decision is not ACCOUNT_SIMULATION_PASSED; "
                "formal tickets are blocked"
            )

    cfg = TicketValidationConfig.from_configs(account_config=account_config, setup_config=setup_config)
    rows = account_sim.copy()
    if selected_trade_id:
        rows = rows.loc[rows["trade_id"].astype(str) == str(selected_trade_id)].copy()
    if selected_simulation_id:
        rows = rows.loc[rows["simulation_id"].astype(str) == str(selected_simulation_id)].copy()

    index_rows: list[dict[str, Any]] = []
    ticket_files: dict[str, str] = {}
    setup_exit = (setup_config or {}).get("exit", {})
    render_defaults = {
        "max_holding_days": setup_exit.get("max_holding_days", 10),
        "time_stop_days": setup_exit.get("time_stop_days", 5),
    }

    for _, row in rows.iterrows():
        base = _base_index_row(row)
        try:
            payload = validate_account_sim_row_for_ticket(row, config=cfg)
            payload.update(render_defaults)
            validate_ticket_payload(payload, config=cfg)
            markdown = generate_ticket(**payload)
            filename = _ticket_filename(payload)
            if filename in ticket_files:
                raise ValueError(f"duplicate ticket filename: {filename}")
            ticket_files[filename] = markdown
            index_rows.append({**base, "ticket_id": payload["ticket_id"], "status": "ticket_generated", "invalid_reason": None, "ticket_path": filename})
        except Exception as exc:  # noqa: BLE001 - we want a full audit row, not a hard stop per row.
            index_rows.append({**base, "ticket_id": base.get("ticket_id"), "status": "ticket_rejected", "invalid_reason": str(exc), "ticket_path": None})

    index = pd.DataFrame(index_rows)
    for col in TICKET_INDEX_COLUMNS:
        if col not in index.columns:
            index[col] = pd.NA
    index = index[TICKET_INDEX_COLUMNS]
    summary = summarize_ticket_generation(index)
    return {
        "ticket_index": index,
        "ticket_files": ticket_files,
        "summary": summary,
        "report_markdown": render_ticket_generation_report(summary),
    }


def summarize_ticket_generation(index: pd.DataFrame) -> dict[str, Any]:
    if index.empty:
        return {
            "decision": "TICKET_GENERATION_FAILED",
            "rows": 0,
            "tickets_generated": 0,
            "tickets_rejected": 0,
            "status_counts": {},
            "invalid_reason_counts": {},
        }
    generated = int((index["status"] == "ticket_generated").sum())
    rejected = int(len(index) - generated)
    decision = "TICKET_GENERATION_READY_FOR_MANUAL_REVIEW" if generated > 0 and rejected == 0 else "TICKET_GENERATION_REVIEW_REQUIRED"
    if generated == 0:
        decision = "TICKET_GENERATION_FAILED"
    return {
        "decision": decision,
        "rows": int(len(index)),
        "tickets_generated": generated,
        "tickets_rejected": rejected,
        "status_counts": index["status"].value_counts(dropna=False).to_dict(),
        "invalid_reason_counts": index.loc[index["status"] != "ticket_generated", "invalid_reason"].value_counts(dropna=False).to_dict(),
        "no_auto_order": True,
    }


def write_ticket_outputs(
    artifacts: dict[str, Any],
    *,
    output_dir: str | Path,
    index_path: str | Path,
    report_path: str | Path,
    summary_path: str | Path | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    written_tickets: list[Path] = []
    for filename, content in artifacts["ticket_files"].items():
        path = output / filename
        path.write_text(content, encoding="utf-8")
        written_tickets.append(path)

    index = artifacts["ticket_index"].copy()
    if not index.empty:
        index["ticket_path"] = index["ticket_path"].map(lambda p: str(output / p) if isinstance(p, str) and p else p)
    idx_path = _write_table(index, index_path)

    rpt_path = Path(report_path)
    rpt_path.parent.mkdir(parents=True, exist_ok=True)
    rpt_path.write_text(artifacts["report_markdown"], encoding="utf-8")

    smy_path = None
    if summary_path is not None:
        smy_path = Path(summary_path)
        smy_path.parent.mkdir(parents=True, exist_ok=True)
        smy_path.write_text(json.dumps(artifacts["summary"], ensure_ascii=False, indent=2), encoding="utf-8")

    return {"tickets": written_tickets, "index": idx_path, "report": rpt_path, "summary": smy_path}


def render_ticket_generation_report(summary: dict[str, Any]) -> str:
    lines = [
        "# STOCK_RS_PULLBACK_v1 交易票生成报告",
        "",
        "## 1. 结论",
        "",
        f"Decision: **{summary.get('decision')}**",
        "",
        "交易票生成层只输出人工复核用 Markdown 文件；系统不包含、也不允许自动下单能力。",
        "",
        "## 2. 汇总",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| rows | {summary.get('rows', 0)} |",
        f"| tickets_generated | {summary.get('tickets_generated', 0)} |",
        f"| tickets_rejected | {summary.get('tickets_rejected', 0)} |",
        f"| no_auto_order | {str(summary.get('no_auto_order', True)).lower()} |",
        "",
        "## 3. 状态分布",
        "",
        "| status | count |",
        "|---|---:|",
    ]
    for key, value in summary.get("status_counts", {}).items():
        lines.append(f"| {key} | {value} |")
    if not summary.get("status_counts"):
        lines.append("| - | 0 |")

    lines += ["", "## 4. 拒绝原因", "", "| reason | count |", "|---|---:|"]
    for key, value in summary.get("invalid_reason_counts", {}).items():
        lines.append(f"| {key} | {value} |")
    if not summary.get("invalid_reason_counts"):
        lines.append("| - | 0 |")

    lines += [
        "",
        "## 5. 硬约束",
        "",
        "- 只允许 `accepted_trade` 生成交易票。",
        "- 计划亏损、资金占用、一手金额、股数手数必须再次校验。",
        "- 交易票不是下单指令，执行前必须人工复核。",
        "- 任何自动下单字段都会被校验器拒绝。",
        "",
    ]
    return "\n".join(lines)


def _base_index_row(row: pd.Series) -> dict[str, Any]:
    data = dict(row)
    return {
        "ticket_id": data.get("ticket_id"),
        "trade_id": data.get("trade_id"),
        "simulation_id": data.get("simulation_id"),
        "setup_id": data.get("setup_id"),
        "code": data.get("code"),
        "name": data.get("name"),
        "entry_date": _date_str(data.get("entry_date")),
        "shares": data.get("shares"),
        "planned_loss": data.get("planned_loss"),
        "capital_used": data.get("capital_used"),
    }


def _ticket_filename(payload: dict[str, Any]) -> str:
    date = str(payload.get("entry_date") or "no-date")
    code = str(payload.get("code") or "unknown")
    tid = str(payload.get("ticket_id") or "ticket")
    raw = f"{date}_{code}_{tid}.md"
    return "".join(ch if ch.isalnum() or ch in {".", "_", "-"} else "-" for ch in raw)


def _date_str(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d")


def _write_table(df: pd.DataFrame, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".csv":
        df.to_csv(output, index=False, encoding="utf-8-sig")
    else:
        df.to_parquet(output, index=False)
    return output
