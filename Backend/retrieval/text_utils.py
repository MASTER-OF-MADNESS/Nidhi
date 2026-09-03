"""
Shared text normalisation used by the parser, BM25 index, scorer and matcher.

Kept in one place so that "does this NGO work in education?" is answered the
same way everywhere in the system.
"""

from __future__ import annotations

import re
import unicodedata

import config

_BOLD_RE = re.compile(r"\*\*|__|`")
_TAG_RE = re.compile(r"<[^>]+>")
_CITATION_RE = re.compile(r"\[\d+\]")
_NON_WORD_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "its", "of", "on", "or", "that", "the", "their",
    "this", "to", "was", "were", "will", "with", "we", "our", "not", "no",
}


def strip_markdown(value: str) -> str:
    """Remove bold markers, inline HTML, and [12] style citation footnotes."""
    text = _BOLD_RE.sub("", value or "")
    text = _TAG_RE.sub(" ", text)
    text = _CITATION_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


def clean_value(value: str | None) -> str | None:
    """
    Normalise a knowledge-base cell to a real value or None.

    This is where the UNKNOWN-is-not-zero rule is enforced at the boundary:
    blanks and the dataset's "we looked and did not find it" sentinels all
    become None so that no downstream code can mistake them for a measurement.
    """
    if value is None:
        return None
    text = strip_markdown(str(value)).strip(" \t|")
    if not text:
        return None
    probe = text.lower().rstrip(".").strip()
    if probe in config.UNKNOWN_SENTINELS:
        return None
    # Sentinels sometimes carry a qualifier, e.g.
    # "NOT PUBLICLY DISCLOSED IN THIS BACKUP DATASET".
    for sentinel in ("not publicly disclosed", "not verified",
                     "requires live verification", "requires live due diligence",
                     "not disclosed", "not found"):
        if probe.startswith(sentinel):
            return None
    return text


def split_list(value: str | None) -> list[str]:
    """Split a semicolon/comma separated cell into clean parts."""
    cleaned = clean_value(value)
    if not cleaned:
        return []
    parts = re.split(r"[;,]|\s+/\s+", cleaned)
    return [p.strip() for p in parts if p.strip()]


def normalize_name(name: str) -> str:
    """
    Fold an organisation name to a merge key.

    Strips accents, punctuation, bracketed acronyms and common legal suffixes so
    that "Society for Poor People Development (SPPD)" and "Society for Poor
    People Development" resolve to the same NGO across two files.
    """
    text = unicodedata.normalize("NFKD", strip_markdown(name or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = _NON_WORD_RE.sub(" ", text)
    tokens = [t for t in text.split()
              if t not in {"the", "of", "for", "and", "trust", "society",
                           "foundation", "org", "organisation", "organization"}]
    return " ".join(tokens) or _WS_RE.sub(" ", text).strip()


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens with stopwords removed, for BM25."""
    lowered = _NON_WORD_RE.sub(" ", strip_markdown(text or "").lower())
    return [t for t in lowered.split() if t and t not in STOPWORDS and len(t) > 1]


def canonical_category(*texts: str | None) -> str:
    """
    Map free text (a sector name, an NGO focus area) to a canonical category key
    matching config.COST_BENCHMARKS. Falls back to "default" when nothing hits.
    """
    blob = " ".join(t.lower() for t in texts if t)
    if not blob.strip():
        return "default"
    best: tuple[int, int, str] | None = None
    for alias, canonical in config.CATEGORY_ALIASES.items():
        position = blob.find(alias)
        if position == -1:
            continue
        # Earliest mention wins: the knowledge base lists a project's primary
        # category first, so "Women's Livelihood, Environmental Sustainability"
        # is a women's livelihood project. Longest alias breaks a tie, so
        # "financial inclusion" beats the bare "inclusion" at the same spot.
        candidate = (position, -len(alias), canonical)
        if best is None or candidate < best:
            best = candidate
    return best[2] if best else "default"


def parse_int(value: str | None) -> int | None:
    """Pull the first integer out of a cell, honouring 33,000+ and 1.5M forms."""
    cleaned = clean_value(value)
    if not cleaned:
        return None
    match = re.search(r"(\d[\d,]*\.?\d*)\s*([kKmM])?", cleaned.replace(" ", ""))
    if not match:
        return None
    try:
        number = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (match.group(2) or "").lower()
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    return int(number)


def parse_bool(value: str | None) -> bool | None:
    """True/False only on an explicit signal; anything else stays None."""
    cleaned = clean_value(value)
    if not cleaned:
        return None
    probe = cleaned.lower()
    if probe.startswith(("yes", "true", "available", "y ")) or probe == "y":
        return True
    if probe.startswith(("no", "false", "unavailable")) or probe == "n":
        return False
    return None


def extract_sdgs(text: str | None) -> list[int]:
    """Pull SDG numbers out of free text such as 'SDG 5, 8, 11, 12'."""
    cleaned = clean_value(text)
    if not cleaned or "sdg" not in cleaned.lower():
        return []
    tail = cleaned.lower().split("sdg", 1)[1]
    return sorted({int(n) for n in re.findall(r"\d{1,2}", tail) if 1 <= int(n) <= 17})
