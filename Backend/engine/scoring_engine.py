"""
Deterministic 10-dimension scoring. No AI, no network, no randomness.

Two rules govern everything here:

1. UNKNOWN is not ZERO. A dimension with no evidence scores the neutral prior
   (config.NEUTRAL_PRIOR) with confidence 0.0 and names the missing inputs.
   Scoring a gap as 0 would punish an NGO for the state of public disclosure
   rather than for anything it did.

2. Score and confidence are independent. A project can be excellent and poorly
   evidenced. The score says how good it looks; the confidence says how far the
   score can be trusted; completeness says how much of the file is filled in.

Every dimension returns (score, confidence, reasons, unknown_inputs) so the
explanation layer can cite why a number came out the way it did.
"""

from __future__ import annotations

import math

import config
from models.schemas import (
    CandidateProject,
    DimensionScore,
    GenerateRequest,
    NGORecord,
    ProjectScore,
)
from retrieval.text_utils import canonical_category

# (score, confidence, reasons, unknown_inputs)
DimensionResult = tuple[float, float, list[str], list[str]]

UNKNOWN = (config.NEUTRAL_PRIOR, 0.0)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _interpolate(value: float, good: float, median: float, poor: float) -> float:
    """
    Map a lower-is-better metric onto 0-100 against three benchmark anchors.

    good -> 100, median -> 60, poor -> 20, worse than poor decays toward 0.
    """
    if value <= good:
        return 100.0
    if value <= median:
        return 100.0 - 40.0 * (value - good) / max(median - good, 1e-9)
    if value <= poor:
        return 60.0 - 40.0 * (value - median) / max(poor - median, 1e-9)
    return max(0.0, 20.0 * poor / max(value, 1e-9))


def _overlap(wanted: list[str], have: list[str]) -> tuple[int, list[str]]:
    """Case-insensitive substring overlap; returns (count, matched terms)."""
    have_blob = " ".join(h.lower() for h in have if h)
    matched = [w for w in wanted if w and w.lower() in have_blob]
    return len(matched), matched


# ---------------------------------------------------------------------------
# 1. Strategic alignment (20%)
#    PS2 PART 18: ESG alignment 0-10, SDG alignment 0-5, geography 0-5
# ---------------------------------------------------------------------------

_TIER_LABEL = {
    "A": "documented community strategy",
    "B": "demonstrated in delivered projects",
    "C": "broader ESG alignment",
}


def score_strategic_alignment(
    project: CandidateProject, ngo: NGORecord | None,
    request: GenerateRequest, profile: dict
) -> DimensionResult:
    reasons: list[str] = []
    unknown: list[str] = []

    blob = " ".join(filter(None, [
        project.category, project.sub_category, project.project_name,
        project.description, " ".join(project.beneficiary_groups),
    ])).lower()

    tier_hit = tier_theme = None
    for tier in ("A", "B", "C"):
        for theme in config.THEME_TIERS[tier]:
            if theme in blob:
                tier_hit, tier_theme = tier, theme
                break
        if tier_hit:
            break

    if tier_hit:
        esg = config.TIER_SCORES[tier_hit]
        reasons.append(f"Matches Tier {tier_hit} company priority "
                       f"'{tier_theme}' ({_TIER_LABEL[tier_hit]})")
    else:
        esg = config.NEUTRAL_PRIOR
        reasons.append("No direct match to a documented company priority theme")
        unknown.append("theme_alignment")

    company_sdgs = set(profile.get("sdg_alignment") or config.TEMENOS_SDGS)
    if project.sdgs:
        shared = sorted(company_sdgs & set(project.sdgs))
        sdg = _clamp(100.0 * len(shared) / max(len(project.sdgs), 1))
        reasons.append(
            f"SDG overlap with company alignment: "
            f"{', '.join(f'SDG {n}' for n in shared)}" if shared
            else "Stated SDGs do not overlap the company's SDG alignment")
    else:
        sdg = UNKNOWN[0]
        unknown.append("sdgs")

    region_blob = f"{project.region} {project.country}".lower()
    presence = max((v for k, v in config.TEMENOS_PRESENCE.items() if k in region_blob),
                   default=None)
    if presence is not None:
        geo = _clamp(40.0 + 60.0 * presence)
        reasons.append(f"{project.region} aligns with documented company CSR presence")
    else:
        geo = UNKNOWN[0]
        reasons.append(f"No documented company CSR presence in {project.region}")
        unknown.append("geographic_alignment")

    # PART 18 sub-weights: 10 / 5 / 5 out of 20.
    score = esg * 0.50 + sdg * 0.25 + geo * 0.25
    return _clamp(score), (3 - len(unknown)) / 3.0, reasons, unknown


