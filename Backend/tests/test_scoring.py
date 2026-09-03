"""
Step 9 checks: the deterministic scorer.

The golden test is test_completely_blank_project_scores_neutral_not_zero -- it
is the whole reason this engine exists rather than letting the model score.
"""

import config
import pytest

from engine import scoring_engine as se
from models.schemas import CandidateProject, GenerateRequest, NGORecord
from retrieval import md_parser


@pytest.fixture(scope="module")
def profile():
    return md_parser.load_company_profile()


def _project(**kwargs) -> CandidateProject:
    base = dict(
        project_id="CAND-01", project_name="Test project",
        category="education", region="Tamil Nadu", country="India",
    )
    base.update(kwargs)
    return CandidateProject(**base)


def _strong_ngo() -> NGORecord:
    return NGORecord(
        ngo_id="NGO-STRONG", name="Strong Org", state="Odisha", country="India",
        focus_areas=["Education", "Skill development"],
        categories=["education"],
        target_beneficiaries=["children", "women", "underserved", "tribal"],
        compliance=["CSR-1", "12A", "80G", "FCRA"], transparency_rating="Gold",
        years_experience=18, staff_capacity=180, past_project_count=30,
        utilization_rate=0.94, audited_financials=True,
        sustainability_plan=True, scalability=True, risk_level="LOW",
        known_impact="12,000 students supported",
    )


# --- the core contract -----------------------------------------------------

def test_completely_blank_project_scores_neutral_not_zero(profile):
    """A project with no evidence at all must land at the neutral prior with
    near-zero confidence -- never 0, which would read as 'assessed and found
    terrible' rather than 'we do not know'."""
    blank = _project(project_name="Nothing known", description="",
                     category="default", region="Unknown Region",
                     country="Nowhere")
    score = se.score_project(blank, None,
                             GenerateRequest(total_csr_budget=1_000_000), profile)

    assert score.final_score > 30, "a blank project must not be scored as bad"
    assert 45 <= score.final_score <= 55, score.final_score
    assert score.confidence < 0.10
    assert score.completeness == 0.0
    assert score.unknown_fields
    # Every dimension either has evidence or sits exactly on the prior.
    for dimension in score.dimensions:
        if dimension.confidence == 0.0:
            assert dimension.score == config.NEUTRAL_PRIOR, dimension.name


def test_confidence_is_graded_by_how_much_is_known(profile):
    """Knowing the sector and region is partial evidence and must raise
    confidence above the blank case without inventing certainty."""
    request = GenerateRequest(total_csr_budget=1_000_000)
    blank = se.score_project(
        _project(category="default", region="Unknown Region", country="Nowhere"),
        None, request, profile)
    partial = se.score_project(
        _project(category="education", region="Tamil Nadu"), None, request, profile)

    assert blank.confidence < partial.confidence < 0.5
    # Both still sit near the prior: partial knowledge is not a good score.
    assert abs(blank.final_score - partial.final_score) < 10


def test_blank_and_zero_are_distinguishable(profile):
    """Zero beneficiaries evidenced vs. no evidence must not score the same."""
    request = GenerateRequest(total_csr_budget=1_000_000)
    unknown = se.score_social_impact(_project(), None, request, profile)
    known_small = se.score_social_impact(
        _project(estimated_beneficiaries=50), None, request, profile)

    assert unknown[1] < known_small[1]        # lower confidence when unknown
    assert "estimated_beneficiaries" in unknown[3]
    assert "estimated_beneficiaries" not in known_small[3]


