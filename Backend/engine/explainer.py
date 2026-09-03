"""
Turn deterministic results into language a CSR manager can act on.

Gemini writes the prose. This module builds the prompts and -- critically --
supplies a deterministic fallback for every one of them, so the narrative
degrades to plainer wording rather than disappearing when the model is
unavailable.

Everything here is decision support. The wording is deliberately
"recommended" / "suggested" and never "approved" or "funded": NIDHI proposes,
a human decides.
"""

from __future__ import annotations

import config
from models.schemas import (
    AllocationResult,
    ExcludedProject,
    GenerateRequest,
    HumanReviewFlag,
    NGORecommendation,
    OptimizationOutcome,
    ProjectScore,
)

GUARDRAIL = (
    "Write for a CSR manager deciding where to place funds. Be specific and "
    "cite the evidence given. Never write 'approved' or 'funded' as a verdict: "
    "these are recommendations for a human to decide on. Never invent figures, "
    "organisations or outcomes that are not in the data below. Where a field is "
    "marked unknown, say plainly that it is unknown and what would resolve it. "
    "Do not restate the numeric scores as if you calculated them."
)


def _money(amount: float, currency: str) -> str:
    return f"{config.CURRENCY_SYMBOLS.get(currency, '')}{amount:,.0f}"


# ---------------------------------------------------------------------------
# Per-project explanation
# ---------------------------------------------------------------------------

def project_prompt(
    score: ProjectScore, allocation: AllocationResult | None,
    recommendation: NGORecommendation | None, request: GenerateRequest,
) -> str:
    top_dimensions = sorted(score.dimensions, key=lambda d: -d.score * d.weight)[:4]
    dimension_lines = "\n".join(
        f"- {d.name.replace('_', ' ')}: {d.score:.0f}/100 (weight {d.weight:.0f}%, "
        f"confidence {d.confidence:.0%}) - {'; '.join(d.reasons) or 'no evidence'}"
        for d in top_dimensions)

    ngo_lines = "none identified"
    if recommendation and recommendation.matches:
        ngo_lines = "\n".join(
            f"- {m.ngo_name}: compatibility {m.compatibility_score:.0f}/100, "
            f"{m.star_rating} stars. Strengths: {'; '.join(m.strengths) or 'none recorded'}. "
            f"Limitations: {'; '.join(m.limitations) or 'none recorded'}"
            for m in recommendation.matches)

    allocation_line = (
        f"{_money(allocation.allocated_amount, request.currency)} "
        f"({allocation.percentage_of_budget:.1f}% of the budget)"
        if allocation else "not selected for funding in this portfolio")

    return f"""{GUARDRAIL}

Write 3-4 short paragraphs about this candidate project. Cover, in order:
why it was prioritised, why this amount, why this implementing partner, and
what information is missing.

PROJECT: {score.project_name}
Category: {score.category} | Region: {score.region}
Overall score: {score.final_score:.1f}/100 (recommendation: {score.recommendation})
Confidence in that score: {score.confidence:.0%}
Proposal completeness: {score.completeness:.0f}%
Suggested allocation: {allocation_line}

STRONGEST SCORING DIMENSIONS:
{dimension_lines}

SUGGESTED IMPLEMENTING PARTNERS:
{ngo_lines}

FIELDS WITH NO SUPPORTING EVIDENCE: {', '.join(score.unknown_fields) or 'none'}
EVIDENCE SOURCES: {', '.join(score.evidence_sources) or 'knowledge base'}
"""