# ---------------------------------------------------------------------------
# 2. Social impact (20%)
#    Beneficiary reach 0-8, impact depth 0-7, measurability 0-5
# ---------------------------------------------------------------------------

_TRANSFORMATIONAL = (
    "livelihood", "employment", "income", "rehabilitation", "surgery",
    "housing", "school", "scholarship", "skill", "training", "empowerment",
    "restoration", "treatment", "education",
)
_INCREMENTAL = ("awareness", "campaign", "workshop", "event", "drive",
                "distribution", "mural", "celebration", "one-off")
_MEASURABLE = ("outcome", "measur", "kpi", "indicator", "assessment",
               "tracked", "monitor", "evaluat", "certif")


def score_social_impact(
    project: CandidateProject, ngo: NGORecord | None,
    request: GenerateRequest, profile: dict
) -> DimensionResult:
    reasons: list[str] = []
    unknown: list[str] = []

    if project.estimated_beneficiaries and project.estimated_beneficiaries > 0:
        # Log scale: 100 beneficiaries -> ~33, 10k -> ~67, 1M -> 100.
        reach = _clamp(100.0 * math.log10(project.estimated_beneficiaries + 1) / 6.0)
        reasons.append(f"Estimated reach of {project.estimated_beneficiaries:,} "
                       f"beneficiaries")
    else:
        reach = UNKNOWN[0]
        unknown.append("estimated_beneficiaries")

    blob = f"{project.project_name} {project.description} {project.category}".lower()
    if ngo and ngo.known_impact:
        blob += " " + ngo.known_impact.lower()

    deep = sum(1 for w in _TRANSFORMATIONAL if w in blob)
    shallow = sum(1 for w in _INCREMENTAL if w in blob)
    if deep or shallow:
        depth = _clamp(50.0 + 15.0 * deep - 12.0 * shallow)
        reasons.append(
            f"Impact reads as {'transformational' if deep > shallow else 'incremental'}"
            f" ({deep} transformational / {shallow} incremental signals)")
    else:
        depth = UNKNOWN[0]
        unknown.append("impact_depth")

    measurable = sum(1 for w in _MEASURABLE if w in blob)
    if measurable:
        measurability = _clamp(45.0 + 18.0 * measurable)
        reasons.append(f"{measurable} measurability signal(s) in the project record")
    elif ngo and ngo.known_impact:
        measurability = 60.0
        reasons.append("NGO publishes impact figures, indicating outcome tracking")
    else:
        measurability = UNKNOWN[0]
        unknown.append("measurability")

    score = reach * 0.40 + depth * 0.35 + measurability * 0.25
    return _clamp(score), (3 - len(unknown)) / 3.0, reasons, unknown


# ---------------------------------------------------------------------------
# 3. Cost effectiveness (15%)
#    PS2 PART 16: cost per beneficiary is the PRIMARY screening metric
# ---------------------------------------------------------------------------

