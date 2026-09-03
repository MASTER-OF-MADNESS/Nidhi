"""
Match implementing NGOs to each recommended project.

Two stages, in this order and never the other way round:

  A. Hard eligibility filters. An organisation that fails a mandatory
     requirement is out, full stop. The knowledge base is explicit that "a high
     soft score must not override a hard eligibility failure".

  B. Weighted compatibility scoring over the survivors, using the weights in
     config.NGO_MATCH_WEIGHTS (sector 30 / geography 25 / experience 20 /
     compliance 15 / relationship 10).

Unknown fields never fail a filter. Absent evidence means we could not verify a
requirement, not that the organisation fails it -- so it flows through to the
match's `limitations` for a human to check, rather than silently eliminating a
possibly excellent partner.
"""

from __future__ import annotations

import config
from models.schemas import (
    NGOMatch,
    NGORecord,
    NGORecommendation,
    GenerateRequest,
    ProjectScore,
)
from retrieval import bm25_search
from retrieval.text_utils import canonical_category

MAX_STARS = 5.0


def _normalise_credential(value: str) -> str:
    return value.upper().replace("-", "").replace(" ", "").strip()


def _geography_terms(ngo: NGORecord) -> str:
    return " ".join(filter(None, [
        *ngo.geographic_coverage, *ngo.districts,
        ngo.state or "", ngo.location or "", ngo.country or "",
    ])).lower()


# ---------------------------------------------------------------------------
# Stage A - hard eligibility
# ---------------------------------------------------------------------------

def check_eligibility(
    ngo: NGORecord, project: ProjectScore, request: GenerateRequest
) -> tuple[bool, list[str]]:
    """
    Returns (eligible, failure reasons).

    Only a *positive* contradiction disqualifies. Missing evidence does not.
    """
    failures: list[str] = []

    # 1. Sector expertise, when the organisation states any at all.
    wanted = canonical_category(project.category, request.csr_sector)
    if ngo.categories and wanted != "default" and wanted not in ngo.categories:
        failures.append(
            f"No stated expertise in {wanted.replace('_', ' ')} "
            f"(states: {', '.join(ngo.categories)})")

    # 2. Geographic coverage, when the organisation states any at all.
    geography = _geography_terms(ngo)
    if geography:
        region = project.region.lower().strip()
        region_words = [w for w in region.split() if len(w) > 3]
        national = any(term in geography
                       for term in ("all india", "nationwide", "national", "pan india"))
        # A shared country is NOT regional coverage: an Odisha-only organisation
        # does not cover Kerala just because both are in India. Country-level
        # matching only counts when the project itself is country-wide.
        country_wide_project = region in {(request.country or "").lower(), "nationwide",
                                          "all india", "national"}
        covers = (any(w in geography for w in region_words)
                  or national
                  or (country_wide_project
                      and (ngo.country or "").lower() == (request.country or "").lower()))
        if not covers:
            failures.append(
                f"No evidenced coverage of {project.region} "
                f"(covers: {', '.join(ngo.geographic_coverage) or ngo.state or 'unstated'})")

    # 3. Compliance. Only fails when the organisation publishes credentials and
    #    a required one is demonstrably absent from that published set.
    if request.compliance_requirements and ngo.compliance:
        held = {_normalise_credential(c) for c in ngo.compliance}
        missing = [c for c in request.compliance_requirements
                   if _normalise_credential(c) not in held]
        if missing:
            failures.append(f"Published credentials do not include "
                            f"{', '.join(missing)}")

    # 4. Minimum experience, only when years are actually known.
    if request.ngo_experience and ngo.years_experience is not None:
        if ngo.years_experience < request.ngo_experience:
            failures.append(
                f"{ngo.years_experience} years of operation is below the "
                f"{request.ngo_experience}-year minimum")

    return (not failures), failures


# ---------------------------------------------------------------------------
# Stage B - weighted compatibility
# ---------------------------------------------------------------------------

def _sector_component(ngo, project, request) -> tuple[float, list[str], list[str]]:
    wanted = canonical_category(project.category, request.csr_sector)
    if not ngo.categories:
        return 50.0, [], ["Focus areas are not documented"]
    if wanted in ngo.categories:
        focus = ", ".join(ngo.focus_areas[:3]) or wanted.replace("_", " ")
        return 100.0, [f"Direct sector match: {focus}"], []
    return 30.0, [], [f"Sector fit with {wanted.replace('_', ' ')} is indirect"]


