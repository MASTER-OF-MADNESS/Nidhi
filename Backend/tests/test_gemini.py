"""
Step 8 checks: extraction, the anchoring guard, and the rule-based fallback.

The contract under test: Gemini classifies, never scores, and never invents an
organisation that was not retrieved.
"""

import asyncio
import json

import config
import pytest

from engine import evidence_builder as eb, gemini_client as gc
from retrieval import tavily_search as ts


@pytest.fixture
def pack(sample_request, monkeypatch):
    monkeypatch.setattr(config, "TAVILY_API_KEY", "")
    return asyncio.run(eb.build_evidence_pack(sample_request))


def _fake_response(payload: dict):
    async def _gen(prompt, json_schema=None):
        return json.dumps(payload)
    return _gen


# --- fallback --------------------------------------------------------------

def test_rule_based_fallback_produces_candidates(pack, sample_request, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    candidates, warnings = asyncio.run(gc.extract_candidates(pack, sample_request))

    assert len(candidates) >= 3
    assert all(c.extraction_method == "rule_based" for c in candidates)
    assert any("rule-based" in w for w in warnings)


def test_fallback_candidates_are_anchored_to_real_ngos(pack, sample_request, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    candidates, _ = asyncio.run(gc.extract_candidates(pack, sample_request))
    known = {n.name for n in pack.ngos}
    assert all(c.derived_from_ngo in known for c in candidates)


def test_fallback_respects_the_funding_range(pack, sample_request, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    candidates, _ = asyncio.run(gc.extract_candidates(pack, sample_request))
    for c in candidates:
        assert sample_request.min_project_funding <= c.requested_budget \
               <= sample_request.max_project_funding


def test_fallback_marks_its_estimates_as_uncertain(pack, sample_request, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    candidates, _ = asyncio.run(gc.extract_candidates(pack, sample_request))
    assert "estimated_budget" in candidates[0].unknown_fields
    assert "estimated_beneficiaries" in candidates[0].unknown_fields


# --- model path ------------------------------------------------------------

def test_model_output_is_accepted_when_anchored(pack, sample_request, monkeypatch):
    real_ngo = pack.ngos[0]
    monkeypatch.setattr(gc, "_generate", _fake_response({"projects": [{
        "project_name": "Digital classrooms in Chennai",
        "category": "Education", "region": "Tamil Nadu",
        "derived_from_ngo": real_ngo.name, "ngo_id": real_ngo.ngo_id,
        "estimated_beneficiaries": 4200, "estimated_budget": 3_000_000,
        "alignment_score": 8.5, "sdgs": [4, 10], "uncertain_fields": [],
    }]}))
    candidates, warnings = asyncio.run(gc.extract_candidates(pack, sample_request))

    assert len(candidates) == 1
    c = candidates[0]
    assert c.extraction_method == "gemini"
    assert c.estimated_beneficiaries == 4200
    assert c.alignment_hint == 8.5
    assert c.sdgs == [4, 10]
    assert not warnings


def test_invented_ngo_is_discarded(pack, sample_request, monkeypatch):
    """A model that hallucinates an organisation must not reach the scorer."""
    monkeypatch.setattr(gc, "_generate", _fake_response({"projects": [{
        "project_name": "Ghost project", "category": "Education",
        "region": "Tamil Nadu", "derived_from_ngo": "Totally Made Up Foundation",
    }]}))
    candidates, warnings = asyncio.run(gc.extract_candidates(pack, sample_request))

    assert all(c.derived_from_ngo != "Totally Made Up Foundation" for c in candidates)
    assert candidates                       # fell back rather than returning nothing
    assert all(c.extraction_method == "rule_based" for c in candidates)


def test_zero_beneficiaries_is_treated_as_unknown(pack, sample_request, monkeypatch):
    """0 from the model means 'no evidence', not 'helps nobody'."""
    real_ngo = pack.ngos[0]
    monkeypatch.setattr(gc, "_generate", _fake_response({"projects": [{
        "project_name": "P", "category": "Education", "region": "Tamil Nadu",
        "derived_from_ngo": real_ngo.name,
        "estimated_beneficiaries": 0, "estimated_budget": 2_000_000,
    }]}))
    candidates, _ = asyncio.run(gc.extract_candidates(pack, sample_request))

    assert candidates[0].estimated_beneficiaries is None
    assert "estimated_beneficiaries" in candidates[0].unknown_fields


def test_model_budget_is_clamped_to_the_requested_range(pack, sample_request, monkeypatch):
    real_ngo = pack.ngos[0]
    monkeypatch.setattr(gc, "_generate", _fake_response({"projects": [{
        "project_name": "P", "category": "Education", "region": "Tamil Nadu",
        "derived_from_ngo": real_ngo.name, "estimated_budget": 999_000_000,
    }]}))
    candidates, _ = asyncio.run(gc.extract_candidates(pack, sample_request))
    assert candidates[0].requested_budget == sample_request.max_project_funding


def test_malformed_model_output_falls_back(pack, sample_request, monkeypatch):
    async def _garbage(prompt, json_schema=None):
        return "I'm afraid I can't do that."
    monkeypatch.setattr(gc, "_generate", _garbage)
    candidates, warnings = asyncio.run(gc.extract_candidates(pack, sample_request))
    assert candidates
    assert any("rule-based" in w for w in warnings)


def test_json_is_recovered_from_a_code_fence():
    parsed = gc._extract_json('```json\n{"projects": []}\n```')
    assert parsed == {"projects": []}


def test_prompt_forbids_scoring():
    assert "must NOT assign final scores" in gc.EXTRACTION_SYSTEM_PROMPT


# --- live ------------------------------------------------------------------

@pytest.mark.live
def test_live_gemini_extraction(pack, sample_request):
    if not config.GEMINI_API_KEY:
        pytest.skip("GEMINI_API_KEY not configured in .env")

    ok, detail = asyncio.run(gc.health_check())
    if not ok:
        # A free-tier key allows 20 requests a day. An exhausted quota is a
        # plan limit, not a code regression -- skip rather than fail, but say so.
        pytest.skip(f"Gemini unavailable ({detail}). If this is a quota error, "
                    f"the daily free-tier limit is spent; it resets in ~24h.")

    candidates, warnings = asyncio.run(gc.extract_candidates(pack, sample_request))
    assert candidates, "extraction must always return candidates"
    assert any(c.extraction_method == "gemini" for c in candidates), warnings


@pytest.mark.live
def test_live_run_survives_quota_exhaustion(pack, sample_request):
    """Whatever the model does, a run must still produce candidates."""
    candidates, _ = asyncio.run(gc.extract_candidates(pack, sample_request))
    assert len(candidates) >= 3
    assert all(c.derived_from_ngo for c in candidates)