def score_cost_effectiveness(
    project: CandidateProject, ngo: NGORecord | None,
    request: GenerateRequest, profile: dict
) -> DimensionResult:
    budget = project.requested_budget
    beneficiaries = project.estimated_beneficiaries

    missing = [f for f, v in (("requested_budget", budget),
                              ("estimated_beneficiaries", beneficiaries)) if not v]
    if missing:
        return (UNKNOWN[0], 0.0,
                ["Cost per beneficiary cannot be computed without "
                 f"{' and '.join(missing)}"], missing)

    # When both figures were derived by the pipeline rather than evidenced, the
    # beneficiary count came out of the budget via this same benchmark table, so
    # the ratio would just reproduce the median. That is circular: it carries no
    # information about this project and must not be presented as a measurement.
    derived = {"estimated_budget", "estimated_beneficiaries"}
    if derived.issubset(set(project.unknown_fields)):
        return (UNKNOWN[0], 0.0,
                ["Cost per beneficiary is not evidenced: both budget and "
                 "beneficiary count are pipeline estimates, so the ratio would "
                 "only restate the category benchmark"],
                ["requested_budget", "estimated_beneficiaries"])

    table = config.COST_BENCHMARKS.get(request.currency, config.COST_BENCHMARKS["INR"])
    category = project.category if project.category in table else "default"
    marks = table[category]
    per_beneficiary = budget / beneficiaries
    score = _interpolate(per_beneficiary, marks["good"], marks["median"], marks["poor"])

    symbol = config.CURRENCY_SYMBOLS.get(request.currency, "")
    verdict = ("well below" if per_beneficiary <= marks["good"]
               else "better than" if per_beneficiary <= marks["median"]
               else "above" if per_beneficiary <= marks["poor"] else "far above")
    reasons = [
        f"Cost per beneficiary {symbol}{per_beneficiary:,.0f} is {verdict} the "
        f"{category} benchmark (median {symbol}{marks['median']:,.0f})"
    ]
    # The inputs exist but at least one may itself be an estimate.
    confidence = 0.6 if "estimated_budget" in project.unknown_fields else 1.0
    if confidence < 1.0:
        reasons.append("Budget is an estimate, so this ratio is indicative only")

    # PS2 PART 17, "Unrealistic Impact Claims": efficiency an order of magnitude
    # better than the category's best benchmark is a data-quality signal, not a
    # genuinely exceptional project. Usually it means a lifetime or
    # organisation-wide reach figure has been attached to a single grant. Cap
    # the score and route it to a human rather than rewarding the anomaly.
    if per_beneficiary < marks["good"] / 10.0:
        score = min(score, 70.0)
        confidence = min(confidence, 0.3)
        reasons.append(
            f"Implausible efficiency: {symbol}{per_beneficiary:,.0f} per "
            f"beneficiary is over 10x better than the best {category} benchmark "
            f"({symbol}{marks['good']:,.0f}). Beneficiary count likely reflects "
            f"organisation-wide reach rather than this project. Verify before use.")
        return _clamp(score), confidence, reasons, ["beneficiary_count_verification"]

    return _clamp(score), confidence, reasons, []


# ---------------------------------------------------------------------------
# 4. Beneficiary relevance (10%)
#    Target group alignment 0-6, marginalised inclusion 0-4
# ---------------------------------------------------------------------------

_MARGINALISED = ("disabled", "persons with disabilities", "tribal", "sc/st",
                 "marginalized", "marginalised", "underserved", "underprivileged",
                 "single mothers", "orphan", "slum", "migrant", "rural")


def score_beneficiary_relevance(
    project: CandidateProject, ngo: NGORecord | None,
    request: GenerateRequest, profile: dict
) -> DimensionResult:
    groups = list(project.beneficiary_groups)
    if ngo:
        groups += ngo.target_beneficiaries
    if not groups:
        return (UNKNOWN[0], 0.0,
                ["No beneficiary groups stated for this project"],
                ["beneficiary_groups"])

    reasons: list[str] = []
    company_hits, company_matched = _overlap(config.TEMENOS_TARGET_GROUPS, groups)
    target = _clamp(100.0 * min(company_hits, 3) / 3.0)
    if company_matched:
        reasons.append("Serves company target groups: "
                       f"{', '.join(sorted(set(company_matched))[:4])}")
    else:
        reasons.append("Beneficiaries do not map to a documented company target group")

    requested = request.beneficiary_categories
    if requested:
        asked_hits, asked_matched = _overlap(requested, groups)
        requested_score = _clamp(100.0 * asked_hits / len(requested))
        reasons.append(
            f"Matches {asked_hits}/{len(requested)} requested categories"
            + (f" ({', '.join(asked_matched)})" if asked_matched else ""))
    else:
        requested_score = target
        reasons.append("No beneficiary categories requested; scored on company fit")

    marginal_hits, marginal_matched = _overlap(_MARGINALISED, groups)
    marginalised = _clamp(40.0 + 30.0 * min(marginal_hits, 2))
    if marginal_matched:
        reasons.append("Includes marginalised groups: "
                       f"{', '.join(sorted(set(marginal_matched))[:3])}")

    # PART 18 sub-weights: target alignment 6, marginalised inclusion 4.
    score = (target * 0.35 + requested_score * 0.25 + marginalised * 0.40)
    return _clamp(score), 1.0, reasons, []


# ---------------------------------------------------------------------------
# 5. Geographic need (10%)
#    Need signal, and existing company presence applied INVERSELY
# ---------------------------------------------------------------------------

