"""Step 2 checks: request validation and the UNKNOWN-is-not-zero contract."""

import pytest
from pydantic import ValidationError

from models.schemas import (
    GenerateRequest,
    NGORecord,
    ProjectScore,
    SSEEvent,
)


def test_full_request_round_trips(sample_request):
    restored = GenerateRequest.model_validate(sample_request.model_dump())
    assert restored == sample_request
    assert restored.states == ["Tamil Nadu", "Karnataka"]
    assert restored.currency == "INR"


def test_negative_budget_rejected():
    with pytest.raises(ValidationError):
        GenerateRequest(total_csr_budget=-1)


def test_max_below_min_rejected():
    with pytest.raises(ValidationError):
        GenerateRequest(
            total_csr_budget=1_000_000,
            min_project_funding=500_000,
            max_project_funding=100_000,
        )


def test_comma_separated_strings_become_lists():
    req = GenerateRequest(
        total_csr_budget=1_000_000,
        states="Tamil Nadu, Kerala ,, Odisha",
    )
    assert req.states == ["Tamil Nadu", "Kerala", "Odisha"]


def test_effective_max_defaults_to_whole_budget():
    req = GenerateRequest(total_csr_budget=5_000_000)
    assert req.effective_max() == 5_000_000


def test_unknown_ngo_fields_are_none_not_zero():
    """The core data-integrity contract: absent evidence is None, not 0/False."""
    ngo = NGORecord(ngo_id="NGO-X", name="Blank Org")
    assert ngo.years_experience is None
    assert ngo.utilization_rate is None
    assert ngo.audited_financials is None
    assert ngo.years_experience != 0
    assert ngo.audited_financials is not False

    unknown = ngo.unknown_fields()
    assert "years_experience" in unknown
    assert "utilization_rate" in unknown
    assert len(unknown) == 10


def test_score_and_confidence_are_independent_fields():
    score = ProjectScore(project_name="P", category="education", region="TN")
    assert hasattr(score, "confidence")
    assert hasattr(score, "completeness")
    assert score.confidence == 0.0 and score.final_score == 0.0


def test_sse_frame_format():
    frame = SSEEvent(section="complete", run_id="abc-123").to_sse()
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    assert '"run_id":"abc-123"' in frame