def test_a_well_evidenced_strong_project_scores_highly(profile):
    request = GenerateRequest(
        total_csr_budget=20_000_000, min_project_funding=1_000_000,
        max_project_funding=6_000_000, states=["Odisha"], csr_sector="Education",
        beneficiary_categories=["Children", "Women"], ngo_experience=5,
        compliance_requirements=["CSR-1", "12A", "80G"], project_duration=18,
    )
    project = _project(
        project_name="Girls education and skills programme",
        description=("Structured 18-month programme delivering school retention "
                     "and skill training with tracked learning outcomes, "
                     "measured against baseline assessment, designed to be "
                     "community-owned and scalable to further districts."),
        region="Odisha", estimated_beneficiaries=4_000,
        requested_budget=4_000_000, duration_months=18,
        beneficiary_groups=["children", "women", "tribal"],
        sdgs=[4, 5, 10],
    )
    score = se.score_project(project, _strong_ngo(), request, profile)

    assert score.final_score > 75, score.final_score
    assert score.recommendation == "FUND"
    assert score.confidence > 0.85
    assert score.completeness > 85


# --- determinism and weighting --------------------------------------------

def test_scoring_is_deterministic(profile):
    request = GenerateRequest(total_csr_budget=5_000_000)
    project = _project(estimated_beneficiaries=1000, requested_budget=500_000)
    runs = [se.score_project(project, _strong_ngo(), request, profile).final_score
            for _ in range(5)]
    assert len(set(runs)) == 1


def test_final_score_is_the_configured_weighted_mean(profile):
    request = GenerateRequest(total_csr_budget=5_000_000)
    score = se.score_project(
        _project(estimated_beneficiaries=800, requested_budget=400_000),
        _strong_ngo(), request, profile)

    expected = sum(d.score * d.weight for d in score.dimensions) / \
        sum(config.SCORING_WEIGHTS.values())
    assert score.final_score == pytest.approx(expected, abs=0.01)
    assert len(score.dimensions) == 10
    assert {d.name for d in score.dimensions} == set(config.SCORING_WEIGHTS)


def test_thresholds_match_the_specification():
    assert se.recommendation_for(59.9) == "REJECT"
    assert se.recommendation_for(60.0) == "REVIEW"
    assert se.recommendation_for(75.0) == "REVIEW"
    assert se.recommendation_for(75.1) == "FUND"


# --- per-dimension behaviour ----------------------------------------------

def test_geographic_need_penalises_existing_company_presence(profile):
    """Dimension 5 is inverse by design: new regions have higher marginal need."""
    request = GenerateRequest(total_csr_budget=1_000_000)
    established = se.score_geographic_need(
        _project(region="Chennai"), None, request, profile)[0]
    newer = se.score_geographic_need(
        _project(region="Odisha"), None, request, profile)[0]
    assert newer > established


def test_strategic_alignment_ranks_tier_a_above_tier_c(profile):
    request = GenerateRequest(total_csr_budget=1_000_000)
    tier_a = se.score_strategic_alignment(
        _project(category="youth development", description="youth development"),
        None, request, profile)[0]
    tier_c = se.score_strategic_alignment(
        _project(category="financial_inclusion", description="financial inclusion"),
        None, request, profile)[0]
    assert tier_a > tier_c


def test_missing_compliance_lowers_credibility_confidence(profile):
    request = GenerateRequest(total_csr_budget=1_000_000,
                              compliance_requirements=["CSR-1", "12A"])
    bare = NGORecord(ngo_id="N1", name="Bare Org")
    full = _strong_ngo()

    bare_score, bare_conf, _, bare_unknown = se.score_ngo_credibility(
        _project(), bare, request, profile)
    full_score, full_conf, _, _ = se.score_ngo_credibility(
        _project(), full, request, profile)

    assert bare_score == config.NEUTRAL_PRIOR   # neutral, not zero
    assert bare_conf == 0.0
    assert "compliance" in bare_unknown
    assert full_score > bare_score and full_conf > bare_conf