def score_geographic_need(
    project: CandidateProject, ngo: NGORecord | None,
    request: GenerateRequest, profile: dict
) -> DimensionResult:
    reasons: list[str] = []
    unknown: list[str] = []
    region_blob = f"{project.region} {project.country}".lower()

    need = next((v for k, v in config.HIGH_NEED_REGIONS.items() if k in region_blob),
                None)
    if need is not None:
        need_score = _clamp(100.0 * need)
        reasons.append(f"{project.region} carries a "
                       f"{'high' if need >= 0.6 else 'moderate'} development-need signal")
    else:
        need_score = UNKNOWN[0]
        reasons.append(f"No district-level need index available for {project.region}")
        unknown.append("regional_need_index")

    presence = max((v for k, v in config.TEMENOS_PRESENCE.items() if k in region_blob),
                   default=0.0)
    # Inverse by design (PS2 PART 18 dimension 5): a region the company already
    # serves is a lower marginal need than a comparable region it does not.
    balance = _clamp(100.0 * (1.0 - presence))
    reasons.append(
        f"Company already active in {project.region}; new regions score higher "
        f"on marginal need" if presence >= 0.5
        else f"Limited existing company CSR presence in {project.region}")

    score = need_score * 0.60 + balance * 0.40
    return _clamp(score), (2 - len(unknown)) / 2.0, reasons, unknown


# ---------------------------------------------------------------------------
# 6. Feasibility (8%)
#    Plan clarity 0-3, timeline realism 0-3, risk mitigation 0-2
# ---------------------------------------------------------------------------

def score_feasibility(
    project: CandidateProject, ngo: NGORecord | None,
    request: GenerateRequest, profile: dict
) -> DimensionResult:
    reasons: list[str] = []
    unknown: list[str] = []

    description = (project.description or "").strip()
    if description:
        clarity = _clamp(35.0 + min(len(description), 400) / 400.0 * 55.0)
        reasons.append(f"Implementation description present ({len(description)} chars)")
    else:
        clarity = UNKNOWN[0]
        unknown.append("implementation_plan")

    duration = project.duration_months
    if duration:
        requested = request.project_duration or duration
        drift = abs(duration - requested) / max(requested, 1)
        timeline = _clamp(100.0 - 70.0 * min(drift, 1.0))
        if duration > 24:
            timeline = min(timeline, 65.0)
            reasons.append(f"{duration}-month duration exceeds the 24-month "
                           f"guideline for a single commitment")
        else:
            reasons.append(f"{duration}-month duration against a {requested}-month "
                           f"request")
    else:
        timeline = UNKNOWN[0]
        unknown.append("duration_months")

    if ngo and (ngo.risk_flags or ngo.risk_level):
        mitigation = 40.0 if ngo.risk_flags else 70.0
        reasons.append("Risk information available for the implementing partner")
    else:
        mitigation = UNKNOWN[0]
        unknown.append("risk_mitigation")

    score = clarity * 0.40 + timeline * 0.35 + mitigation * 0.25
    return _clamp(score), (3 - len(unknown)) / 3.0, reasons, unknown


# ---------------------------------------------------------------------------
# 7. NGO capability (7%)  -- experience 0-3, staff 0-2, operational capacity 0-2
# ---------------------------------------------------------------------------

def score_ngo_capability(
    project: CandidateProject, ngo: NGORecord | None,
    request: GenerateRequest, profile: dict
) -> DimensionResult:
    if ngo is None:
        return UNKNOWN[0], 0.0, ["No implementing partner identified"], ["ngo"]

    reasons: list[str] = []
    unknown: list[str] = []
    parts: list[float] = []

    if ngo.years_experience is not None:
        # 15+ years is a mature organisation; scale linearly below that.
        experience = _clamp(100.0 * min(ngo.years_experience, 15) / 15.0)
        parts.append(experience)
        reasons.append(f"{ngo.years_experience} years of operation")
        if request.ngo_experience and ngo.years_experience < request.ngo_experience:
            reasons.append(f"Below the {request.ngo_experience}-year minimum requested")
    else:
        unknown.append("years_experience")

    if ngo.staff_capacity is not None:
        parts.append(_clamp(100.0 * min(ngo.staff_capacity, 200) / 200.0))
        reasons.append(f"{ngo.staff_capacity} staff")
    else:
        unknown.append("staff_capacity")

    if ngo.past_project_count is not None:
        parts.append(_clamp(100.0 * min(ngo.past_project_count, 25) / 25.0))
        reasons.append(f"{ngo.past_project_count} past projects on record")
    else:
        unknown.append("past_project_count")

    # Published impact figures are weaker evidence of capacity than a headcount,
    # but they are evidence, so they carry a reduced score rather than none.
    if not parts and ngo.known_impact:
        parts.append(65.0)
        reasons.append(f"Published scale: {ngo.known_impact}")
        unknown = [u for u in unknown if u != "past_project_count"]

    if not parts:
        return (UNKNOWN[0], 0.0,
                ["No capability evidence found for this organisation"], unknown)

    score = sum(parts) / len(parts)
    confidence = (3 - len(unknown)) / 3.0
    return _clamp(score), max(confidence, 0.2), reasons, unknown


