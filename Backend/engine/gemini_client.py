"""
Gemini Flash: the understanding and explanation layer.

Hard boundary, stated in the specification and enforced here: Gemini extracts,
classifies and explains. It never scores. Every number a user sees comes from
scoring_engine.py and ortools_optimizer.py, which are pure deterministic code.

Every call has a rule-based fallback, so losing the model degrades the quality
of the prose -- never the availability of the analysis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator

import config
from models.schemas import CandidateProject, EvidencePack, GenerateRequest
from retrieval.text_utils import canonical_category

log = logging.getLogger("nidhi.gemini")

EXTRACTION_SYSTEM_PROMPT = """You are a CSR analyst assistant supporting a fund \
allocation decision.

Given the company profile, the CSR manager's requirements, and the retrieved \
NGO and project data, produce candidate CSR projects that could be proposed to \
this company.

Rules you must follow:
1. Produce at most {max_projects} candidate projects.
2. Every candidate must be anchored to a specific retrieved NGO. Put that NGO's \
exact name in `derived_from_ngo` and its identifier in `ngo_id`.
3. Only use regions, sectors and beneficiary groups supported by the retrieved \
evidence or explicitly requested by the manager. Do not invent organisations.
4. `estimated_budget` and `estimated_beneficiaries` are planning estimates. \
Keep each budget inside the manager's per-project funding range.
5. If a field has no supporting evidence, put its name in `uncertain_fields` \
and leave the value null. Never guess a number to fill a gap, and never use 0 \
to mean "unknown".
6. `alignment_score` is a 0-10 indication of fit with the manager's stated \
requirements. It is a classification hint only.

