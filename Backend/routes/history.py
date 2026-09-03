"""Run-log endpoints: the audit trail for every analysis NIDHI has produced."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

import config
from db import crud
from models.schemas import RunLog, RunLogDetail

router = APIRouter(tags=["history"])


@router.get("/history", response_model=list[RunLog])
async def list_history(
    company_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[RunLog]:
    return await crud.list_run_logs(
        company_id=company_id or config.COMPANY_ID, limit=limit, offset=offset)


@router.get("/history/{run_id}", response_model=RunLogDetail)
async def get_history(run_id: str) -> RunLogDetail:
    detail = await crud.get_run_log(run_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No run found with id '{run_id}'.",
        )
    return detail
