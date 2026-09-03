"""
Step 6 checks: Tavily parsing, and every failure mode degrading cleanly.

The live probe is marked `live` and skipped unless a real key is configured.
"""

import asyncio

import config
import pytest

from retrieval import tavily_search as ts

SAMPLE = {
    "results": [
        {
            "title": "Bhumi - Empowering Children Through Education",
            "url": "https://bhumi.ngo/",
            "content": ("Bhumi is a Chennai based NGO in Tamil Nadu working with "
                        "25,000 children. Registered under 12A, 80G and CSR-1. "
                        "Founded in 2006."),
            "score": 0.93,
        },
        {
            "title": "Top 10 NGOs in India | Best Charities List",
            "url": "https://example.com/top-10",
            "content": "A listicle, not an organisation.",
            "score": 0.8,
        },
        {
            "title": "Akshaya Patra Foundation",
            "url": "https://akshayapatra.org/",
            "content": ("Mid-day meal programme reaching 2M children across "
                        "Karnataka. FCRA registered. 20 years of experience."),
            "score": 0.9,
        },
        {
            "title": "Bhumi",   # duplicate organisation, different page
            "url": "https://bhumi.ngo/about",
            "content": "About Bhumi.",
            "score": 0.7,
        },
    ]
}


class FakeClient:
    def __init__(self, payload=None, error=None, hang=False):
        self.payload, self.error, self.hang = payload, error, hang
        self.calls = []

    def search(self, query=None, **kwargs):
        self.calls.append(query)
        if self.hang:
            import time
            time.sleep(5)
        if self.error:
            raise self.error
        return self.payload


@pytest.fixture
def use_client(monkeypatch):
    def _install(client):
        monkeypatch.setattr(ts, "_client", lambda: client)
        return client
    return _install


def _run(**overrides):
    kwargs = dict(
        csr_sector="Education", country="India", states=["Tamil Nadu"],
        beneficiary_categories=["Children"], compliance_requirements=["CSR-1"],
    )
    kwargs.update(overrides)
    return asyncio.run(ts.search_ngos(**kwargs))


def test_three_queries_are_built_as_specified():
    queries = ts.build_queries("Education", "India", ["Tamil Nadu"],
                               ["Children"], ["CSR-1", "12A"])
    assert len(queries) == 3
    assert queries[0].startswith("Education NGOs in India Tamil Nadu")
    assert "impact" in queries[1]
    assert queries[2].startswith("NGO compliance")


def test_results_parse_into_ngo_records(use_client):
    use_client(FakeClient(payload=SAMPLE))
    outcome = _run()

    names = [n.name for n in outcome.ngos]
    assert "Bhumi" in names
    assert "Akshaya Patra Foundation" in names
    # Listicles are not organisations.
    assert not any("Top 10" in n for n in names)
    # The same organisation found twice is one record.
    assert names.count("Bhumi") == 1

    bhumi = next(n for n in outcome.ngos if n.name == "Bhumi")
    assert bhumi.compliance == ["CSR-1", "12A", "80G"]
    assert bhumi.state == "Tamil Nadu"
    assert bhumi.source == "TAVILY"
    # A web page is secondary evidence, never official.
    assert bhumi.evidence_class == "VERIFIED_SECONDARY"
    assert bhumi.years_experience == 2026 - 2006


def test_title_site_suffix_is_stripped(use_client):
    use_client(FakeClient(payload=SAMPLE))
    outcome = _run()
    assert "Empowering Children" not in " ".join(n.name for n in outcome.ngos)


def test_missing_key_degrades_without_raising(monkeypatch):
    monkeypatch.setattr(config, "TAVILY_API_KEY", "")
    outcome = _run()
    assert outcome.ok is False
    assert outcome.sufficient is False
    assert "not configured" in outcome.warnings[0]


def test_timeout_is_reported_not_raised(use_client, monkeypatch):
    monkeypatch.setattr(config, "TAVILY_TIMEOUT", 1)
    use_client(FakeClient(hang=True))
    outcome = _run()
    assert outcome.sufficient is False
    assert any("timed out" in w for w in outcome.warnings)


def test_api_error_is_reported_not_raised(use_client):
    use_client(FakeClient(error=RuntimeError("503 upstream")))
    outcome = _run()
    assert outcome.sufficient is False
    assert any("failed" in w for w in outcome.warnings)


def test_too_few_results_is_insufficient_and_says_so(use_client):
    thin = {"results": [SAMPLE["results"][0]]}
    use_client(FakeClient(payload=thin))
    outcome = _run()
    assert outcome.ok is True          # the call itself worked
    assert outcome.sufficient is False  # but 1 NGO < TAVILY_MIN_NGOS
    assert any("Falling back" in w for w in outcome.warnings)


@pytest.mark.live
def test_live_tavily_search():
    if not config.TAVILY_API_KEY:
        pytest.skip("TAVILY_API_KEY not configured in .env")
    outcome = _run()
    assert outcome.ok, outcome.warnings
    assert outcome.raw_results


def test_generic_page_titles_are_rejected():
    """
    A page titled "Projects" is a navigation label, not an organisation.
    Letting one through produces a recommendation addressed to an NGO called
    "Projects", which is what happened against the live API.
    """
    for junk in ("Projects", "Home", "About Us", "Our Work", "CSR",
                 "Partners", "Donate", "Impact"):
        assert not ts._looks_like_organisation(junk), junk


def test_single_word_ngo_names_are_kept():
    """Many real NGOs are one word; word count is not a disqualifier."""
    for real in ("Bhumi", "Pratham", "Sevalaya", "Swasti", "Thuvakkam"):
        assert ts._looks_like_organisation(real), real


def test_quota_error_is_not_retried(monkeypatch):
    """A 429 means the quota is gone; retrying burns time for no gain."""
    import asyncio
    import config as cfg
    from engine import gemini_client as gc

    attempts = []

    class Boom:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                attempts.append(1)
                raise RuntimeError(
                    "429 RESOURCE_EXHAUSTED. Quota exceeded for metric")

    monkeypatch.setattr(cfg, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(gc, "_client", lambda: Boom())
    result = asyncio.run(gc._generate("hello"))

    assert result is None
    assert len(attempts) == 1, f"retried {len(attempts)} times on a quota error"
