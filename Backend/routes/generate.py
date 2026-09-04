"""
POST /generate -- the main analysis endpoint.

Streams Server-Sent Events so the user sees each section the moment it is ready
rather than waiting for the whole pipeline. The order is fixed and documented in
SECTION_ORDER below, so the frontend can render progressively.

Every stage is individually guarded. A failure emits a `warning` frame and the
pipeline continues down its fallback chain; the stream always reaches
`complete` with a real run_id, because a blank response is never an acceptable
answer to a funding question.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

import config
from db import crud
from engine import (
    evidence_builder,
    explainer,
    gemini_client,
    ngo_matcher,
    ortools_optimizer,
    scoring_engine,
)
from models.schemas import GenerateRequest, SSEEvent

log = logging.getLogger("nidhi.generate")

router = APIRouter(tags=["generate"])

SECTION_ORDER = [
    "progress", "overview", "scorecard", "fund_split", "ngo_recommendations",
    "ngo_comparison", "strategic_insights", "human_review_flags", "complete",
]

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # Stops nginx and similar proxies buffering the stream into one response.
    "X-Accel-Buffering": "no",
}


def _event(**kwargs) -> str:
    return SSEEvent(**kwargs).to_sse()


def _progress(step: str, status_text: str, **extra) -> str:
    return _event(section="progress", step=step, status=status_text, **extra)


def _warning(message: str) -> str:
    log.warning("generate: %s", message)
    return _event(section="warning", content={"message": message})


async def _stream_section(prompt: str, fallback: str) -> str:
    """Collect a model-written section, falling back to deterministic prose."""
    chunks: list[str] = []
    async for chunk in gemini_client.stream_text(prompt, fallback):
        chunks.append(chunk)
    text = "".join(chunks).strip()
    return text or fallback


async def run_pipeline(
    request: GenerateRequest, company_id: str
) -> AsyncIterator[str]:
    """The whole analysis, yielding an SSE frame after every stage."""
    run_id = crud.new_run_id()
    warnings: list[str] = []

    # --- 1. Retrieval ------------------------------------------------------
    yield _progress("retrieval", "running")
    try:
        pack = await evidence_builder.build_evidence_pack(request, company_id)
    except Exception as exc:
        log.exception("evidence assembly failed")
        yield _warning(f"Evidence assembly failed: {type(exc).__name__}: {exc}")
        yield _event(section="complete", run_id=run_id,
                     content={"status": "failed", "reason": str(exc)})
        return

    warnings.extend(pack.warnings)
    yield _progress("retrieval", "done", source=pack.data_source)
    for warning in pack.warnings:
        yield _warning(warning)

    # --- 2. Candidate extraction ------------------------------------------
    yield _progress("extraction", "running")
    candidates, extraction_warnings = await gemini_client.extract_candidates(
        pack, request)
    warnings.extend(extraction_warnings)
    for warning in extraction_warnings:
        yield _warning(warning)

    if not candidates:
        yield _warning("No candidate projects could be assembled from the "
                       "available evidence.")
        yield _event(section="complete", run_id=run_id,
                     content={"status": "no_candidates"})
        return

    method = candidates[0].extraction_method
    yield _progress("extraction", "done",
                    content={"candidates": len(candidates), "method": method})

    # --- 3. Deterministic scoring -----------------------------------------
    yield _progress("scoring", "running")
    ngos_by_id = {n.ngo_id: n for n in pack.ngos}
    scores = scoring_engine.score_projects(
        candidates, ngos_by_id, request, pack.company_profile)
    yield _progress("scoring", "done", content={"scored": len(scores)})

    # --- 4. Optimisation ---------------------------------------------------
    yield _progress("optimization", "running")
    # CP-SAT is synchronous and CPU-bound. Running it inline would block the
    # event loop for its whole duration, stalling every other request and the
    # keep-alive on this one.
    outcome = await asyncio.to_thread(ortools_optimizer.optimize, scores, request)
    yield _progress("optimization", "done",
                    content={"solver": outcome.constraint_report.solver,
                             "status": outcome.constraint_report.status,
                             "selected": len(outcome.selected)})

    # --- 5. NGO matching ---------------------------------------------------
    yield _progress("ngo_matching", "running")
    funded_ids = {a.project_id for a in outcome.selected}
    funded_scores = [s for s in scores if s.project_id in funded_ids]
    recommendations = ngo_matcher.match_all(funded_scores, pack.ngos, request)
    yield _progress("ngo_matching", "done",
                    content={"projects_matched": len(recommendations)})

    # --- 6. Overview -------------------------------------------------------
    allocation_by_id = {a.project_id: a for a in outcome.selected}
    recommendation_by_id = {r.project_id: r for r in recommendations}

    yield _event(section="overview", content={
        "run_id": run_id,
        "company_id": company_id,
        "company_name": pack.company_profile.get("official_name"),
        "data_source": pack.data_source,
        "extraction_method": method,
        "currency": request.currency,
        "total_budget": request.total_csr_budget,
        "projects_requested": request.number_of_projects,
        "projects_evaluated": len(scores),
        "projects_recommended": len(outcome.selected),
        "total_allocated": outcome.total_allocated,
        "budget_utilisation": outcome.budget_utilisation,
        "evidence_summary": evidence_builder.evidence_summary(pack),
        "ngos_considered": len(pack.ngos),
        "average_confidence": round(
            sum(s.confidence for s in scores) / len(scores), 3) if scores else 0.0,
        "disclaimer": ("Decision support only. Every figure is a recommendation "
                       "for human review, not an approval."),
    })

    # --- 7. Scorecards, one frame per project ------------------------------
    # All project explanations come from a single model call rather than one
    # per project: eight separate calls is both slow and, on a free-tier key
    # capped at 20 requests a day, enough to exhaust the quota in one run.
    # The portfolio sections are started concurrently alongside it.
    items = [(score,
              allocation_by_id.get(score.project_id),
              recommendation_by_id.get(score.project_id))
             for score in scores]

    explanations_task = asyncio.create_task(
        gemini_client.explain_projects(items, request))
    insights_task = asyncio.create_task(_stream_section(
        explainer.portfolio_prompt(outcome, scores, request,
                                   pack.company_profile, pack.data_source),
        explainer.portfolio_fallback(outcome, scores, request,
                                     pack.company_profile, pack.data_source)))

    try:
        explanations, explain_warnings = await explanations_task
    except Exception as exc:
        log.exception("explanation batch failed")
        explanations = {s.project_id: explainer.project_fallback(s, a, r, request)
                        for s, a, r in items}
        explain_warnings = [f"Explanations fell back to rule-based text: "
                            f"{type(exc).__name__}"]
    warnings.extend(explain_warnings)
    for warning in explain_warnings:
        yield _warning(warning)

    for index, (score, allocation, _) in enumerate(items):
        yield _event(section="scorecard", project_index=index, content={
            **score.model_dump(),
            "selected": allocation is not None,
            "allocated_amount": allocation.allocated_amount if allocation else 0.0,
            "explanation": explanations.get(score.project_id, ""),
        })

    # --- 8. Fund split -----------------------------------------------------
    exclusion_text = explainer.exclusion_fallback(outcome.excluded, request)

    yield _event(section="fund_split", content={
        "currency": request.currency,
        "total_budget": request.total_csr_budget,
        "total_allocated": outcome.total_allocated,
        "budget_utilisation": outcome.budget_utilisation,
        "unallocated": round(request.total_csr_budget - outcome.total_allocated, 2),
        "allocations": [a.model_dump() for a in outcome.selected],
        "excluded": [e.model_dump() for e in outcome.excluded],
        "exclusion_explanation": exclusion_text,
        "constraint_report": outcome.constraint_report.model_dump(),
    })

    # --- 9. NGO recommendations and comparison -----------------------------
    yield _event(section="ngo_recommendations", content={
        "recommendations": [r.model_dump() for r in recommendations],
        "matches_per_project": config.NGO_MATCHES_PER_PROJECT,
    })
    yield _event(section="ngo_comparison",
                 content=ngo_matcher.build_comparison(recommendations))

    # --- 10. Strategic insights -------------------------------------------
    try:
        insights = await insights_task
    except Exception as exc:
        insights = explainer.portfolio_fallback(
            outcome, scores, request, pack.company_profile, pack.data_source)
        yield _warning(f"Strategic insights fell back to rule-based text: "
                       f"{type(exc).__name__}")

    yield _event(section="strategic_insights", content={
        "narrative": insights,
        "regional_distribution": _distribution(outcome, "region"),
        "sector_distribution": _distribution(outcome, "category"),
    })

    # --- 11. Human review flags -------------------------------------------
    flags = explainer.build_review_flags(
        scores, outcome, recommendations, pack.data_source, warnings)
    yield _event(section="human_review_flags", content={
        "flags": [f.model_dump() for f in flags],
        "high_severity_count": sum(1 for f in flags if f.severity == "HIGH"),
    })

    # --- 12. Audit trail, written before the stream closes -----------------
    try:
        await crud.save_run_log(
            run_id=run_id,
            company_id=company_id,
            input_parameters=request.model_dump(),
            data_source=pack.data_source,
            projects_evaluated=len(scores),
            projects_funded=len(outcome.selected),
            total_allocated=outcome.total_allocated,
            constraints_satisfied=outcome.constraint_report.satisfied,
            output_summary={
                "extraction_method": method,
                "budget_utilisation": outcome.budget_utilisation,
                "solver": outcome.constraint_report.solver,
                "relaxations": outcome.constraint_report.relaxations_applied,
                "recommended": [
                    {"project_id": a.project_id, "project_name": a.project_name,
                     "amount": a.allocated_amount, "score": a.final_score,
                     "region": a.region, "category": a.category}
                    for a in outcome.selected],
                "review_flags": len(flags),
                "warnings": warnings,
            },
        )
        saved = True
    except Exception as exc:
        log.exception("run log save failed")
        yield _warning(f"Run log could not be saved: {type(exc).__name__}: {exc}")
        saved = False

    yield _event(section="complete", run_id=run_id, content={
        "status": "ok",
        "run_id": run_id,
        "audit_logged": saved,
        "warnings": warnings,
    })


def _distribution(outcome, by: str) -> list[dict]:
    totals: dict[str, dict] = {}
    for allocation in outcome.selected:
        key = getattr(allocation, by)
        entry = totals.setdefault(key, {"name": key, "amount": 0.0,
                                        "percentage": 0.0, "projects": 0})
        entry["amount"] += allocation.allocated_amount
        entry["percentage"] += allocation.percentage_of_budget
        entry["projects"] += 1
    for entry in totals.values():
        entry["amount"] = round(entry["amount"], 2)
        entry["percentage"] = round(entry["percentage"], 2)
    return sorted(totals.values(), key=lambda e: -e["amount"])


async def _guarded(request: GenerateRequest, company_id: str) -> AsyncIterator[str]:
    """
    Wrap the pipeline so an unexpected error still closes the stream cleanly.

    A half-open SSE connection leaves the frontend spinning forever, which is a
    worse failure than an explicit error frame.
    """
    try:
        async for frame in run_pipeline(request, company_id):
            yield frame
    except asyncio.CancelledError:
        log.info("client disconnected mid-stream")
        raise
    except Exception as exc:
        log.exception("generate pipeline crashed")
        yield _warning(f"Analysis failed: {type(exc).__name__}: {exc}")
        yield _event(section="complete", content={"status": "failed"})


@router.post("/generate")
async def generate(request: GenerateRequest, company_id: str = config.COMPANY_ID):
    """
    Run the full CSR fund analysis and stream the result as SSE.

    Frames arrive as `data: {json}\n\n`, each carrying a `section` field.
    See SECTION_ORDER for the sequence.
    """
    if config.get_company(company_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No company registered with id '{company_id}'.")

    if request.min_project_funding and request.max_project_funding:
        capacity = request.max_project_funding * request.number_of_projects
        if capacity < request.min_project_funding:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The per-project funding range cannot satisfy the "
                       "requested number of projects.")

    return StreamingResponse(
        _guarded(request, company_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/generate/sections")
async def sections() -> dict:
    """The SSE contract, so the frontend can be built against it directly."""
    return {
        "order": SECTION_ORDER,
        "frame_format": "data: {json}\n\n",
        "sections": {
            "progress": "Stage updates: retrieval, extraction, scoring, "
                        "optimization, ngo_matching. Carries step and status.",
            "overview": "Run metadata, budget totals and evidence summary.",
            "scorecard": "One frame per evaluated project, with project_index, "
                         "all ten dimension scores, confidence, completeness "
                         "and a written explanation.",
            "fund_split": "Allocations, exclusions and the constraint report.",
            "ngo_recommendations": "Top NGO matches per recommended project.",
            "ngo_comparison": "Flat comparison table across all matches.",
            "strategic_insights": "Portfolio-level narrative and distributions.",
            "human_review_flags": "What a human should check before acting.",
            "warning": "Non-fatal issue; the pipeline continued via a fallback.",
            "complete": "Terminal frame, carries run_id.",
        },
    }
