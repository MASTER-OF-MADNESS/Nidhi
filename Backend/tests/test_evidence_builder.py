"""Step 7 checks: the evidence pack is complete, tagged, and honest about source."""

import asyncio

import config

from engine import evidence_builder as eb
from retrieval import tavily_search as ts
from tests.test_tavily import SAMPLE, FakeClient


def _pack(request):
    return asyncio.run(eb.build_evidence_pack(request))


def test_falls_back_to_md_when_tavily_has_no_key(sample_request, monkeypatch):
    monkeypatch.setattr(config, "TAVILY_API_KEY", "")
    pack = _pack(sample_request)

    assert pack.data_source == config.DATA_SOURCE_MD
    assert len(pack.ngos) >= 10
    assert any("not configured" in w for w in pack.warnings)


def test_uses_tavily_when_it_returns_enough(sample_request, monkeypatch):
    rich = {"results": SAMPLE["results"] + [
        {"title": "Pratham Education Foundation", "url": "https://pratham.org",
         "content": "Education NGO working across India. 12A, 80G, CSR-1.",
         "score": 0.9},
    ]}
    monkeypatch.setattr(ts, "_client", lambda: FakeClient(payload=rich))
    pack = _pack(sample_request)

    assert pack.data_source == config.DATA_SOURCE_TAVILY
    assert any(n.source == "TAVILY" for n in pack.ngos)


def test_thin_tavily_result_falls_back_to_md(sample_request, monkeypatch):
    thin = {"results": [SAMPLE["results"][0]]}
    monkeypatch.setattr(ts, "_client", lambda: FakeClient(payload=thin))
    pack = _pack(sample_request)

    assert pack.data_source == config.DATA_SOURCE_MD
    assert any("Falling back" in w for w in pack.warnings)


def test_company_profile_always_comes_from_the_knowledge_base(
    sample_request, monkeypatch
):
    """Temenos' own priorities are never sourced from a live web search."""
    monkeypatch.setattr(ts, "_client", lambda: FakeClient(payload=SAMPLE))
    pack = _pack(sample_request)
    assert pack.company_profile["source_file"].endswith(".md")
    assert len(pack.company_profile["esg_pillars"]) == 6


def test_every_evidence_item_carries_a_class(sample_request):
    pack = _pack(sample_request)
    assert pack.evidence_items
    valid = {config.EVIDENCE_VERIFIED_OFFICIAL, config.EVIDENCE_VERIFIED_SECONDARY,
             config.EVIDENCE_INFERENCE, config.EVIDENCE_UNKNOWN}
    assert all(item.evidence_class in valid for item in pack.evidence_items)


def test_missing_data_is_recorded_as_unknown_evidence(sample_request):
    """Gaps are evidence too -- they drive the confidence score and review flags."""
    pack = _pack(sample_request)
    summary = eb.evidence_summary(pack)
    assert summary.get(config.EVIDENCE_UNKNOWN, 0) > 0
    assert any(i.label and i.label.startswith("ngo_gaps:")
               for i in pack.evidence_items)


def test_temenos_partners_are_retained_alongside_web_results(
    sample_request, monkeypatch
):
    rich = {"results": SAMPLE["results"] * 2}
    monkeypatch.setattr(ts, "_client", lambda: FakeClient(payload=rich))
    pack = _pack(sample_request)
    assert any(n.temenos_partner for n in pack.ngos)


def test_prompt_context_marks_gaps_explicitly(sample_request):
    context = eb.to_prompt_context(_pack(sample_request))
    assert "## CSR MANAGER REQUIREMENTS" in context
    assert "## RETRIEVED NGOs" in context
    assert "UNKNOWN" in context
    assert "NO EVIDENCE FOUND FOR:" in context


def test_prompt_context_is_bounded(sample_request):
    pack = _pack(sample_request)
    short = eb.to_prompt_context(pack, max_ngos=2)
    long = eb.to_prompt_context(pack, max_ngos=20)
    assert len(short) < len(long)
