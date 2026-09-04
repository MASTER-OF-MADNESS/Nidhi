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


# --- chain ordering --------------------------------------------------------

def test_gemini_is_preferred_when_it_works(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "XAI_API_KEY", "x")

    async def gemini_ok(prompt, json_schema=None): return "from-gemini"
    async def xai_should_not_run(prompt, json_schema=None, temperature=0.4):
        raise AssertionError("xAI must not be called while Gemini works")

    monkeypatch.setattr(gc, "_generate_gemini", gemini_ok)
    monkeypatch.setattr(xai_client, "generate", xai_should_not_run)

    assert asyncio.run(gc._generate("hi")) == ("from-gemini", "gemini")


def test_falls_through_to_grok_when_gemini_fails(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "XAI_API_KEY", "x")

    async def gemini_dead(prompt, json_schema=None): return None
    async def xai_ok(prompt, json_schema=None, temperature=0.4): return "from-grok"

    monkeypatch.setattr(gc, "_generate_gemini", gemini_dead)
    monkeypatch.setattr(xai_client, "generate", xai_ok)

    assert asyncio.run(gc._generate("hi")) == ("from-grok", "xai")


def test_returns_none_when_every_provider_fails(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "XAI_API_KEY", "x")

    async def dead(prompt, json_schema=None): return None
    async def dead_xai(prompt, json_schema=None, temperature=0.4): return None

    monkeypatch.setattr(gc, "_generate_gemini", dead)
    monkeypatch.setattr(xai_client, "generate", dead_xai)

    assert asyncio.run(gc._generate("hi")) == (None, None)


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


def test_concurrent_requests_do_not_cross_provider_attribution(monkeypatch):
    """
    Two /generate requests in flight must not overwrite each other's record of
    which model answered. Attribution used to live in a module global, and a
    run served by Grok could be logged as Gemini.
    """
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "XAI_API_KEY", "x")

    async def gemini(prompt, json_schema=None):
        if "A" in prompt:
            await asyncio.sleep(0.01)     # A: fails early, falls through to xAI
            return None
        await asyncio.sleep(0.03)         # B: succeeds later
        return "gemini-answer"

    async def xai_ok(prompt, json_schema=None, temperature=0.4):
        return "xai-answer"

    monkeypatch.setattr(gc, "_generate_gemini", gemini)
    monkeypatch.setattr(xai_client, "generate", xai_ok)

    async def call(tag, read_delay):
        text, provider = await gc._generate(tag)
        await asyncio.sleep(read_delay)   # work before the attribution is used
        return text, provider

    async def both():
        return await asyncio.gather(call("request A", 0.05), call("request B", 0.0))

    (a_text, a_provider), (b_text, b_provider) = asyncio.run(both())

    assert (a_text, a_provider) == ("xai-answer", "xai")
    assert (b_text, b_provider) == ("gemini-answer", "gemini")


def test_stream_reports_its_provider(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "XAI_API_KEY", "x")

    async def xai_stream(prompt, temperature=0.5):
        for chunk in ("hello ", "from grok"):
            yield chunk

    monkeypatch.setattr(xai_client, "generate_stream", xai_stream)

    async def collect():
        seen: list[str] = []
        out = [c async for c in gc.stream_text("hi", "fallback", seen)]
        return "".join(out), seen

    text, providers = asyncio.run(collect())
    assert text == "hello from grok"
    assert providers == ["xai"]


# --- xAI streaming transport (previously never executed) -------------------

def _stream_transport(chunks, status=200):
    """Emit an OpenAI-style SSE body."""
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": "nope"})
        lines = []
        for c in chunks:
            lines.append("data: " + json.dumps(
                {"choices": [{"delta": {"content": c}}]}))
        lines.append("data: [DONE]")
        return httpx.Response(200, text="\n".join(lines) + "\n",
                              headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


def _use_transport(monkeypatch, transport):
    real = httpx.AsyncClient

    def patched(*a, **kw):
        kw["transport"] = transport
        return real(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", patched)


def test_xai_stream_parses_sse_chunks(monkeypatch):
    monkeypatch.setattr(config, "XAI_API_KEY", "x")
    _use_transport(monkeypatch, _stream_transport(["Grok ", "wrote ", "this."]))

    async def collect():
        return [c async for c in xai_client.generate_stream("hi")]

    assert "".join(asyncio.run(collect())) == "Grok wrote this."


def test_xai_stream_ignores_malformed_frames(monkeypatch):
    """A single bad frame must not abort the whole stream."""
    monkeypatch.setattr(config, "XAI_API_KEY", "x")

    def handler(request):
        body = "\n".join([
            'data: {"choices":[{"delta":{"content":"good "}}]}',
            "data: {not json at all",
            "data: {}",                                   # no choices
            'data: {"choices":[{"delta":{}}]}',           # no content
            'data: {"choices":[{"delta":{"content":"tail"}}]}',
            "data: [DONE]",
        ])
        return httpx.Response(200, text=body + "\n")
    _use_transport(monkeypatch, httpx.MockTransport(handler))

    async def collect():
        return [c async for c in xai_client.generate_stream("hi")]

    assert "".join(asyncio.run(collect())) == "good tail"


def test_xai_stream_error_status_yields_nothing(monkeypatch):
    monkeypatch.setattr(config, "XAI_API_KEY", "x")
    _use_transport(monkeypatch, _stream_transport([], status=403))

    async def collect():
        return [c async for c in xai_client.generate_stream("hi")]

    assert asyncio.run(collect()) == []
