"""
BM25 keyword search over the parsed knowledge base.

Used in two places:
  - MD fallback retrieval, when Tavily is unavailable or thin
  - NGO matching, to score how well an NGO's profile fits a project brief

The index is built once per process from the cached parse.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

from rank_bm25 import BM25Okapi

from models.schemas import NGORecord
from retrieval.md_parser import load_all_ngos, load_historical_projects
from retrieval.text_utils import tokenize


def ngo_document(ngo: NGORecord) -> str:
    """
    Flatten an NGO into the text BM25 indexes.

    Name, focus and geography are repeated because a query naming a state or a
    sector should pull matching organisations up strongly.
    """
    parts = [
        ngo.name, ngo.name,
        " ".join(ngo.focus_areas), " ".join(ngo.focus_areas),
        " ".join(ngo.categories),
        " ".join(ngo.target_beneficiaries),
        " ".join(ngo.geographic_coverage), " ".join(ngo.geographic_coverage),
        ngo.state or "", ngo.location or "", ngo.country or "",
        " ".join(ngo.districts),
        " ".join(ngo.compliance),
        ngo.transparency_rating or "",
        ngo.known_impact or "",
        ngo.temenos_relationship or "",
        str(ngo.raw.get("matching_signals", "")),
    ]
    return " ".join(p for p in parts if p)


def project_document(project: dict[str, Any]) -> str:
    parts = [
        project.get("project_name", ""),
        project.get("csr_category", "") or "",
        project.get("canonical_category", "") or "",
        project.get("region", "") or "",
        project.get("country", "") or "",
        project.get("problem", "") or "",
        project.get("partner", "") or "",
        project.get("beneficiaries", "") or "",
        project.get("expected_impact", "") or "",
    ]
    return " ".join(p for p in parts if p)


@dataclass
class BM25Index:
    """A BM25Okapi index plus the records it was built from."""

    records: list[Any]
    documents: list[str]
    model: BM25Okapi

    def search(
        self,
        query: str,
        k: int = 10,
        predicate: Callable[[Any], bool] | None = None,
    ) -> list[tuple[Any, float]]:
        """
        Top-k records for `query`, highest score first.

        `predicate` filters candidates *before* ranking, so hard eligibility
        rules never have to be undone by a high keyword score.
        """
        tokens = tokenize(query)
        if not tokens or not self.records:
            return []
        scores = self.model.get_scores(tokens)
        pairs = [
            (record, float(score))
            for record, score in zip(self.records, scores)
            if predicate is None or predicate(record)
        ]
        # Sort by score, then by a stable key, so equal scores never reorder
        # between runs -- the whole pipeline has to be reproducible.
        pairs.sort(key=lambda p: (-p[1], _stable_key(p[0])))
        return pairs[:k]


def _stable_key(record: Any) -> str:
    if isinstance(record, NGORecord):
        return record.ngo_id
    if isinstance(record, dict):
        return str(record.get("project_id", ""))
    return str(record)


def build_index(records: list[Any], to_document: Callable[[Any], str]) -> BM25Index:
    documents = [to_document(r) for r in records]
    tokenized = [tokenize(d) or ["__empty__"] for d in documents]
    return BM25Index(records=records, documents=documents, model=BM25Okapi(tokenized))


@lru_cache(maxsize=1)
def ngo_index() -> BM25Index:
    return build_index(load_all_ngos(), ngo_document)


@lru_cache(maxsize=1)
def project_index() -> BM25Index:
    return build_index(load_historical_projects(), project_document)


def build_query(
    csr_sector: str = "",
    states: list[str] | None = None,
    beneficiary_categories: list[str] | None = None,
    sub_sector: str = "",
    districts: list[str] | None = None,
) -> str:
    """The MD-fallback query described in the NIDHI specification."""
    parts = [csr_sector, sub_sector]
    parts += list(states or [])
    parts += list(districts or [])
    parts += list(beneficiary_categories or [])
    return " ".join(p for p in parts if p).strip()


def search_ngos(query: str, k: int = 10, **kwargs) -> list[tuple[NGORecord, float]]:
    return ngo_index().search(query, k=k, **kwargs)


def search_projects(query: str, k: int = 5, **kwargs) -> list[tuple[dict, float]]:
    return project_index().search(query, k=k, **kwargs)


def clear_caches() -> None:
    ngo_index.cache_clear()
    project_index.cache_clear()
