"""
Health probe.

Registered on both GET and POST: the specification says POST /health, which is
unusual for a probe, so GET is provided too for curl and uptime checks.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

import config
from db.database import get_connection
from engine import gemini_client, xai_client
from models.schemas import HealthResponse
from retrieval import tavily_search

router = APIRouter(tags=["health"])


def _database_ok() -> tuple[bool, str]:
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1 FROM run_logs LIMIT 1").fetchone()
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:160]


async def _probe() -> HealthResponse:
    gemini, grok, tavily, database = await asyncio.gather(
        gemini_client.health_check(),
        xai_client.health_check(),
        tavily_search.health_check(),
        asyncio.to_thread(_database_ok),
    )
    gemini_ok, gemini_detail = gemini
    grok_ok, grok_detail = grok
    tavily_ok, tavily_detail = tavily
    db_ok, db_detail = database

    # The service is usable without either external API -- that is what the
    # fallback chain is for -- so degraded is reported, not down.
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        gemini=gemini_ok,
        grok=grok_ok,
        tavily=tavily_ok,
        database=db_ok,
        company_id=config.COMPANY_ID,
        model=config.GEMINI_MODEL,
        detail={"gemini": gemini_detail, "grok": grok_detail,
                "tavily": tavily_detail, "database": db_detail},
    )


@router.get("/health", response_model=HealthResponse)
async def health_get() -> HealthResponse:
    return await _probe()


@router.post("/health", response_model=HealthResponse)
async def health_post() -> HealthResponse:
    return await _probe()
