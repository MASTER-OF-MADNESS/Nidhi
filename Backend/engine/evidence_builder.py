"""
Assemble the evidence pack that every downstream layer reasons over.

Two jobs:
  1. Run the retrieval chain (Tavily first, knowledge base as fallback) and
     record which one actually supplied the NGO data.
  2. Tag every piece of evidence with its class, so any claim NIDHI makes can
     be traced back to something with a known reliability.

The company profile always comes from the knowledge base -- Temenos' own
priorities are not something to look up on the open web at request time.
"""

from __future__ import annotations

import config
from models.schemas import (
    EvidenceItem,
    EvidencePack,
    GenerateRequest,
    NGORecord,
)
from retrieval import bm25_search, md_parser, tavily_search


def _md_fallback_ngos(request: GenerateRequest, limit: int = 10) -> list[NGORecord]:
    """
    Top NGOs from the knowledge base for this brief.

    Temenos partners matching the request are pulled in alongside the BM25 hits:
    an existing relationship is real evidence and should not be lost just
    because a pool NGO scored higher on keywords.
    """
    query = bm25_search.build_query(
        csr_sector=request.csr_sector,
        sub_sector=request.csr_sub_sector,
        states=request.states,
        districts=request.districts,
        beneficiary_categories=request.beneficiary_categories,
    )
    # BM25 ranks on keywords alone, so an organisation elsewhere in the country
    # can outrank a local one on sector terms. The manager named specific
    # states, so restrict candidates to those (plus nationwide organisations)
    # before ranking -- otherwise the portfolio fills up with projects in
    # regions that were never asked for.
    def in_requested_geography(ngo: NGORecord) -> bool:
        if not request.states:
            return True
        blob = " ".join(filter(None, [
            *ngo.geographic_coverage, *ngo.districts,
            ngo.state or "", ngo.location or "",
        ])).lower()
        if any(term in blob for term in ("all india", "nationwide", "pan india")):
            return True
        return any(state.lower() in blob for state in request.states if state)

    def operates_in(state: str):
        needle = state.lower()
        def predicate(ngo: NGORecord) -> bool:
            blob = " ".join(filter(None, [
                *ngo.geographic_coverage, *ngo.districts,
                ngo.state or "", ngo.location or "",
            ])).lower()
            return needle in blob
        return predicate

    ranked: list[NGORecord] = []
    if query and len(request.states) > 1:
        # Draw a quota from each requested state before topping up globally.
        # A single global ranking skews to whichever state happens to have more
        # matching organisations, which both ignores the manager's stated
        # geography and leaves the optimizer unable to satisfy the per-region
        # concentration cap.
        seen: set[str] = set()
        quota = max(2, limit // len(request.states))
        for state in request.states:
            for ngo, _ in bm25_search.search_ngos(query, k=quota,
                                                  predicate=operates_in(state)):
                if ngo.ngo_id not in seen:
                    seen.add(ngo.ngo_id)
                    ranked.append(ngo)
        for ngo, _ in bm25_search.search_ngos(query, k=limit,
                                              predicate=in_requested_geography):
            if len(ranked) >= limit:
                break
            if ngo.ngo_id not in seen:
                seen.add(ngo.ngo_id)
                ranked.append(ngo)
    elif query:
        ranked = [ngo for ngo, _ in
                  bm25_search.search_ngos(query, k=limit,
                                          predicate=in_requested_geography)]

    if len(ranked) < 3:
        # Too few local candidates to build a portfolio from. Widen rather than
        # return nothing; the geographic mismatch shows up in scoring.
        seen = {n.ngo_id for n in ranked}
        ranked += [ngo for ngo, _ in bm25_search.search_ngos(query, k=limit)
                   if ngo.ngo_id not in seen] if query else []

    if not ranked:  # no usable query text at all
        ranked = md_parser.load_all_ngos()[:limit]

    ranked = ranked[:limit]

    chosen = {n.ngo_id: n for n in ranked}
    for partner in md_parser.load_master_ngos():
        if partner.ngo_id in chosen:
            continue
        if _matches_request(partner, request):
            chosen[partner.ngo_id] = partner
    return list(chosen.values())


def _matches_request(ngo: NGORecord, request: GenerateRequest) -> bool:
    """Loose relevance test used only to keep relevant Temenos partners in play."""
    from retrieval.text_utils import canonical_category

    wanted = canonical_category(request.csr_sector, request.csr_sub_sector)
    if wanted != "default" and wanted in ngo.categories:
        return True
    geography = " ".join(ngo.geographic_coverage + [ngo.state or "", ngo.country or ""]).lower()
    return any(s.lower() in geography for s in request.states if s)


def _relevant_history(request: GenerateRequest, limit: int = 6) -> list[dict]:
    """Historical Temenos projects closest to this brief, direct CSR first."""
    query = bm25_search.build_query(
        csr_sector=request.csr_sector,
        sub_sector=request.csr_sub_sector,
        states=request.states,
        beneficiary_categories=request.beneficiary_categories,
    )
    hits = bm25_search.search_projects(query, k=limit) if query else []
    projects = [p for p, score in hits if score > 0]
    if len(projects) < 3:
        seen = {p["project_id"] for p in projects}
        projects += [p for p in md_parser.load_historical_projects()
                     if p["is_direct_csr"] and p["project_id"] not in seen]
    return projects[:limit]


def _company_evidence(profile: dict) -> list[EvidenceItem]:
    items = [
        EvidenceItem(
            content=f"ESG strategic pillars: {', '.join(profile['esg_pillars'])}",
            source=profile["source_file"],
            evidence_class=config.EVIDENCE_VERIFIED_OFFICIAL,
            label="esg_pillars",
        ),
        EvidenceItem(
            content=profile["community_investment_statement"],
            source=profile["source_file"],
            evidence_class=config.EVIDENCE_VERIFIED_OFFICIAL,
            label="community_investment",
        ),
    ]
    for theme in profile["priority_themes"]:
        confidence = (theme.get("confidence") or "").upper()
        # The knowledge base marks its own inferences; carry that through
        # rather than promoting an inference to a verified fact.
        evidence_class = (
            config.EVIDENCE_INFERENCE if "INFERENCE" in confidence
            else config.EVIDENCE_VERIFIED_OFFICIAL if confidence.startswith("HIGH")
            else config.EVIDENCE_VERIFIED_SECONDARY
        )
        if not theme.get("evidence"):
            evidence_class = config.EVIDENCE_UNKNOWN
        items.append(EvidenceItem(
            content=f"Priority theme '{theme['priority']}': {theme.get('evidence') or 'no public evidence found'}",
            source=f"{profile['source_file']} PART 3",
            evidence_class=evidence_class,
            label=f"theme:{theme['priority']}",
        ))
    return items


def _ngo_evidence(ngos: list[NGORecord]) -> list[EvidenceItem]:
    items = []
    for ngo in ngos:
        unknown = ngo.unknown_fields()
        items.append(EvidenceItem(
            content=(f"{ngo.name} - focus: {', '.join(ngo.focus_areas) or 'unstated'}; "
                     f"geography: {', '.join(ngo.geographic_coverage) or ngo.state or 'unstated'}; "
                     f"compliance: {', '.join(ngo.compliance) or 'not evidenced'}"),
            source=ngo.source,
            evidence_class=ngo.evidence_class,
            url=ngo.url,
            label=f"ngo:{ngo.ngo_id}",
        ))
        if unknown:
            items.append(EvidenceItem(
                content=f"{ngo.name}: no public evidence for {', '.join(unknown)}",
                source=ngo.source,
                evidence_class=config.EVIDENCE_UNKNOWN,
                label=f"ngo_gaps:{ngo.ngo_id}",
            ))
    return items


async def build_evidence_pack(
    request: GenerateRequest, company_id: str = "temenos"
) -> EvidencePack:
    """
    Run the retrieval chain and assemble everything the reasoning layers see.

    Tavily is attempted first, always. The knowledge base supplies the NGO set
    only when Tavily fails, times out, or returns fewer than TAVILY_MIN_NGOS
    usable organisations -- and the pack records which path was taken.
    """
    profile = md_parser.load_company_profile()
    warnings: list[str] = []

    outcome = await tavily_search.search_ngos(
        csr_sector=request.csr_sector,
        country=request.country,
        states=request.states,
        beneficiary_categories=request.beneficiary_categories,
        compliance_requirements=request.compliance_requirements,
    )
    warnings.extend(outcome.warnings)

    if outcome.sufficient:
        data_source = config.DATA_SOURCE_TAVILY
        ngos = outcome.ngos
        # Temenos' own verified partners are always worth having alongside live
        # results; the web will not tell us who Temenos already works with.
        known = {n.name.lower() for n in ngos}
        ngos = ngos + [p for p in md_parser.load_master_ngos()
                       if p.name.lower() not in known and _matches_request(p, request)]
    else:
        data_source = config.DATA_SOURCE_MD
        ngos = _md_fallback_ngos(request)

    evidence_items = _company_evidence(profile) + _ngo_evidence(ngos)

    return EvidencePack(
        company_id=company_id,
        data_source=data_source,
        company_profile=profile,
        ngos=ngos,
        historical_projects=_relevant_history(request),
        user_input=request.model_dump(),
        evidence_items=evidence_items,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def _fmt_ngo(ngo: NGORecord, index: int) -> str:
    unknown = ngo.unknown_fields()
    lines = [
        f"{index}. {ngo.name} [{ngo.ngo_id}] (source: {ngo.source}, "
        f"evidence: {ngo.evidence_class})",
        f"   focus: {', '.join(ngo.focus_areas) or 'UNKNOWN'}",
        f"   categories: {', '.join(ngo.categories) or 'UNKNOWN'}",
        f"   geography: {', '.join(ngo.geographic_coverage) or ngo.state or 'UNKNOWN'}"
        f" | districts: {', '.join(ngo.districts) or 'UNKNOWN'}",
        f"   beneficiaries: {', '.join(ngo.target_beneficiaries) or 'UNKNOWN'}",
        f"   compliance: {', '.join(ngo.compliance) or 'UNKNOWN'}"
        f" | rating: {ngo.transparency_rating or 'UNKNOWN'}",
        f"   known impact: {ngo.known_impact or 'UNKNOWN'}",
    ]
    if ngo.temenos_partner:
        lines.append(f"   VERIFIED TEMENOS PARTNER: {ngo.temenos_relationship}")
    if unknown:
        lines.append(f"   NO EVIDENCE FOUND FOR: {', '.join(unknown)}")
    return "\n".join(lines)


def to_prompt_context(pack: EvidencePack, max_ngos: int = 20) -> str:
    """
    Render the pack as the text block sent to Gemini.

    Bounded on purpose: NGO entries are the bulk of the pack and are capped so a
    large retrieval cannot push the instructions out of the model's attention.
    """
    request = pack.user_input
    profile = pack.company_profile

    history = "\n".join(
        f"- {p['project_id']} {p['project_name']} ({p.get('year') or 'year UNKNOWN'}): "
        f"{p.get('csr_category') or 'UNKNOWN'} in {p.get('region') or 'UNKNOWN'}, "
        f"partner {p.get('partner') or 'UNKNOWN'}, "
        f"beneficiaries {p.get('beneficiaries') or 'UNKNOWN'}, "
        f"budget {p.get('budget') or 'UNKNOWN'}"
        for p in pack.historical_projects
    ) or "- none retrieved"

    themes = ", ".join(
        t["priority"] for t in profile.get("priority_themes", [])
        if (t.get("confidence") or "").upper().startswith("HIGH")
    )

    return f"""## FUNDING COMPANY
{profile.get('official_name')} - {profile.get('industry') or 'UNKNOWN'}
ESG pillars: {', '.join(profile.get('esg_pillars', []))}
High-confidence priority themes: {themes}
SDG alignment: {', '.join(f'SDG {n}' for n in profile.get('sdg_alignment', []))}
Community investment approach: {profile.get('community_investment_statement')}

## CSR MANAGER REQUIREMENTS
Budget: {request.get('total_csr_budget')} {request.get('currency')}
Funding type: {request.get('funding_type') or 'UNSPECIFIED'}
Projects wanted: {request.get('number_of_projects')} over {request.get('project_duration')} months
Per-project funding range: {request.get('min_project_funding')} to {request.get('max_project_funding')}
Geography: {request.get('country')} / {', '.join(request.get('states') or []) or 'any state'} / {', '.join(request.get('districts') or []) or 'any district'}
Sector: {request.get('csr_sector')} | Sub-sector: {request.get('csr_sub_sector') or 'UNSPECIFIED'}
Beneficiary categories: {', '.join(request.get('beneficiary_categories') or []) or 'UNSPECIFIED'}
Minimum NGO experience: {request.get('ngo_experience')} years
Similar project experience required: {request.get('similar_project_experience')}
Compliance required: {', '.join(request.get('compliance_requirements') or []) or 'UNSPECIFIED'}
Collaboration model: {request.get('collaboration_type') or 'UNSPECIFIED'}
Employee involvement wanted: {request.get('employee_involvement')}
Additional comments: {request.get('additional_comments') or 'none'}

## RETRIEVED NGOs (source: {pack.data_source})
{chr(10).join(_fmt_ngo(n, i + 1) for i, n in enumerate(pack.ngos[:max_ngos]))}

## HISTORICAL COMPANY CSR PROJECTS (precedent, not available for funding)
{history}
"""


def evidence_summary(pack: EvidencePack) -> dict[str, int]:
    """Count evidence by class -- shown to the user in the overview section."""
    counts: dict[str, int] = {}
    for item in pack.evidence_items:
        counts[item.evidence_class] = counts.get(item.evidence_class, 0) + 1
    return counts
