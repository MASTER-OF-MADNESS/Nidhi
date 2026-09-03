"""
Read/write helpers for the run-log audit trail.

Each public function has a sync core (used by tests and scripts) and an async
wrapper that runs it via asyncio.to_thread, so the SSE event loop is never
blocked on disk I/O mid-stream.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from typing import Any

from db.database import get_connection
from models.schemas import RunLog, RunLogDetail, utc_now_iso


def new_run_id() -> str:
    return str(uuid.uuid4())


def _row_to_detail(row: sqlite3.Row) -> RunLogDetail:
    return RunLogDetail(
        run_id=row["run_id"],
        timestamp=row["timestamp"],
        company_id=row["company_id"],
        data_source=row["data_source"],
        projects_evaluated=row["projects_evaluated"],
        projects_funded=row["projects_funded"],
        total_allocated=row["total_allocated"],
        constraints_satisfied=bool(row["constraints_satisfied"]),
        input_parameters=json.loads(row["input_parameters"] or "{}"),
        output_summary=json.loads(row["output_summary"] or "{}"),
    )


# --- writes ----------------------------------------------------------------

def save_run_log_sync(
    *,
    run_id: str,
    company_id: str,
    input_parameters: dict[str, Any],
    data_source: str,
    projects_evaluated: int,
    projects_funded: int,
    total_allocated: float,
    constraints_satisfied: bool,
    output_summary: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> RunLog:
    ts = timestamp or utc_now_iso()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO run_logs (
                run_id, timestamp, company_id, input_parameters, data_source,
                projects_evaluated, projects_funded, total_allocated,
                constraints_satisfied, output_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, ts, company_id,
                json.dumps(input_parameters, default=str),
                data_source, projects_evaluated, projects_funded,
                float(total_allocated), int(bool(constraints_satisfied)),
                json.dumps(output_summary or {}, default=str),
            ),
        )
    return RunLog(
        run_id=run_id, timestamp=ts, company_id=company_id,
        data_source=data_source, projects_evaluated=projects_evaluated,
        projects_funded=projects_funded, total_allocated=float(total_allocated),
        constraints_satisfied=bool(constraints_satisfied),
    )


async def save_run_log(**kwargs) -> RunLog:
    return await asyncio.to_thread(save_run_log_sync, **kwargs)


# --- reads -----------------------------------------------------------------

def list_run_logs_sync(
    company_id: str | None = None, limit: int = 50, offset: int = 0
) -> list[RunLog]:
    sql = "SELECT * FROM run_logs"
    params: list[Any] = []
    if company_id:
        sql += " WHERE company_id = ?"
        params.append(company_id)
    sql += " ORDER BY timestamp DESC, rowid DESC LIMIT ? OFFSET ?"
    params.extend([int(limit), int(offset)])

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [RunLog(**_row_to_detail(r).model_dump(include=set(RunLog.model_fields)))
            for r in rows]


async def list_run_logs(
    company_id: str | None = None, limit: int = 50, offset: int = 0
) -> list[RunLog]:
    return await asyncio.to_thread(list_run_logs_sync, company_id, limit, offset)


def get_run_log_sync(run_id: str) -> RunLogDetail | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM run_logs WHERE run_id = ?", (run_id,)
        ).fetchone()
    return _row_to_detail(row) if row else None


async def get_run_log(run_id: str) -> RunLogDetail | None:
    return await asyncio.to_thread(get_run_log_sync, run_id)


def count_run_logs_sync(company_id: str | None = None) -> int:
    sql = "SELECT COUNT(*) AS n FROM run_logs"
    params: list[Any] = []
    if company_id:
        sql += " WHERE company_id = ?"
        params.append(company_id)
    with get_connection() as conn:
        return int(conn.execute(sql, params).fetchone()["n"])
