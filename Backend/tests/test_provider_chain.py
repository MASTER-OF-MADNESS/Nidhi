"""
The Gemini -> xAI (Grok) -> rule-based fallback chain.

Grok cannot be exercised live (the key has no credits), so the transport is
tested against a stubbed HTTP layer and the chain against stubbed providers.
"""

import asyncio
import json

import config
import httpx
import pytest

from engine import evidence_builder as eb, gemini_client as gc, xai_client


@pytest.fixture
def pack(sample_request, monkeypatch):
    monkeypatch.setattr(config, "TAVILY_API_KEY", "")
    return asyncio.run(eb.build_evidence_pack(sample_request))


@pytest.fixture(autouse=True)
def reset_provider():
    gc.last_provider = None
    yield
    gc.last_provider = None


# --- chain ordering --------------------------------------------------------

def test_gemini_is_preferred_when_it_works(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "XAI_API_KEY", "x")

    async def gemini_ok(prompt, json_schema=None): return "from-gemini"
    async def xai_should_not_run(prompt, json_schema=None, temperature=0.4):
        raise AssertionError("xAI must not be called while Gemini works")

    monkeypatch.setattr(gc, "_generate_gemini", gemini_ok)
    monkeypatch.setattr(xai_client, "generate", xai_should_not_run)

    assert asyncio.run(gc._generate("hi")) == "from-gemini"
    assert gc.last_provider == "gemini"


def test_falls_through_to_grok_when_gemini_fails(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "XAI_API_KEY", "x")

    async def gemini_dead(prompt, json_schema=None): return None
    async def xai_ok(prompt, json_schema=None, temperature=0.4): return "from-grok"

    monkeypatch.setattr(gc, "_generate_gemini", gemini_dead)
    monkeypatch.setattr(xai_client, "generate", xai_ok)

    assert asyncio.run(gc._generate("hi")) == "from-grok"
    assert gc.last_provider == "xai"


def test_returns_none_when_every_provider_fails(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "XAI_API_KEY", "x")

    async def dead(prompt, json_schema=None): return None
    async def dead_xai(prompt, json_schema=None, temperature=0.4): return None

    monkeypatch.setattr(gc, "_generate_gemini", dead)
    monkeypatch.setattr(xai_client, "generate", dead_xai)

    assert asyncio.run(gc._generate("hi")) is None
    assert gc.last_provider is None


def test_unconfigured_provider_is_skipped(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "XAI_API_KEY", "x")
    assert gc._enabled_providers() == ["xai"]

    monkeypatch.setattr(config, "XAI_API_KEY", "")
    assert gc._enabled_providers() == []


def test_provider_order_is_configurable(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "XAI_API_KEY", "x")
    monkeypatch.setattr(config, "LLM_PROVIDER_ORDER", ("xai", "gemini"))
    assert gc._enabled_providers() == ["xai", "gemini"]


# --- the run still completes ----------------------------------------------

def test_grok_extraction_is_labelled_as_grok(pack, sample_request, monkeypatch):
    """The user must be able to tell Grok output from Gemini output."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "XAI_API_KEY", "x")
    real = pack.ngos[0]

    async def gemini_dead(prompt, json_schema=None): return None
    async def xai_ok(prompt, json_schema=None, temperature=0.4):
        return json.dumps({"projects": [{
            "project_name": "Grok proposed project", "category": "Education",
            "region": "Tamil Nadu", "derived_from_ngo": real.name,
            "estimated_budget": 2_000_000, "estimated_beneficiaries": 900,
        }]})

    monkeypatch.setattr(gc, "_generate_gemini", gemini_dead)
    monkeypatch.setattr(xai_client, "generate", xai_ok)

    candidates, warnings = asyncio.run(gc.extract_candidates(pack, sample_request))
    assert candidates[0].extraction_method == "xai"
    assert any("xai" in w for w in warnings), warnings


def test_both_providers_down_still_produces_candidates(pack, sample_request,
                                                       monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "XAI_API_KEY", "")
    candidates, warnings = asyncio.run(gc.extract_candidates(pack, sample_request))
    assert len(candidates) >= 3
    assert all(c.extraction_method == "rule_based" for c in candidates)
    assert any("rule-based" in w for w in warnings)


# --- xAI transport ---------------------------------------------------------

def _stub_transport(status: int, payload: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


def test_xai_parses_a_normal_completion(monkeypatch):
    monkeypatch.setattr(config, "XAI_API_KEY", "x")
    transport = _stub_transport(200, {
        "choices": [{"message": {"content": "hello from grok"}}]})
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    assert asyncio.run(xai_client.generate("hi")) == "hello from grok"


def test_xai_credit_error_returns_none_not_raise(monkeypatch):
    """The live key returns 403 for exhausted credits; it must degrade quietly."""
    monkeypatch.setattr(config, "XAI_API_KEY", "x")
    transport = _stub_transport(403, {
        "code": "permission-denied",
        "error": "Your team has either used all available credits..."})
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    assert asyncio.run(xai_client.generate("hi")) is None


def test_xai_without_a_key_is_unavailable(monkeypatch):
    monkeypatch.setattr(config, "XAI_API_KEY", "")
    assert xai_client.available() is False
    assert asyncio.run(xai_client.generate("hi")) is None
    ok, detail = asyncio.run(xai_client.health_check())
    assert ok is False and "not configured" in detail


@pytest.mark.live
def test_live_grok():
    if not config.XAI_API_KEY:
        pytest.skip("XAI_API_KEY not configured")
    ok, detail = asyncio.run(xai_client.health_check())
    if not ok:
        pytest.skip(f"xAI unavailable ({detail[:90]}). If this is a credit "
                    f"error, the account needs credits before Grok can serve "
                    f"as a fallback.")
    assert ok