# ---------------------------------------------------------------------------
# 8. NGO credibility (5%) -- compliance 0-2, utilisation 0-2, financials 0-1
# ---------------------------------------------------------------------------

def score_ngo_credibility(
    project: CandidateProject, ngo: NGORecord | None,
    request: GenerateRequest, profile: dict
) -> DimensionResult:
    if ngo is None:
        return UNKNOWN[0], 0.0, ["No implementing partner identified"], ["ngo"]

    reasons: list[str] = []
    unknown: list[str] = []

    if ngo.compliance:
        held = [c for c in config.COMPLIANCE_CREDENTIALS if c in ngo.compliance]
        compliance = _clamp(100.0 * len(held) / len(config.COMPLIANCE_CREDENTIALS))
        reasons.append(f"Holds {', '.join(held)}")
        missing = [c for c in request.compliance_requirements
                   if c.upper().replace(" ", "") not in
                   {h.upper().replace("-", "").replace(" ", "") for h in held}
                   and c.upper().replace("-", "") not in
                   {h.upper().replace("-", "") for h in held}]
        if missing:
            reasons.append(f"No public evidence of requested: {', '.join(missing)}")
    else:
        compliance = UNKNOWN[0]
        unknown.append("compliance")

    if ngo.utilization_rate is not None:
        utilisation = _clamp(ngo.utilization_rate * 100.0
                             if ngo.utilization_rate <= 1 else ngo.utilization_rate)
        reasons.append(f"Past fund utilisation {utilisation:.0f}%")
    else:
        utilisation = UNKNOWN[0]
        unknown.append("utilization_rate")

    if ngo.audited_financials is not None:
        financials = 100.0 if ngo.audited_financials else 25.0
        reasons.append("Audited financials available" if ngo.audited_financials
                       else "Audited financials reported unavailable")
    else:
        financials = UNKNOWN[0]
        unknown.append("audited_financials")

    if ngo.transparency_rating:
        bump = {"gold": 12.0, "platinum": 15.0, "silver": 6.0,
                "bronze": 2.0, "high": 8.0}.get(ngo.transparency_rating.lower(), 0.0)
        if bump:
            reasons.append(f"{ngo.transparency_rating} transparency rating")
    else:
        bump = 0.0
        unknown.append("transparency_rating")

    score = compliance * 0.40 + utilisation * 0.40 + financials * 0.20 + bump
    return _clamp(score), (4 - len(unknown)) / 4.0, reasons, unknown


# ---------------------------------------------------------------------------
# 9. Risk (3%) -- PS2 PART 17 red-flag detection. Higher score = lower risk.
# ---------------------------------------------------------------------------

_RISK_SIGNALS = {
    "compliance": (("no csr-1", "expired", "not registered"), "HIGH"),
    "financial": (("negative net worth", "no audited"), "HIGH"),
    "fund_misuse": (("low utilization", "utilisation below"), "HIGH"),
    "governance": (("single-person control", "no board"), "MEDIUM"),
    "implementation": (("no similar past projects", "inexperienced"), "MEDIUM"),
    "reputation": (("controversy", "investigation", "fraud"), "MEDIUM"),
}