def _geography_component(ngo, project, request) -> tuple[float, list[str], list[str]]:
    geography = _geography_terms(ngo)
    if not geography:
        return 50.0, [], ["Geographic coverage is not documented"]
    region = project.region.lower()
    if any(d.lower() in region or region in d.lower() for d in ngo.districts):
        return 100.0, [f"Operates in {project.region} at district level"], []
    if any(w in geography for w in region.split() if len(w) > 3):
        note = [] if ngo.districts else ["District-level coverage unverified"]
        return 90.0, [f"Operates in {project.region}"], note
    if "all india" in geography or "nationwide" in geography:
        return 70.0, ["National coverage"], [f"Presence in {project.region} unverified"]
    return 35.0, [], [f"No evidenced presence in {project.region}"]


def _experience_component(ngo, project, request) -> tuple[float, list[str], list[str]]:
    strengths: list[str] = []
    limitations: list[str] = []
    if ngo.years_experience is None:
        score = 50.0
        limitations.append("Years of operation not published")
    else:
        score = min(100.0, 100.0 * ngo.years_experience / 15.0)
        strengths.append(f"{ngo.years_experience} years of operation")

    if ngo.similar_project_experience is True:
        score = min(100.0, score + 15.0)
        strengths.append("Documented similar project experience")
    elif request.similar_project_experience and ngo.similar_project_experience is None:
        limitations.append("Similar project experience requested but not evidenced")

    if ngo.known_impact:
        score = min(100.0, score + 10.0)
        strengths.append(f"Published scale: {ngo.known_impact}")
    if ngo.past_project_count is None:
        limitations.append("Past project count not published")
    return score, strengths, limitations


def _compliance_component(ngo, project, request) -> tuple[float, list[str], list[str]]:
    if not ngo.compliance:
        return 50.0, [], ["No compliance credentials found in public sources"]

    held = {_normalise_credential(c) for c in ngo.compliance}
    all_creds = [c for c in config.COMPLIANCE_CREDENTIALS
                 if _normalise_credential(c) in held]
    score = 100.0 * len(all_creds) / len(config.COMPLIANCE_CREDENTIALS)
    strengths = [f"Holds {', '.join(all_creds)}"] if all_creds else []
    limitations: list[str] = []

    missing = [c for c in request.compliance_requirements
               if _normalise_credential(c) not in held]
    if missing:
        limitations.append(f"Requested {', '.join(missing)} not evidenced")

    if ngo.transparency_rating:
        bonus = {"platinum": 15.0, "gold": 12.0, "high": 10.0,
                 "silver": 6.0, "bronze": 2.0}.get(ngo.transparency_rating.lower(), 0.0)
        score = min(100.0, score + bonus)
        if bonus:
            strengths.append(f"{ngo.transparency_rating} transparency rating")
    else:
        limitations.append("Transparency rating not published")
    return score, strengths, limitations


def _relationship_component(ngo, project, request) -> tuple[float, list[str], list[str]]:
    if ngo.temenos_partner:
        relationship = (ngo.temenos_relationship or "verified partner").replace("_", " ").lower()
        return 100.0, [f"Verified existing company relationship ({relationship})"], []
    return 40.0, [], ["No existing relationship with the funding company on record"]


COMPONENTS = {
    "sector": _sector_component,
    "geography": _geography_component,
    "experience": _experience_component,
    "compliance": _compliance_component,
    "temenos_relationship": _relationship_component,
}


