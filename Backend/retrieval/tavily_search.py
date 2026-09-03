"""
Tavily web search -- the PRIMARY retrieval path.

The specification is explicit: always try Tavily first, fall back to the
Markdown knowledge base only when it fails, times out, or returns too little.
This module never raises for an external failure; it reports what happened and
lets the caller decide, so the fallback chain stays visible in the response.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import config
from models.schemas import NGORecord
from retrieval.text_utils import (
    canonical_category,
    clean_value,
    normalize_name,
    parse_int,
)

_COMPLIANCE_PATTERNS = {
    "CSR-1": re.compile(r"\bcsr[\s-]?1\b", re.I),
    "12A": re.compile(r"\b12[\s-]?a\b", re.I),
    "80G": re.compile(r"\b80[\s-]?g\b", re.I),
    "FCRA": re.compile(r"\bfcra\b", re.I),
}
_YEARS_RE = re.compile(r"(\d{1,3})\+?\s*years?\s*(of\s*)?(experience|service|work)", re.I)
_FOUNDED_RE = re.compile(r"\b(?:founded|established|since)\s*(?:in\s*)?(\d{4})\b", re.I)
_BENEFICIARY_RE = re.compile(
    r"([\d,]+(?:\.\d+)?\s*[kKmM]?)\+?\s*(?:children|students|women|youth|"
    r"beneficiaries|people|families|farmers|patients)", re.I)

# Titles that are clearly listicles or directories rather than organisations.
_NON_ORG_HINTS = (
    "top ", "best ", "list of", "ngos in", "10 ", "20 ", "guide", "how to",
    "blog", "news", "|", " - ",
)

# Generic page titles that are navigation labels, not organisation names.
# A result titled "Projects" or "Home" carries no identity, and letting one
# through produces recommendations addressed to an NGO called "Projects".
_GENERIC_TITLES = {
    "projects", "project", "home", "about", "about us", "contact", "contact us",
    "our work", "our projects", "programs", "programmes", "initiatives",
    "csr", "csr projects", "csr initiatives", "donate", "partners", "impact",
    "welcome", "index", "overview", "services", "gallery", "media", "resources",
    "ngo", "ngos", "foundation", "trust", "charity", "sitemap", "search",
}


@dataclass
class TavilyOutcome:
    """What retrieval actually managed to do, so the caller can be honest."""

    ngos: list[NGORecord] = field(default_factory=list)
    raw_results: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = False
    queries: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def sufficient(self) -> bool:
        return self.ok and len(self.ngos) >= config.TAVILY_MIN_NGOS


def build_queries(
    csr_sector: str,
    country: str,
    states: list[str],
    beneficiary_categories: list[str],
    compliance_requirements: list[str],
) -> list[str]:
    """The three specified queries."""
    states_text = " ".join(states[:3])
    beneficiaries = " ".join(beneficiary_categories[:3])
    compliance = " ".join(compliance_requirements[:4])
    return [
        f"{csr_sector} NGOs in {country} {states_text} CSR projects".strip(),
        f"{csr_sector} CSR projects {beneficiaries} impact".strip(),
        f"NGO compliance {compliance} {country}".strip(),
    ]


def _looks_like_organisation(title: str) -> bool:
    probe = (title or "").strip().lower().strip(" .-:")
    if len(probe) < 4 or len(probe) > 90:
        return False
    # Many real NGOs are single words (Bhumi, Pratham, Sevalaya, Swasti), so
    # word count says nothing. What disqualifies a title is being a generic
    # navigation label.
    if probe in _GENERIC_TITLES:
        return False
    return not any(hint in probe for hint in _NON_ORG_HINTS)


def _clean_title(title: str) -> str:
    """Strip the site-name tail that most pages append to their <title>."""
    text = re.split(r"\s[|\u2013\u2014-]\s", title or "", maxsplit=1)[0]
    text = re.sub(r"\s*\((?:official|home|website)[^)]*\)\s*$", "", text, flags=re.I)
    return text.strip(" .-\u2013\u2014")


def _extract_region(text: str, states: list[str]) -> str | None:
    for state in states:
        if state and state.lower() in text.lower():
            return state
    return None


def _result_to_ngo(
    result: dict[str, Any], index: int, states: list[str], country: str
) -> NGORecord | None:
    title = _clean_title(str(result.get("title") or ""))
    if not _looks_like_organisation(title):
        return None

    body = f"{title} {result.get('content') or ''}"
    compliance = [name for name, pattern in _COMPLIANCE_PATTERNS.items()
                  if pattern.search(body)]

    years = None
    if (m := _YEARS_RE.search(body)):
        years = int(m.group(1))
    elif (m := _FOUNDED_RE.search(body)):
        founded = int(m.group(1))
        if 1900 < founded <= 2026:
            years = 2026 - founded

    beneficiaries = None
    if (m := _BENEFICIARY_RE.search(body)):
        beneficiaries = parse_int(m.group(1))

    snippet = clean_value(str(result.get("content") or ""))
    region = _extract_region(body, states)

    return NGORecord(
        ngo_id=f"TAVILY-{index:03d}",
        name=title,
        source="TAVILY",
        # A web page is a reputable secondary source at best -- never treated
        # as official Temenos evidence.
        evidence_class=config.EVIDENCE_VERIFIED_SECONDARY,
        country=country or None,
        state=region,
        geographic_coverage=[region] if region else [],
        focus_areas=[snippet[:180]] if snippet else [],
        categories=[canonical_category(body)],
        compliance=[c for c in config.COMPLIANCE_CREDENTIALS if c in compliance],
        years_experience=years,
        known_impact=f"{beneficiaries} reported beneficiaries" if beneficiaries else None,
        url=clean_value(str(result.get("url") or "")),
        raw={"content": snippet or "", "tavily_score": result.get("score")},
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def _client():
    """Build a Tavily client, or None when no key is configured."""
    if not config.TAVILY_API_KEY:
        return None
    try:
        from tavily import TavilyClient
    except ImportError:  # pragma: no cover - dependency is pinned
        return None
    try:
        return TavilyClient(api_key=config.TAVILY_API_KEY)
    except Exception:
        return None


def _search_sync(client, query: str) -> dict[str, Any]:
    return client.search(
        query=query,
        max_results=config.MAX_TAVILY_RESULTS,
        search_depth="basic",
    )


async def _run_query(client, query: str, warnings: list[str]) -> list[dict[str, Any]]:
    """One query, hard-capped at TAVILY_TIMEOUT seconds. Never raises."""
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(_search_sync, client, query),
            timeout=config.TAVILY_TIMEOUT,
        )
        return list(response.get("results") or [])
    except asyncio.TimeoutError:
        warnings.append(
            f"Tavily query timed out after {config.TAVILY_TIMEOUT}s: {query!r}"
        )
    except Exception as exc:
        warnings.append(f"Tavily query failed ({type(exc).__name__}): {query!r}")
    return []


async def search_ngos(
    csr_sector: str,
    country: str,
    states: list[str],
    beneficiary_categories: list[str],
    compliance_requirements: list[str],
) -> TavilyOutcome:
    """
    Run the three specified queries concurrently and parse NGOs out of them.

    Returns an outcome rather than raising: `sufficient` tells the caller
    whether to use these results or fall back to the knowledge base.
    """
    queries = build_queries(
        csr_sector, country, states, beneficiary_categories, compliance_requirements
    )
    outcome = TavilyOutcome(queries=queries)

    client = _client()
    if client is None:
        outcome.warnings.append(
            "Tavily unavailable: TAVILY_API_KEY is not configured."
        )
        return outcome

    batches = await asyncio.gather(
        *(_run_query(client, q, outcome.warnings) for q in queries)
    )

    seen: set[str] = set()
    index = 0
    for batch in batches:
        outcome.raw_results.extend(batch)
        for result in batch:
            ngo = _result_to_ngo(result, index, states, country)
            if ngo is None:
                continue
            key = normalize_name(ngo.name)
            if not key or key in seen:
                continue
            seen.add(key)
            index += 1
            outcome.ngos.append(ngo)

    # ok = at least one query came back with something usable.
    outcome.ok = bool(outcome.raw_results) and len(outcome.warnings) < len(queries)
    if outcome.ok and not outcome.sufficient:
        outcome.warnings.append(
            f"Tavily returned only {len(outcome.ngos)} usable NGO(s); "
            f"{config.TAVILY_MIN_NGOS} required. Falling back to the "
            f"knowledge base."
        )
    return outcome


async def health_check() -> tuple[bool, str]:
    """Cheap liveness probe for GET /health."""
    client = _client()
    if client is None:
        return False, "TAVILY_API_KEY not configured"
    try:
        await asyncio.wait_for(
            asyncio.to_thread(client.search, "CSR NGO India"),
            timeout=min(config.TAVILY_TIMEOUT, 10),
        )
        return True, "ok"
    except asyncio.TimeoutError:
        return False, "timeout"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:160]