CRITICAL: You must NOT assign final scores, rankings, or funding amounts. A \
separate deterministic engine does all scoring and allocation. Your job is to \
extract and classify only."""

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "category": {"type": "string"},
                    "sub_category": {"type": "string"},
                    "region": {"type": "string"},
                    "country": {"type": "string"},
                    "description": {"type": "string"},
                    "estimated_beneficiaries": {"type": "integer"},
                    "estimated_budget": {"type": "number"},
                    "duration_months": {"type": "integer"},
                    "derived_from_ngo": {"type": "string"},
                    "ngo_id": {"type": "string"},
                    "beneficiary_groups": {"type": "array", "items": {"type": "string"}},
                    "sdgs": {"type": "array", "items": {"type": "integer"}},
                    "alignment_score": {"type": "number"},
                    "uncertain_fields": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["project_name", "category", "region", "derived_from_ngo"],
            },
        }
    },
    "required": ["projects"],
}


def _client():
    """Build a google-genai client, or None when no key is configured."""
    if not config.GEMINI_API_KEY:
        return None
    try:
        from google import genai
        return genai.Client(api_key=config.GEMINI_API_KEY)
    except Exception as exc:
        log.warning("Gemini client unavailable: %s", exc)
        return None


def available() -> bool:
    return _client() is not None


def _extract_json(text: str) -> dict[str, Any] | None:
    """Parse a JSON object out of a model response, tolerating code fences."""
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.S)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", candidate, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


async def _generate_gemini(prompt: str, *, json_schema: dict | None = None) -> str | None:
    """One non-streaming Gemini call, retried, None on definitive failure."""
    client = _client()
    if client is None:
        return None

    from google.genai import types

    cfg: dict[str, Any] = {
        "temperature": 0.4,
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
    }
    if json_schema is not None:
        cfg["response_mime_type"] = "application/json"
        cfg["response_schema"] = json_schema

    for attempt in range(config.GEMINI_MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model=config.GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(**cfg),
                ),
                timeout=config.GEMINI_TIMEOUT,
            )
            if (text := getattr(response, "text", None)):
                return text
            log.warning("Gemini returned an empty response (attempt %d)", attempt + 1)
        except asyncio.TimeoutError:
            log.warning("Gemini timed out after %ss (attempt %d)",
                        config.GEMINI_TIMEOUT, attempt + 1)
        except Exception as exc:
            message = str(exc)
            log.warning("Gemini call failed (attempt %d): %s: %s",
                        attempt + 1, type(exc).__name__, message[:200])
            if "RESOURCE_EXHAUSTED" in message or "429" in message:
                log.warning("Gemini quota exhausted; using the deterministic "
                            "fallback for the rest of this run.")
                return None
        if attempt < config.GEMINI_MAX_RETRIES:
            await asyncio.sleep(0.5 * (attempt + 1))
    return None


async def _generate_gemini_stream(prompt: str) -> AsyncIterator[str]:
    """Stream Gemini chunks. Yields nothing if Gemini is unavailable."""
    client = _client()
    if client is None:
        return

    from google.genai import types

    def _open():
        return client.models.generate_content_stream(
            model=config.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.5,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True),
            ),
        )

    try:
        stream = await asyncio.wait_for(
            asyncio.to_thread(_open), timeout=config.GEMINI_TIMEOUT
        )
        # The SDK's stream is a sync generator; step it in the thread pool so
        # the SSE event loop keeps serving other work between chunks.
        sentinel = object()
        while True:
            chunk = await asyncio.to_thread(next, stream, sentinel)
            if chunk is sentinel:
                break
            if (text := getattr(chunk, "text", None)):
                yield text
    except Exception as exc:
        log.warning("Gemini streaming failed: %s: %s", type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# Provider chain
#
# Gemini first, then xAI (Grok), then nothing -- at which point the caller uses
# its deterministic fallback. Each call records which provider actually
# answered so the run can tell the user where its prose came from.
# ---------------------------------------------------------------------------

def _enabled_providers() -> list[str]:
    from engine import xai_client
    checks = {"gemini": lambda: bool(config.GEMINI_API_KEY),
              "xai": xai_client.available}
    return [name for name in config.LLM_PROVIDER_ORDER
            if name in checks and checks[name]()]


async def _generate(
    prompt: str, *, json_schema: dict | None = None
) -> tuple[str | None, str | None]:
    """
    Try each configured provider in order.

    Returns (text, provider_name); (None, None) when every provider fails.
    The provider is returned rather than stored on the module: two concurrent
    /generate requests would otherwise overwrite each other's attribution and
    the audit trail would name the wrong model.
    """
    from engine import xai_client

    for provider in _enabled_providers():
        if provider == "gemini":
            text = await _generate_gemini(prompt, json_schema=json_schema)
        elif provider == "xai":
            log.info("Falling back to xAI (%s)", config.XAI_MODEL)
            text = await xai_client.generate(prompt, json_schema=json_schema)
        else:
            continue
        if text:
            return text, provider
    return None, None


async def _generate_stream(
    prompt: str, provider_out: list[str] | None = None
) -> AsyncIterator[str]:
    """
    Stream from the first provider that produces anything.

    An async generator cannot return a value, so the provider that answered is
    appended to `provider_out` when one is supplied.
    """
    from engine import xai_client

    providers = _enabled_providers()
    for index, provider in enumerate(providers):
        produced = False
        source = (_generate_gemini_stream(prompt) if provider == "gemini"
                  else xai_client.generate_stream(prompt))
        async for chunk in source:
            produced = True
            yield chunk
        if produced:
            if provider_out is not None:
                provider_out.append(provider)
            return
        if index < len(providers) - 1:
            log.info("Provider %s produced nothing; trying the next.", provider)


def provider_status() -> dict[str, bool]:
    """Which providers are configured, for the health endpoint."""
    from engine import xai_client
    return {"gemini": bool(config.GEMINI_API_KEY), "xai": xai_client.available()}


# ---------------------------------------------------------------------------
# Rule-based fallback: candidate extraction without the model
# ---------------------------------------------------------------------------

def _budget_for(request: GenerateRequest, ngo_count: int) -> float:
    """An even split across the requested project count, clamped to the range."""
    n = max(request.number_of_projects, 1)
    even = request.total_csr_budget / n
    low = request.min_project_funding or 0
    high = request.effective_max()
    return round(min(max(even, low), high), 2)


def _beneficiaries_for(budget: float, category: str, currency: str) -> int | None:
    """
    Derive a beneficiary estimate from the category's median cost benchmark.

    This is an explicit INFERENCE, not a measurement, and is surfaced as such
    in the candidate's uncertain_fields.
    """
    table = config.COST_BENCHMARKS.get(currency, config.COST_BENCHMARKS["INR"])
    median = table.get(category, table["default"])["median"]
    return int(budget / median) if median else None


def rule_based_candidates(
    pack: EvidencePack, request: GenerateRequest
) -> list[CandidateProject]:
    """
    Build candidates deterministically when Gemini is unavailable.

    Same shape as the model path: one candidate per retrieved NGO, anchored to
    that NGO, with the manager's sector and region applied.
    """
    wanted = canonical_category(request.csr_sector, request.csr_sub_sector)
    budget = _budget_for(request, len(pack.ngos))
    candidates: list[CandidateProject] = []

    for index, ngo in enumerate(pack.ngos[:config.MAX_CANDIDATE_PROJECTS]):
        category = wanted if wanted != "default" else (
            ngo.categories[0] if ngo.categories else "default"
        )
        region = (ngo.state or (ngo.geographic_coverage[0] if ngo.geographic_coverage
                                else None) or (request.states[0] if request.states
                                               else request.country))
        # Deliberately NOT ngo.known_impact: that figure is the organisation's
        # lifetime reach across all its programmes and funders (e.g. "6,50,000+
        # beneficiaries; 18+ locations"), and sometimes counts volunteers or
        # income rather than beneficiaries at all. Treating it as the reach of
        # one grant would inflate social impact and hand large NGOs an unearned
        # cost-effectiveness advantage. Estimate from the money actually being
        # allocated instead; known_impact still counts as capability evidence.
        beneficiaries = _beneficiaries_for(budget, category, request.currency)
        uncertain = ["estimated_budget", "estimated_beneficiaries"]

        # The sub-sector field can carry several selections ("Primary Education;
        # Smart Classrooms & STEM; Teacher Training"). Use the first for the
        # project's name and keep the full list for matching and scoring.
        first_sub = (request.csr_sub_sector or "").split(";")[0].strip()
        sector_label = first_sub or request.csr_sector or category
        candidates.append(CandidateProject(
            project_id=f"CAND-{index + 1:02d}",
            project_name=f"{sector_label} programme with {ngo.name} ({region})",
            category=category,
            sub_category=request.csr_sub_sector or None,
            region=region,
            country=ngo.country or request.country,
            description=(
                f"Proposed {sector_label} intervention delivered by {ngo.name} in "
                f"{region}, based on its documented focus on "
                f"{', '.join(ngo.focus_areas[:3]) or 'this sector'}."
            ),
            estimated_beneficiaries=beneficiaries,
            requested_budget=budget,
            duration_months=request.project_duration,
            derived_from_ngo=ngo.name,
            ngo_id=ngo.ngo_id,
            beneficiary_groups=(ngo.target_beneficiaries
                                or request.beneficiary_categories),
            sdgs=[],
            alignment_hint=None,
            evidence_class=config.EVIDENCE_INFERENCE,
            evidence_sources=[ngo.source] + ([ngo.url] if ngo.url else []),
            unknown_fields=uncertain + ngo.unknown_fields(),
            extraction_method="rule_based",
        ))
    return candidates


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _coerce_candidate(
    raw: dict[str, Any], index: int, pack: EvidencePack, request: GenerateRequest
) -> CandidateProject | None:
    """Validate one model-produced candidate against the retrieved evidence."""
    name = str(raw.get("project_name") or "").strip()
    anchor = str(raw.get("derived_from_ngo") or "").strip()
    if not name or not anchor:
        return None

    # The anchor NGO must actually exist in the pack; otherwise the model
    # invented an organisation and the candidate is discarded.
    by_name = {n.name.lower(): n for n in pack.ngos}
    by_id = {n.ngo_id.upper(): n for n in pack.ngos}
    ngo = (by_name.get(anchor.lower())
           or by_id.get(str(raw.get("ngo_id") or "").upper()))
    if ngo is None:
        log.warning("Discarding candidate %r: NGO %r not in evidence pack",
                    name, anchor)
        return None

    uncertain = [str(f) for f in (raw.get("uncertain_fields") or [])]

    budget = raw.get("estimated_budget")
    budget = float(budget) if isinstance(budget, (int, float)) and budget > 0 else None
    if budget is None:
        budget = _budget_for(request, len(pack.ngos))
        uncertain.append("estimated_budget")
    else:
        low, high = request.min_project_funding or 0, request.effective_max()
        budget = min(max(budget, low), high) if high else budget

    beneficiaries = raw.get("estimated_beneficiaries")
    # 0 means "the model had nothing", not "this project helps nobody".
    beneficiaries = int(beneficiaries) if isinstance(beneficiaries, int) and beneficiaries > 0 else None
    if beneficiaries is None:
        uncertain.append("estimated_beneficiaries")

    alignment = raw.get("alignment_score")
    alignment = (max(0.0, min(10.0, float(alignment)))
                 if isinstance(alignment, (int, float)) else None)

    category = canonical_category(str(raw.get("category") or ""), request.csr_sector)

    return CandidateProject(
        project_id=f"CAND-{index + 1:02d}",
        project_name=name,
        category=category,
        sub_category=str(raw.get("sub_category") or "") or None,
        region=str(raw.get("region") or ngo.state or request.country),
        country=str(raw.get("country") or ngo.country or request.country),
        description=str(raw.get("description") or ""),
        estimated_beneficiaries=beneficiaries,
        requested_budget=budget,
        duration_months=(int(raw["duration_months"])
                         if isinstance(raw.get("duration_months"), int)
                         else request.project_duration),
        derived_from_ngo=ngo.name,
        ngo_id=ngo.ngo_id,
        beneficiary_groups=[str(b) for b in (raw.get("beneficiary_groups") or [])]
                           or ngo.target_beneficiaries,
        sdgs=[int(s) for s in (raw.get("sdgs") or []) if isinstance(s, int) and 1 <= s <= 17],
        alignment_hint=alignment,
        evidence_class=config.EVIDENCE_INFERENCE,
        evidence_sources=[ngo.source] + ([ngo.url] if ngo.url else []),
        unknown_fields=sorted(set(uncertain) | set(ngo.unknown_fields())),
        extraction_method="gemini",
    )


async def extract_candidates(
    pack: EvidencePack, request: GenerateRequest
) -> tuple[list[CandidateProject], list[str]]:
    """
    Ask Gemini for candidate projects; fall back to rules on any failure.

    Returns (candidates, warnings). Never returns an empty list while the
    evidence pack holds at least one NGO.
    """
    from engine.evidence_builder import to_prompt_context

    warnings: list[str] = []
    prompt = (
        EXTRACTION_SYSTEM_PROMPT.format(max_projects=config.MAX_CANDIDATE_PROJECTS)
        + "\n\n"
        + to_prompt_context(pack)
        + "\n\nReturn JSON matching the required schema."
    )

    text, provider = await _generate(prompt, json_schema=CANDIDATE_SCHEMA)
    payload = _extract_json(text) if text else None
    raw_projects = (payload or {}).get("projects") or []

    candidates: list[CandidateProject] = []
    for index, raw in enumerate(raw_projects):
        if isinstance(raw, dict) and (c := _coerce_candidate(raw, index, pack, request)):
            # Record the provider so the user can tell Grok output from Gemini.
            c.extraction_method = provider or "gemini"
            candidates.append(c)
        if len(candidates) >= config.MAX_CANDIDATE_PROJECTS:
            break

    if not candidates:
        tried = _enabled_providers()
        names = " and ".join(
            {"gemini": f"Gemini ({config.GEMINI_MODEL})",
             "xai": f"Grok ({config.XAI_MODEL})"}.get(p, p) for p in tried
        ) or "no model provider"
        warnings.append(
            f"{names} unavailable or unusable; candidates derived by "
            f"rule-based extraction instead.")
        candidates = rule_based_candidates(pack, request)
    elif provider and provider != "gemini":
        warnings.append(
            f"Gemini was unavailable; candidates were extracted by "
            f"{provider} ({config.XAI_MODEL}) instead.")

    if candidates and len(candidates) < len(raw_projects):
        warnings.append(
            f"Discarded {len(raw_projects) - len(candidates)} model-proposed "
            f"project(s) not anchored to a retrieved NGO."
        )
    return candidates, warnings


async def stream_text(prompt: str, fallback: str,
                      provider_out: list[str] | None = None) -> AsyncIterator[str]:
    """Stream a narrative section, emitting `fallback` if every provider is silent."""
    produced = False
    async for chunk in _generate_stream(prompt, provider_out):
        produced = True
        yield chunk
    if not produced:
        yield fallback


async def health_check() -> tuple[bool, str]:
    """Cheap liveness probe for GET /health."""
    if not config.GEMINI_API_KEY:
        return False, "GEMINI_API_KEY not configured"
    text, _ = await _generate("Reply with the single word: ok")
    if text is None:
        return False, f"no response from {config.GEMINI_MODEL}"
    return True, "ok"


async def explain_projects(
    items: list, request: GenerateRequest
) -> tuple[dict[str, str], list[str]]:
    """
    One call for every project explanation.

    Returns (project_id -> text, warnings). Any project the model omits falls
    back to deterministic prose, so the caller always has text for every one.
    """
    from engine import explainer

    warnings: list[str] = []
    fallbacks = {
        score.project_id: explainer.project_fallback(
            score, allocation, recommendation, request)
        for score, allocation, recommendation in items
    }
    if not items:
        return {}, warnings

    text, provider = await _generate(
        explainer.batch_projects_prompt(items, request),
        json_schema=explainer.BATCH_SCHEMA,
    )
    payload = _extract_json(text) if text else None
    entries = (payload or {}).get("explanations") or []

    explanations: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pid, body = str(entry.get("project_id") or ""), str(entry.get("text") or "")
        if pid in fallbacks and body.strip():
            explanations[pid] = body.strip()

    missing = [pid for pid in fallbacks if pid not in explanations]
    if len(missing) == len(fallbacks):
        tried = _enabled_providers()
        names = " and ".join(
            {"gemini": f"Gemini ({config.GEMINI_MODEL})",
             "xai": f"Grok ({config.XAI_MODEL})"}.get(p, p) for p in tried
        ) or "no model provider"
        warnings.append(
            f"{names} unavailable; using rule-based text for every project.")
    elif provider and provider != "gemini":
        warnings.append(
            f"Explanations were written by Grok ({config.XAI_MODEL}); "
            f"Gemini was unavailable.")
    elif missing:
        warnings.append(
            f"Gemini omitted {len(missing)} project explanation(s); those use "
            f"rule-based text.")
    for pid in missing:
        explanations[pid] = fallbacks[pid]
    return explanations, warnings