def score_match(
    ngo: NGORecord, project: ProjectScore, request: GenerateRequest,
    keyword_score: float = 0.0,
) -> NGOMatch:
    """Weighted compatibility for one eligible NGO against one project."""
    breakdown: dict[str, float] = {}
    strengths: list[str] = []
    limitations: list[str] = []
    total = 0.0

    for name, weight in config.NGO_MATCH_WEIGHTS.items():
        component_score, component_strengths, component_limits = COMPONENTS[name](
            ngo, project, request)
        breakdown[name] = round(component_score, 1)
        total += component_score * weight / 100.0
        strengths.extend(component_strengths)
        limitations.extend(component_limits)

    if ngo.temenos_partner:
        total = min(100.0, total + config.TEMENOS_PARTNER_BONUS)
        breakdown["partner_bonus"] = config.TEMENOS_PARTNER_BONUS

    # Keyword similarity is a tie-breaker, not a scoring dimension: it nudges
    # within a couple of points rather than competing with the weighted model.
    if keyword_score > 0:
        total = min(100.0, total + min(keyword_score / 10.0, 2.0))
        breakdown["keyword_similarity"] = round(keyword_score, 2)

    # Every unknown field is a limitation a human should close before funding.
    for field in ngo.unknown_fields():
        label = field.replace("_", " ")
        if not any(label in existing.lower() for existing in limitations):
            limitations.append(f"No public evidence for {label}")

    return NGOMatch(
        ngo_name=ngo.name,
        compatibility_score=round(total, 2),
        star_rating=round(min(total / 20.0, MAX_STARS) * 2) / 2,
        strengths=strengths[:6],
        limitations=limitations[:6],
        evidence_source="Tavily" if ngo.source == "TAVILY" else "Knowledge base",
        temenos_partner=ngo.temenos_partner,
        ngo_id=ngo.ngo_id,
        project_id=project.project_id,
        breakdown=breakdown,
    )


def match_ngos_for_project(
    project: ProjectScore, ngos: list[NGORecord], request: GenerateRequest,
    limit: int | None = None,
) -> NGORecommendation:
    """Top NGOs for one project: hard filters first, then weighted scoring."""
    limit = limit or config.NGO_MATCHES_PER_PROJECT

    eligible: list[NGORecord] = []
    ineligible: list[dict[str, str]] = []
    for ngo in ngos:
        ok, failures = check_eligibility(ngo, project, request)
        if ok:
            eligible.append(ngo)
        else:
            ineligible.append({"ngo_name": ngo.name, "reason": "; ".join(failures)})

    # Keyword similarity over the eligible set only, so a hard failure can never
    # be recovered by a strong text match.
    query = " ".join(filter(None, [
        project.category.replace("_", " "), project.project_name, project.region,
        request.csr_sector, " ".join(request.beneficiary_categories),
    ]))
    eligible_ids = {n.ngo_id for n in eligible}
    keyword = {
        ngo.ngo_id: score
        for ngo, score in bm25_search.search_ngos(
            query, k=len(ngos) or 1,
            predicate=lambda n: n.ngo_id in eligible_ids)
    } if query and eligible else {}

    matches = [score_match(n, project, request, keyword.get(n.ngo_id, 0.0))
               for n in eligible]
    matches.sort(key=lambda m: (-m.compatibility_score, m.ngo_id or ""))

    return NGORecommendation(
        project_id=project.project_id,
        project_name=project.project_name,
        matches=matches[:limit],
        ineligible_count=len(ineligible),
        ineligible_examples=ineligible[:3],
    )


def match_all(
    projects: list[ProjectScore], ngos: list[NGORecord], request: GenerateRequest,
) -> list[NGORecommendation]:
    return [match_ngos_for_project(p, ngos, request) for p in projects]


def build_comparison(recommendations: list[NGORecommendation]) -> dict:
    """A flat side-by-side table of every recommended NGO, for the UI."""
    rows: list[dict] = []
    for recommendation in recommendations:
        for rank, match in enumerate(recommendation.matches, start=1):
            rows.append({
                "project_id": recommendation.project_id,
                "project_name": recommendation.project_name,
                "rank": rank,
                "ngo_name": match.ngo_name,
                "compatibility_score": match.compatibility_score,
                "star_rating": match.star_rating,
                "temenos_partner": match.temenos_partner,
                "evidence_source": match.evidence_source,
                "top_strength": match.strengths[0] if match.strengths else None,
                "key_limitation": match.limitations[0] if match.limitations else None,
                **{f"score_{k}": v for k, v in match.breakdown.items()},
            })
    return {
        "rows": rows,
        "dimensions": list(config.NGO_MATCH_WEIGHTS),
        "weights": dict(config.NGO_MATCH_WEIGHTS),
        "max_stars": MAX_STARS,
    }
