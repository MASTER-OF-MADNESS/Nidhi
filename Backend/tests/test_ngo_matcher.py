"""
Step 11 checks: NGO matching.

The rule that matters most: a hard eligibility failure is final. No amount of
keyword similarity or sector fit may resurrect an ineligible organisation.
"""

import config
import pytest

from engine.ngo_matcher import (
    build_comparison,
    check_eligibility,
    match_ngos_for_project,
    score_match,
)
from models.schemas import GenerateRequest, NGORecord, ProjectScore


def _project(category="education", region="Tamil Nadu") -> ProjectScore:
    return ProjectScore(project_id="P1", project_name="Education programme",
                        category=category, region=region, final_score=80.0)


def _request(**kwargs) -> GenerateRequest:
    base = dict(total_csr_budget=10_000_000, country="India",
                csr_sector="Education", compliance_requirements=["CSR-1", "12A"],
                ngo_experience=5)
    base.update(kwargs)
    return GenerateRequest(**base)


def _ngo(**kwargs) -> NGORecord:
    base = dict(ngo_id="N1", name="Test NGO", state="Tamil Nadu", country="India",
                geographic_coverage=["Tamil Nadu"], categories=["education"],
                focus_areas=["Education"], compliance=["CSR-1", "12A", "80G"],
                years_experience=10)
    base.update(kwargs)
    return NGORecord(**base)


# --- hard eligibility ------------------------------------------------------

def test_wrong_sector_is_ineligible():
    ok, failures = check_eligibility(
        _ngo(categories=["environment"], focus_areas=["Tree planting"]),
        _project(), _request())
    assert not ok and "expertise" in failures[0]


def test_wrong_geography_is_ineligible():
    ok, failures = check_eligibility(
        _ngo(state="Odisha", geographic_coverage=["Odisha"]),
        _project(region="Kerala"), _request())
    assert not ok and "coverage" in failures[0]


def test_missing_required_credential_is_ineligible():
    ok, failures = check_eligibility(
        _ngo(compliance=["80G"]), _project(), _request())
    assert not ok and "CSR-1" in failures[0]


def test_insufficient_experience_is_ineligible():
    ok, failures = check_eligibility(
        _ngo(years_experience=2), _project(), _request(ngo_experience=5))
    assert not ok and "below" in failures[0]


def test_unknown_fields_never_disqualify():
    """Absent evidence means unverified, not failed."""
    blank = NGORecord(ngo_id="N9", name="Undocumented Org")
    ok, failures = check_eligibility(blank, _project(), _request())
    assert ok, failures


def test_national_coverage_satisfies_any_region():
    ok, _ = check_eligibility(
        _ngo(state="All India", geographic_coverage=["All India"]),
        _project(region="Odisha"), _request())
    assert ok


# --- the hard-filter-beats-soft-score rule ---------------------------------

def test_hard_failure_beats_a_perfect_soft_profile():
    perfect_but_wrong_state = _ngo(
        ngo_id="N-PERFECT", name="Perfect But Elsewhere",
        state="Odisha", geographic_coverage=["Odisha"],
        compliance=["CSR-1", "12A", "80G", "FCRA"], transparency_rating="Gold",
        years_experience=30, temenos_partner=True,
        known_impact="500,000 students", similar_project_experience=True)
    eligible = _ngo(ngo_id="N-OK", name="Modest But Eligible",
                    state="Kerala", geographic_coverage=["Kerala"])

    result = match_ngos_for_project(
        _project(region="Kerala"), [perfect_but_wrong_state, eligible],
        _request(compliance_requirements=[], ngo_experience=0))

    matched = [m.ngo_name for m in result.matches]
    assert "Perfect But Elsewhere" not in matched, (
        "a hard geography failure must not be recoverable by a strong profile")
    assert "Modest But Eligible" in matched
    assert result.ineligible_count == 1
    assert "coverage" in result.ineligible_examples[0]["reason"]


# --- weighted scoring ------------------------------------------------------

def test_company_partner_outranks_an_equivalent_non_partner():
    partner = _ngo(ngo_id="N-P", name="Partner Org", temenos_partner=True,
                   temenos_relationship="DIRECT_CSR_PARTNER")
    stranger = _ngo(ngo_id="N-S", name="Stranger Org")

    result = match_ngos_for_project(_project(), [stranger, partner],
                                    _request(ngo_experience=0))
    assert result.matches[0].ngo_name == "Partner Org"
    assert result.matches[0].compatibility_score > result.matches[1].compatibility_score


def test_stars_are_score_over_twenty_capped_at_five():
    match = score_match(
        _ngo(temenos_partner=True, transparency_rating="Gold",
             compliance=["CSR-1", "12A", "80G", "FCRA"], years_experience=25,
             districts=["Chennai"], similar_project_experience=True,
             known_impact="10,000 children"),
        _project(region="Chennai"), _request(ngo_experience=0))
    assert match.star_rating <= 5.0
    assert match.star_rating == pytest.approx(
        round(min(match.compatibility_score / 20.0, 5.0) * 2) / 2)


def test_unknown_fields_surface_as_limitations():
    match = score_match(NGORecord(ngo_id="N9", name="Undocumented"),
                        _project(), _request(ngo_experience=0))
    assert match.limitations
    assert any("No public evidence" in l or "not documented" in l.lower()
               or "not published" in l.lower() for l in match.limitations)


def test_breakdown_covers_every_configured_weight():
    match = score_match(_ngo(), _project(), _request(ngo_experience=0))
    for dimension in config.NGO_MATCH_WEIGHTS:
        assert dimension in match.breakdown


def test_only_top_n_matches_are_returned():
    ngos = [_ngo(ngo_id=f"N{i}", name=f"Org {i}") for i in range(6)]
    result = match_ngos_for_project(_project(), ngos, _request(ngo_experience=0))
    assert len(result.matches) == config.NGO_MATCHES_PER_PROJECT


def test_ineligible_organisations_are_reported_with_reasons():
    ngos = [_ngo(ngo_id="N1", name="Fine"),
            _ngo(ngo_id="N2", name="Wrong sector", categories=["healthcare"])]
    result = match_ngos_for_project(_project(), ngos, _request(ngo_experience=0))
    assert result.ineligible_count == 1
    assert result.ineligible_examples[0]["ngo_name"] == "Wrong sector"
    assert result.ineligible_examples[0]["reason"]


def test_comparison_table_is_flat_and_complete():
    ngos = [_ngo(ngo_id="N1", name="A"), _ngo(ngo_id="N2", name="B")]
    recommendation = match_ngos_for_project(_project(), ngos, _request(ngo_experience=0))
    table = build_comparison([recommendation])

    assert len(table["rows"]) == len(recommendation.matches)
    assert table["rows"][0]["rank"] == 1
    assert table["weights"] == dict(config.NGO_MATCH_WEIGHTS)