def score_risk(
    project: CandidateProject, ngo: NGORecord | None,
    request: GenerateRequest, profile: dict
) -> DimensionResult:
    reasons: list[str] = []
    unknown: list[str] = []
    flags: list[tuple[str, str]] = []

    if ngo is None:
        return UNKNOWN[0], 0.0, ["No implementing partner identified"], ["ngo"]

    blob = " ".join(ngo.risk_flags + [ngo.risk_level or ""]).lower()
    for name, (patterns, severity) in _RISK_SIGNALS.items():
        if any(p in blob for p in patterns):
            flags.append((name, severity))

    # Mandatory Indian CSR credentials missing is itself a compliance red flag.
    if ngo.compliance and "CSR-1" not in ngo.compliance and ngo.country == "India":
        flags.append(("compliance", "HIGH"))

    stated = (ngo.risk_level or "").strip().upper()
    if stated in ("LOW", "MEDIUM", "HIGH"):
        base = {"LOW": 100.0, "MEDIUM": 60.0, "HIGH": 0.0}[stated]
        reasons.append(f"Stated risk level: {stated}")
    elif ngo.compliance:
        # Full credentials with nothing adverse found is a genuine low-risk
        # signal, but weaker than an assessed rating.
        base = 75.0
        reasons.append("No adverse risk signals found; compliance evidence present")
    else:
        base = UNKNOWN[0]
        reasons.append("No risk assessment available for this organisation")
        unknown.append("risk_level")

    penalty = sum(config.RISK_SEVERITY[sev] * 12.0 for _, sev in flags)
    if flags:
        reasons.append("Red flags: "
                       + ", ".join(f"{n} ({s})" for n, s in flags))

    confidence = 0.0 if "risk_level" in unknown and not flags else \
        (1.0 if stated else 0.6)
    return _clamp(base - penalty), confidence, reasons, unknown


# ---------------------------------------------------------------------------
# 10. Sustainability (2%) -- post-funding plan 0-3, scalability 0-2
# ---------------------------------------------------------------------------

_SUSTAIN_WORDS = ("sustainab", "self-sustain", "outlast", "long-term",
                  "community-owned", "revenue", "endowment", "capacity building")
_SCALE_WORDS = ("scale", "scalable", "replicat", "expand", "roll out", "model")


def score_sustainability(
    project: CandidateProject, ngo: NGORecord | None,
    request: GenerateRequest, profile: dict
) -> DimensionResult:
    reasons: list[str] = []
    unknown: list[str] = []
    blob = f"{project.description} {project.project_name}".lower()
    if ngo:
        blob += " " + " ".join(ngo.focus_areas).lower()

    if ngo and ngo.sustainability_plan is not None:
        plan = 100.0 if ngo.sustainability_plan else 20.0
        reasons.append("Post-funding sustainability plan on record"
                       if ngo.sustainability_plan else "No sustainability plan found")
    elif any(w in blob for w in _SUSTAIN_WORDS):
        plan = 70.0
        reasons.append("Sustainability language present in the project record")
    else:
        plan = UNKNOWN[0]
        unknown.append("sustainability_plan")

    if ngo and ngo.scalability is not None:
        scale = 100.0 if ngo.scalability else 30.0
        reasons.append("Scalability documented" if ngo.scalability
                       else "Scalability reported as limited")
    elif any(w in blob for w in _SCALE_WORDS):
        scale = 70.0
        reasons.append("Scalability language present in the project record")
    else:
        scale = UNKNOWN[0]
        unknown.append("scalability")

    # PART 18 sub-weights: sustainability 3, scalability 2.
    score = plan * 0.60 + scale * 0.40
    return _clamp(score), (2 - len(unknown)) / 2.0, reasons, unknown


DIMENSION_FUNCTIONS = {
    "strategic_alignment": score_strategic_alignment,
    "social_impact": score_social_impact,
    "cost_effectiveness": score_cost_effectiveness,
    "beneficiary_relevance": score_beneficiary_relevance,
    "geographic_need": score_geographic_need,
    "feasibility": score_feasibility,
    "ngo_capability": score_ngo_capability,
    "ngo_credibility": score_ngo_credibility,
    "risk": score_risk,
    "sustainability": score_sustainability,
}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

# Fields we expect a complete proposal to evidence. Completeness is measured
# against this list and reported separately from the project's potential score.
EXPECTED_FIELDS = (
    "estimated_beneficiaries", "requested_budget", "duration_months",
    "sdgs", "beneficiary_groups", "description",
    "years_experience", "staff_capacity", "past_project_count",
    "compliance", "utilization_rate", "audited_financials",
    "transparency_rating", "sustainability_plan", "scalability", "risk_level",
)


