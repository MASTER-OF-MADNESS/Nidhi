"""
NIDHI - Pydantic models for every request, response and internal pipeline type.

Design rule that runs through this whole module: a field that was not found in
the evidence is None, never 0 and never False. "Unknown" and "zero" are
different facts and the type system is the first place that distinction is
enforced.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EvidenceClass = Literal[
    "VERIFIED_OFFICIAL", "VERIFIED_SECONDARY", "INFERENCE", "UNKNOWN"
]
DataSource = Literal["TAVILY_PRIMARY", "MD_FALLBACK"]
Recommendation = Literal["FUND", "REVIEW", "REJECT"]
Currency = Literal["INR", "USD"]


# ---------------------------------------------------------------------------
# Auth / company
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    company_id: str | None = None
    company_name: str | None = None
    message: str = ""


class CompanyProfile(BaseModel):
    company_id: str
    display_name: str
    legal_name: str
    headquarters: str | None = None
    founded: int | None = None
    country: str | None = None
    default_currency: str = "INR"
    esg_pillars: list[str] = Field(default_factory=list)
    priority_themes: list[dict[str, Any]] = Field(default_factory=list)
    geographic_priorities: list[dict[str, Any]] = Field(default_factory=list)
    historical_projects: list[dict[str, Any]] = Field(default_factory=list)
    ngo_partners: list[dict[str, Any]] = Field(default_factory=list)
    sdg_alignment: list[int] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    """The 21 CSR-manager inputs, plus an explicit currency selector."""

    model_config = ConfigDict(populate_by_name=True)

    total_csr_budget: float = Field(gt=0)
    funding_type: str = ""
    project_duration: int = Field(default=12, ge=1, le=120)
    number_of_projects: int = Field(default=5, ge=1, le=50)
    min_project_funding: float = Field(default=0, ge=0)
    max_project_funding: float = Field(default=0, ge=0)

    country: str = "India"
    states: list[str] = Field(default_factory=list)
    districts: list[str] = Field(default_factory=list)

    csr_sector: str = ""
    csr_sub_sector: str = ""
    beneficiary_categories: list[str] = Field(default_factory=list)

    ngo_experience: int = Field(default=0, ge=0, le=100)
    similar_project_experience: bool = False
    geographic_capability: str = ""
    ngo_rating: str = ""
    compliance_requirements: list[str] = Field(default_factory=list)

    collaboration_type: str = ""
    employee_involvement: bool = False
    additional_comments: str = ""

    currency: Currency = "INR"

    @field_validator("states", "districts", "beneficiary_categories",
                     "compliance_requirements", mode="before")
    @classmethod
    def _coerce_list(cls, v: Any) -> list[str]:
        """Accept a comma-separated string as well as a list, and drop blanks."""
        if v is None:
            return []
        if isinstance(v, str):
            v = [p for p in v.split(",")]
        return [str(p).strip() for p in v if str(p).strip()]

    @field_validator("max_project_funding")
    @classmethod
    def _max_above_min(cls, v: float, info) -> float:
        lo = info.data.get("min_project_funding") or 0
        if v and lo and v < lo:
            raise ValueError(
                "max_project_funding must be >= min_project_funding"
            )
        return v

    def effective_max(self) -> float:
        """max_project_funding, defaulting to the whole budget when unset."""
        return self.max_project_funding or self.total_csr_budget


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

class EvidenceItem(BaseModel):
    """One traceable piece of evidence. Every claim NIDHI makes cites one."""

    content: str
    source: str
    evidence_class: EvidenceClass = "UNKNOWN"
    url: str | None = None
    label: str | None = None


class NGORecord(BaseModel):
    """
    An NGO as known to the system. Every optional field is `| None` on purpose:
    the knowledge base leaves most of them deliberately blank, and a blank means
    'not found', which must not be scored as a zero.
    """

    ngo_id: str
    name: str
    source: str = "MD"
    evidence_class: EvidenceClass = "VERIFIED_SECONDARY"

    location: str | None = None
    country: str | None = None
    state: str | None = None
    districts: list[str] = Field(default_factory=list)
    geographic_coverage: list[str] = Field(default_factory=list)

    focus_areas: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    target_beneficiaries: list[str] = Field(default_factory=list)

    compliance: list[str] = Field(default_factory=list)
    transparency_rating: str | None = None

    years_experience: int | None = None
    staff_capacity: int | None = None
    past_project_count: int | None = None
    known_impact: str | None = None
    utilization_rate: float | None = None
    audited_financials: bool | None = None

    similar_project_experience: bool | None = None
    collaboration_types: list[str] = Field(default_factory=list)
    employee_involvement: bool | None = None

    sustainability_plan: bool | None = None
    scalability: bool | None = None
    risk_flags: list[str] = Field(default_factory=list)
    risk_level: str | None = None

    temenos_partner: bool = False
    temenos_relationship: str | None = None
    rank: int | None = None
    url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    def unknown_fields(self) -> list[str]:
        """Names of the scoring-relevant fields with no evidence behind them."""
        checks = {
            "years_experience": self.years_experience,
            "staff_capacity": self.staff_capacity,
            "past_project_count": self.past_project_count,
            "utilization_rate": self.utilization_rate,
            "audited_financials": self.audited_financials,
            "districts": self.districts or None,
            "transparency_rating": self.transparency_rating,
            "sustainability_plan": self.sustainability_plan,
            "scalability": self.scalability,
            "similar_project_experience": self.similar_project_experience,
        }
        return sorted(k for k, v in checks.items() if v is None)


class EvidencePack(BaseModel):
    """Everything the reasoning layers are allowed to look at, with provenance."""

    company_id: str
    data_source: DataSource
    company_profile: dict[str, Any] = Field(default_factory=dict)
    ngos: list[NGORecord] = Field(default_factory=list)
    historical_projects: list[dict[str, Any]] = Field(default_factory=list)
    user_input: dict[str, Any] = Field(default_factory=dict)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Candidate projects and scores
# ---------------------------------------------------------------------------

class CandidateProject(BaseModel):
    """
    A candidate intervention assembled from evidence -- an NGO's demonstrated
    capability aimed at the requested sector, region and beneficiaries.

    It is a *proposal to consider*, not a record of an existing funded project.
    `derived_from_ngo` and `evidence_class` keep that provenance visible.
    """

    project_id: str
    project_name: str
    category: str
    sub_category: str | None = None
    region: str
    country: str = "India"
    description: str = ""

    estimated_beneficiaries: int | None = None
    requested_budget: float | None = None
    duration_months: int | None = None

    derived_from_ngo: str | None = None
    ngo_id: str | None = None
    beneficiary_groups: list[str] = Field(default_factory=list)
    sdgs: list[int] = Field(default_factory=list)

    alignment_hint: float | None = Field(default=None, ge=0, le=10)
    evidence_class: EvidenceClass = "INFERENCE"
    evidence_sources: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    extraction_method: str = "gemini"


class DimensionScore(BaseModel):
    """One of the ten dimensions: its score, how sure we are, and why."""

    name: str
    score: float = Field(ge=0, le=100)
    weight: float
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    unknown_inputs: list[str] = Field(default_factory=list)


class ProjectScore(BaseModel):
    project_id: str = ""
    project_name: str
    category: str
    region: str
    estimated_beneficiaries: int = 0
    requested_budget: float = 0.0

    strategic_alignment: float = 0.0
    social_impact: float = 0.0
    cost_effectiveness: float = 0.0
    beneficiary_relevance: float = 0.0
    geographic_need: float = 0.0
    feasibility: float = 0.0
    ngo_capability: float = 0.0
    ngo_credibility: float = 0.0
    risk: float = 0.0
    sustainability: float = 0.0

    final_score: float = 0.0
    confidence: float = 0.0
    completeness: float = 0.0
    recommendation: Recommendation = "REVIEW"

    rca: list[str] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)
    dimensions: list[DimensionScore] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    ngo_id: str | None = None
    derived_from_ngo: str | None = None


# ---------------------------------------------------------------------------
# Optimizer output
# ---------------------------------------------------------------------------

class AllocationResult(BaseModel):
    project_id: str
    project_name: str
    category: str
    region: str
    allocated_amount: float
    percentage_of_budget: float
    final_score: float
    confidence: float
    ngo_id: str | None = None


class ExcludedProject(BaseModel):
    project_id: str
    project_name: str
    final_score: float
    reason: str
    binding_constraint: str | None = None
    what_would_change_it: str = ""


class ConstraintReport(BaseModel):
    satisfied: bool = True
    status: str = "OPTIMAL"
    solver: str = "CP-SAT"
    relaxations_applied: list[str] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class OptimizationOutcome(BaseModel):
    selected: list[AllocationResult] = Field(default_factory=list)
    excluded: list[ExcludedProject] = Field(default_factory=list)
    constraint_report: ConstraintReport = Field(default_factory=ConstraintReport)
    total_allocated: float = 0.0
    budget_utilisation: float = 0.0


# ---------------------------------------------------------------------------
# NGO matching
# ---------------------------------------------------------------------------

class NGOMatch(BaseModel):
    ngo_name: str
    compatibility_score: float = Field(ge=0, le=100)
    star_rating: float = Field(ge=0, le=5)
    strengths: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evidence_source: str = "MD"
    temenos_partner: bool = False
    ngo_id: str | None = None
    project_id: str | None = None
    breakdown: dict[str, float] = Field(default_factory=dict)


class NGORecommendation(BaseModel):
    project_id: str
    project_name: str
    matches: list[NGOMatch] = Field(default_factory=list)
    ineligible_count: int = 0
    ineligible_examples: list[dict[str, str]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Review flags
# ---------------------------------------------------------------------------

class HumanReviewFlag(BaseModel):
    severity: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    category: str
    subject: str
    detail: str
    suggested_action: str = ""


# ---------------------------------------------------------------------------
# Run log (SQLite audit trail)
# ---------------------------------------------------------------------------

class RunLog(BaseModel):
    run_id: str
    timestamp: str
    company_id: str
    data_source: str
    projects_evaluated: int = 0
    projects_funded: int = 0
    total_allocated: float = 0.0
    constraints_satisfied: bool = True


class RunLogDetail(RunLog):
    input_parameters: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Streaming envelope
# ---------------------------------------------------------------------------

SectionName = Literal[
    "progress", "overview", "scorecard", "fund_split", "ngo_recommendations",
    "ngo_comparison", "strategic_insights", "human_review_flags",
    "warning", "complete",
]


class SSEEvent(BaseModel):
    """One `data: {...}` frame on the wire."""

    section: SectionName
    content: Any = None
    project_index: int | None = None
    step: str | None = None
    status: str | None = None
    source: str | None = None
    run_id: str | None = None

    def to_sse(self) -> str:
        payload = self.model_dump_json(exclude_none=True)
        return f"data: {payload}\n\n"


class HealthResponse(BaseModel):
    status: str = "ok"
    gemini: bool = False
    tavily: bool = False
    database: bool = False
    company_id: str = ""
    model: str = ""
    detail: dict[str, str] = Field(default_factory=dict)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