def test_capability_uses_published_scale_when_headcount_is_unknown(profile):
    """Published impact is weaker evidence than a headcount, but it is evidence."""
    request = GenerateRequest(total_csr_budget=1_000_000)
    nothing = NGORecord(ngo_id="N1", name="Nothing Known")
    published = NGORecord(ngo_id="N2", name="Publishes Scale",
                          known_impact="12,000 students supported")

    blank_score, blank_conf, _, _ = se.score_ngo_capability(
        _project(), nothing, request, profile)
    pub_score, pub_conf, _, _ = se.score_ngo_capability(
        _project(), published, request, profile)

    assert blank_conf == 0.0 and blank_score == config.NEUTRAL_PRIOR
    assert pub_score > blank_score and pub_conf > 0


def test_high_risk_ngo_scores_low_on_risk_dimension(profile):
    request = GenerateRequest(total_csr_budget=1_000_000)
    risky = NGORecord(ngo_id="N1", name="Risky", risk_level="HIGH",
                      risk_flags=["controversy reported"])
    safe = NGORecord(ngo_id="N2", name="Safe", risk_level="LOW",
                     compliance=["CSR-1", "12A", "80G"])

    risky_score = se.score_risk(_project(), risky, request, profile)[0]
    safe_score = se.score_risk(_project(), safe, request, profile)[0]
    assert risky_score < safe_score
    assert safe_score > 90


# --- anomaly guards (PS2 PART 17) -----------------------------------------

def test_implausible_cost_efficiency_is_capped_and_flagged(profile):
    """A lifetime reach figure attached to one grant must not score 100."""
    request = GenerateRequest(total_csr_budget=20_000_000, currency="INR")
    absurd = _project(estimated_beneficiaries=650_000, requested_budget=4_000_000)
    score, confidence, reasons, unknown = se.score_cost_effectiveness(
        absurd, None, request, profile)

    assert score <= 70
    assert confidence <= 0.3
    assert any("Implausible efficiency" in r for r in reasons)
    assert "beneficiary_count_verification" in unknown


def test_circular_cost_ratio_is_not_presented_as_a_measurement(profile):
    """When both figures are pipeline estimates the ratio carries no signal."""
    request = GenerateRequest(total_csr_budget=20_000_000, currency="INR")
    derived = _project(
        estimated_beneficiaries=666, requested_budget=4_000_000,
        unknown_fields=["estimated_budget", "estimated_beneficiaries"])
    score, confidence, reasons, _ = se.score_cost_effectiveness(
        derived, None, request, profile)

    assert score == config.NEUTRAL_PRIOR
    assert confidence == 0.0
    assert "not evidenced" in reasons[0]


def test_genuine_cost_ratio_is_scored_normally(profile):
    request = GenerateRequest(total_csr_budget=20_000_000, currency="INR")
    # 4,000 per beneficiary sits between the education good (2,000) and
    # median (6,000) anchors, so it should land in the upper band, not at 100.
    good = _project(estimated_beneficiaries=1_000, requested_budget=4_000_000)
    score, confidence, _, unknown = se.score_cost_effectiveness(
        good, None, request, profile)
    assert 60 < score < 100, score
    assert confidence == 1.0 and not unknown

    # Hitting the "good" anchor exactly is a full score, by design.
    at_anchor = _project(estimated_beneficiaries=2_000, requested_budget=4_000_000)
    assert se.score_cost_effectiveness(at_anchor, None, request, profile)[0] == 100.0


def test_currency_changes_the_benchmark_applied(profile):
    """The same numeric ratio means different things in INR and USD."""
    # 3,000 per beneficiary: strong against the INR education benchmark
    # (good 2,000) but far above the USD one (poor 216).
    project = _project(estimated_beneficiaries=20, requested_budget=60_000)
    inr = se.score_cost_effectiveness(
        project, None, GenerateRequest(total_csr_budget=1e7, currency="INR"), profile)[0]
    usd = se.score_cost_effectiveness(
        project, None, GenerateRequest(total_csr_budget=1e7, currency="USD"), profile)[0]
    assert inr > 80, inr
    assert usd < 20, usd