def _completeness(project: CandidateProject, ngo: NGORecord | None) -> tuple[float, list[str]]:
    """Percentage of expected fields with real evidence, plus the missing names."""
    present: dict[str, object] = {
        "estimated_beneficiaries": project.estimated_beneficiaries,
        "requested_budget": project.requested_budget,
        "duration_months": project.duration_months,
        "sdgs": project.sdgs or None,
        "beneficiary_groups": project.beneficiary_groups or None,
        "description": project.description or None,
    }
    if ngo is not None:
        present.update({
            "years_experience": ngo.years_experience,
            "staff_capacity": ngo.staff_capacity,
            "past_project_count": ngo.past_project_count,
            "compliance": ngo.compliance or None,
            "utilization_rate": ngo.utilization_rate,
            "audited_financials": ngo.audited_financials,
            "transparency_rating": ngo.transparency_rating,
            "sustainability_plan": ngo.sustainability_plan,
            "scalability": ngo.scalability,
            "risk_level": ngo.risk_level,
        })
    # An estimate the pipeline generated is not evidence of the real value.
    for field in project.unknown_fields:
        if field in present:
            present[field] = None

    missing = [f for f in EXPECTED_FIELDS if present.get(f) is None]
    filled = len(EXPECTED_FIELDS) - len(missing)
    return round(100.0 * filled / len(EXPECTED_FIELDS), 1), missing


def recommendation_for(score: float) -> str:
    """PS2 PART 18 thresholds. Decision support only -- a human decides."""
    if score < config.THRESHOLD_REJECT:
        return "REJECT"
    if score <= config.THRESHOLD_RECOMMEND:
        return "REVIEW"
    return "FUND"


def score_project(
    project: CandidateProject,
    ngo: NGORecord | None,
    request: GenerateRequest,
    profile: dict,
) -> ProjectScore:
    """
    Run all ten dimensions and combine them. Pure function: same inputs always
    produce the same output, which is what makes a recommendation auditable.
    """
    dimensions: list[DimensionScore] = []
    weighted_total = 0.0
    weighted_confidence = 0.0
    total_weight = sum(config.SCORING_WEIGHTS.values())
    rca: list[str] = []
    unknown_inputs: set[str] = set()

    for name, weight in config.SCORING_WEIGHTS.items():
        score, confidence, reasons, unknown = DIMENSION_FUNCTIONS[name](
            project, ngo, request, profile
        )
        dimensions.append(DimensionScore(
            name=name, score=round(score, 2), weight=weight,
            confidence=round(confidence, 3), reasons=reasons,
            unknown_inputs=unknown,
        ))
        weighted_total += score * weight
        weighted_confidence += confidence * weight
        unknown_inputs.update(unknown)
        # Root-cause notes: the dimensions that actually moved the number.
        if reasons and (weight >= 10 or score >= 80 or score <= 40):
            rca.append(f"{name.replace('_', ' ').title()} "
                       f"({score:.0f}/100): {reasons[0]}")

    final = weighted_total / total_weight
    confidence = weighted_confidence / total_weight
    completeness, missing = _completeness(project, ngo)

    sources = list(dict.fromkeys(project.evidence_sources + ([ngo.source] if ngo else [])))

    return ProjectScore(
        project_id=project.project_id,
        project_name=project.project_name,
        category=project.category,
        region=project.region,
        estimated_beneficiaries=project.estimated_beneficiaries or 0,
        requested_budget=project.requested_budget or 0.0,
        **{d.name: d.score for d in dimensions},
        final_score=round(final, 2),
        confidence=round(confidence, 3),
        completeness=completeness,
        recommendation=recommendation_for(final),
        rca=rca,
        evidence_sources=sources,
        dimensions=dimensions,
        unknown_fields=sorted(unknown_inputs | set(missing)),
        ngo_id=project.ngo_id,
        derived_from_ngo=project.derived_from_ngo,
    )


def score_projects(
    projects: list[CandidateProject],
    ngos_by_id: dict[str, NGORecord],
    request: GenerateRequest,
    profile: dict,
) -> list[ProjectScore]:
    """Score every candidate, ranked. Ties break on project_id for stability."""
    scores = [
        score_project(p, ngos_by_id.get(p.ngo_id or ""), request, profile)
        for p in projects
    ]
    scores.sort(key=lambda s: (-s.final_score, s.project_id))
    return scores