def project_fallback(
    score: ProjectScore, allocation: AllocationResult | None,
    recommendation: NGORecommendation | None, request: GenerateRequest,
) -> str:
    """Deterministic prose used when the model is unavailable."""
    # RCA entries are fragments, so join them into real sentences rather than
    # letting them run together.
    drivers = ". ".join(r.rstrip(".") for r in score.rca[:3])
    parts = [
        f"{score.project_name} scores {score.final_score:.1f}/100 and is "
        f"classified {score.recommendation}."
        + (f" {drivers}." if drivers else "")
    ]
    if allocation:
        parts.append(
            f"A suggested allocation of "
            f"{_money(allocation.allocated_amount, request.currency)} "
            f"({allocation.percentage_of_budget:.1f}% of the budget) reflects its "
            f"rank within the recommended portfolio and the per-project funding "
            f"limits set for this cycle.")
    else:
        parts.append("This project is not part of the recommended portfolio.")

    if recommendation and recommendation.matches:
        best = recommendation.matches[0]
        parts.append(
            f"{best.ngo_name} is the strongest suggested implementing partner at "
            f"{best.compatibility_score:.0f}/100 compatibility"
            + (f", based on {best.strengths[0].lower()}" if best.strengths else "")
            + ". " + (f"Note: {best.limitations[0].lower()}."
                      if best.limitations else ""))

    if score.unknown_fields:
        parts.append(
            f"Confidence is {score.confidence:.0%} and completeness "
            f"{score.completeness:.0f}% because no public evidence was found for: "
            f"{', '.join(score.unknown_fields[:6])}. These are unknowns rather "
            f"than negative findings, and should be confirmed with the partner "
            f"before any funding decision.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio-level insights
# ---------------------------------------------------------------------------

def portfolio_prompt(
    outcome: OptimizationOutcome, scores: list[ProjectScore],
    request: GenerateRequest, profile: dict, data_source: str,
) -> str:
    selected_lines = "\n".join(
        f"- {a.project_name} ({a.category}, {a.region}): "
        f"{_money(a.allocated_amount, request.currency)}, "
        f"{a.percentage_of_budget:.1f}% of budget, score {a.final_score:.1f}, "
        f"confidence {a.confidence:.0%}"
        for a in outcome.selected) or "- none"

    regions: dict[str, float] = {}
    categories: dict[str, float] = {}
    for a in outcome.selected:
        regions[a.region] = regions.get(a.region, 0.0) + a.percentage_of_budget
        categories[a.category] = categories.get(a.category, 0.0) + a.percentage_of_budget

    themes = ", ".join(
        t["priority"] for t in profile.get("priority_themes", [])
        if (t.get("confidence") or "").upper().startswith("HIGH"))

    return f"""{GUARDRAIL}

Write 5 to 7 bullet points of portfolio-level strategic insight. Cover:
alignment with the company's ESG pillars and priority themes; the regional and
sector distribution; gaps against the company's stated priorities; risks
carried by this portfolio; and what to do differently next cycle.
Each bullet should be one or two sentences and reference the specifics below.

COMPANY ESG PILLARS: {', '.join(profile.get('esg_pillars', []))}
COMPANY HIGH-CONFIDENCE PRIORITY THEMES: {themes}

RECOMMENDED PORTFOLIO ({len(outcome.selected)} of {len(scores)} candidates,
{_money(outcome.total_allocated, request.currency)} of
{_money(request.total_csr_budget, request.currency)},
{outcome.budget_utilisation:.1f}% utilisation):
{selected_lines}

REGIONAL SPLIT: {', '.join(f'{k} {v:.0f}%' for k, v in sorted(regions.items())) or 'n/a'}
SECTOR SPLIT: {', '.join(f'{k} {v:.0f}%' for k, v in sorted(categories.items())) or 'n/a'}

CONSTRAINTS RELAXED TO REACH THIS RESULT:
{chr(10).join('- ' + r for r in outcome.constraint_report.relaxations_applied) or '- none'}

SOLVER NOTES:
{chr(10).join('- ' + n for n in outcome.constraint_report.notes) or '- none'}

EVIDENCE BASE: NGO data came from {data_source}.
The manager asked for {request.number_of_projects} projects in
{', '.join(request.states) or request.country}, sector {request.csr_sector}.
"""


def portfolio_fallback(
    outcome: OptimizationOutcome, scores: list[ProjectScore],
    request: GenerateRequest, profile: dict, data_source: str,
) -> str:
    regions: dict[str, float] = {}
    categories: dict[str, float] = {}
    for a in outcome.selected:
        regions[a.region] = regions.get(a.region, 0.0) + a.percentage_of_budget
        categories[a.category] = categories.get(a.category, 0.0) + a.percentage_of_budget

    bullets = [
        f"The recommended portfolio places "
        f"{_money(outcome.total_allocated, request.currency)} across "
        f"{len(outcome.selected)} of {len(scores)} evaluated candidates, using "
        f"{outcome.budget_utilisation:.1f}% of the available budget.",
        "Regional distribution: "
        + (", ".join(f"{k} {v:.0f}%" for k, v in sorted(regions.items()))
           or "no allocation made") + ".",
        "Sector distribution: "
        + (", ".join(f"{k.replace('_', ' ')} {v:.0f}%"
                     for k, v in sorted(categories.items()))
           or "no allocation made") + ".",
    ]

    average_confidence = (sum(a.confidence for a in outcome.selected)
                          / len(outcome.selected)) if outcome.selected else 0.0
    bullets.append(
        f"Average confidence across the recommended portfolio is "
        f"{average_confidence:.0%}. NGO evidence came from {data_source}; "
        f"figures drawn from the knowledge base are point-in-time research and "
        f"should be re-verified before commitment.")

    if outcome.constraint_report.relaxations_applied:
        bullets.append(
            "Portfolio rules relaxed to reach a feasible result: "
            + "; ".join(outcome.constraint_report.relaxations_applied)
            + ". Widening the candidate search would restore them.")
    else:
        bullets.append("All portfolio balance constraints were satisfied without "
                       "relaxation.")

    covered = set(categories)
    missing = [t["priority"] for t in profile.get("priority_themes", [])
               if (t.get("confidence") or "").upper().startswith("HIGH")
               and t.get("canonical_category") not in covered][:4]
    if missing:
        bullets.append(
            "Company priority themes not represented in this portfolio: "
            + ", ".join(missing) + ". Consider these for the next cycle.")

    weak = [a.project_name for a in outcome.selected if a.confidence < 0.6]
    if weak:
        bullets.append(
            f"{len(weak)} recommended project(s) rest on low-confidence "
            f"evidence and should be diligenced before commitment: "
            + ", ".join(weak[:3]) + ".")
    return "\n".join(f"- {b}" for b in bullets)


# ---------------------------------------------------------------------------
# Excluded projects
# ---------------------------------------------------------------------------

def exclusion_prompt(
    excluded: list[ExcludedProject], request: GenerateRequest
) -> str:
    lines = "\n".join(
        f"- {e.project_name} (score {e.final_score:.1f}): {e.reason}. "
        f"Blocking factor: {e.binding_constraint or 'none identified'}."
        for e in excluded)
    return f"""{GUARDRAIL}

For each excluded candidate below, write one short paragraph saying why it did
not make the recommended portfolio and precisely what would have to change for
it to be reconsidered. Be concrete about whether the barrier was its own score
or a portfolio constraint.

EXCLUDED CANDIDATES:
{lines}

PORTFOLIO SETTINGS: {request.number_of_projects} projects,
per-project range {_money(request.min_project_funding, request.currency)} to
{_money(request.effective_max(), request.currency)},
minimum score to be recommended: {config.THRESHOLD_REJECT:.0f}.
"""


def exclusion_fallback(
    excluded: list[ExcludedProject], request: GenerateRequest
) -> str:
    return "\n\n".join(
        f"{e.project_name} (score {e.final_score:.1f}): {e.reason}. "
        f"{e.what_would_change_it}"
        for e in excluded) or "No candidates were excluded."


# ---------------------------------------------------------------------------
# Human review flags
# ---------------------------------------------------------------------------

def build_review_flags(
    scores: list[ProjectScore], outcome: OptimizationOutcome,
    recommendations: list[NGORecommendation], data_source: str,
    warnings: list[str],
) -> list[HumanReviewFlag]:
    """
    Everything a human should look at before acting on this analysis.

    Low confidence is not a defect to hide -- it is the main output of the
    UNKNOWN-is-not-zero rule, and surfacing it is the point.
    """
    flags: list[HumanReviewFlag] = []
    funded_ids = {a.project_id for a in outcome.selected}

    for score in scores:
        if score.project_id not in funded_ids:
            continue
        if score.confidence < config.LOW_CONFIDENCE_THRESHOLD:
            flags.append(HumanReviewFlag(
                severity="HIGH", category="Low confidence",
                subject=score.project_name,
                detail=(f"Recommended for funding at {score.final_score:.1f}/100 "
                        f"but confidence is only {score.confidence:.0%}. "
                        f"Unevidenced: {', '.join(score.unknown_fields[:5])}."),
                suggested_action="Request these fields from the partner before "
                                 "committing funds."))
        elif score.completeness < config.LOW_COMPLETENESS_THRESHOLD:
            flags.append(HumanReviewFlag(
                severity="MEDIUM", category="Incomplete proposal",
                subject=score.project_name,
                detail=(f"Only {score.completeness:.0f}% of expected proposal "
                        f"fields are evidenced."),
                suggested_action="Complete the proposal record before final review."))

    for recommendation in recommendations:
        if not recommendation.matches:
            flags.append(HumanReviewFlag(
                severity="HIGH", category="No eligible partner",
                subject=recommendation.project_name,
                detail=(f"No NGO passed the mandatory filters "
                        f"({recommendation.ineligible_count} were assessed)."),
                suggested_action="Relax a requirement or widen the search "
                                 "geography, then re-run."))
        elif recommendation.matches[0].compatibility_score < 60:
            flags.append(HumanReviewFlag(
                severity="MEDIUM", category="Weak partner fit",
                subject=recommendation.project_name,
                detail=(f"Best available partner scores only "
                        f"{recommendation.matches[0].compatibility_score:.0f}/100."),
                suggested_action="Consider a wider partner search before "
                                 "committing."))

    for relaxation in outcome.constraint_report.relaxations_applied:
        flags.append(HumanReviewFlag(
            severity="MEDIUM", category="Constraint relaxed",
            subject="Portfolio construction",
            detail=f"The {relaxation} could not be satisfied and was relaxed.",
            suggested_action="Review whether this trade-off is acceptable, or "
                             "widen the candidate pool and re-run."))

    if data_source == config.DATA_SOURCE_MD:
        flags.append(HumanReviewFlag(
            severity="MEDIUM", category="Evidence freshness",
            subject="Data source",
            detail=("Live web search was unavailable or returned too little, so "
                    "NGO data came from the point-in-time knowledge base."),
            suggested_action="Verify current compliance, capacity and "
                             "availability directly with each partner."))

    for warning in warnings:
        flags.append(HumanReviewFlag(
            severity="LOW", category="Pipeline warning",
            subject="Retrieval and extraction",
            detail=warning,
            suggested_action="Informational; no action required unless repeated."))

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    flags.sort(key=lambda f: (order[f.severity], f.category, f.subject))
    return flags


# ---------------------------------------------------------------------------
# Batched explanations
#
# One model call for every project rather than one per project. A free-tier
# Gemini key allows 20 requests a day; at eight scorecards plus insights plus
# exclusions, a single run would consume half of that. Batching takes a run
# from ~11 calls to 3.
# ---------------------------------------------------------------------------

def batch_projects_prompt(
    items: list[tuple[ProjectScore, AllocationResult | None, NGORecommendation | None]],
    request: GenerateRequest,
) -> str:
    blocks = []
    for score, allocation, recommendation in items:
        top = sorted(score.dimensions, key=lambda d: -d.score * d.weight)[:3]
        partner = (recommendation.matches[0].ngo_name
                   if recommendation and recommendation.matches else "none identified")
        blocks.append(
            f"""### {score.project_id} | {score.project_name}
category: {score.category} | region: {score.region}
score: {score.final_score:.1f}/100 ({score.recommendation}) | confidence {score.confidence:.0%} | completeness {score.completeness:.0f}%
allocation: {_money(allocation.allocated_amount, request.currency) if allocation else 'not selected'}
strongest dimensions: {'; '.join(f"{d.name} {d.score:.0f}/100 ({d.reasons[0] if d.reasons else 'no evidence'})" for d in top)}
suggested partner: {partner}
no evidence for: {', '.join(score.unknown_fields) or 'none'}""")

    return f"""{GUARDRAIL}

For EACH project below write 2-3 short paragraphs covering: why it was
prioritised, why this amount, why this partner, and what information is missing.

Return JSON only, shaped as:
{{"explanations": [{{"project_id": "...", "text": "..."}}]}}

Include one entry for every project id listed. Do not merge or omit any.

{chr(10).join(blocks)}
"""


BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "explanations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["project_id", "text"],
            },
        }
    },
    "required": ["explanations"],
}
