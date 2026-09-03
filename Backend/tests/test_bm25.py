"""Step 5 checks: keyword retrieval surfaces the right organisations."""

from retrieval.bm25_search import (
    build_query,
    ngo_index,
    search_ngos,
    search_projects,
)


def test_women_livelihood_query_ranks_women_focused_ngos_first():
    query = build_query("Women livelihood", ["Tamil Nadu"], ["Women"])
    names = [n.name for n, _ in search_ngos(query, k=5)]
    assert any("Non-Traditional Employment for Women" in n for n in names)
    assert all(
        n.state in {"Tamil Nadu", "Chennai", None} or n.temenos_partner
        for n, _ in search_ngos(query, k=3)
    )


def test_education_query_stays_in_the_requested_state():
    query = build_query("Education", ["Karnataka"], ["Children"], "Digital literacy")
    top = [n for n, _ in search_ngos(query, k=4)]
    assert sum(1 for n in top if n.state == "Karnataka") >= 3


def test_project_search_finds_the_matching_historical_project():
    hits = search_projects("women livelihood plastic recycling chennai", k=3)
    assert hits[0][0]["project_id"] == "CSR-003"


def test_predicate_filters_before_ranking():
    """Hard eligibility must not be recoverable by a high keyword score."""
    query = build_query("Education", ["Kerala"], ["Children"])
    results = search_ngos(query, k=10, predicate=lambda n: n.state == "Odisha")
    assert results
    assert all(n.state == "Odisha" for n, _ in results)


def test_results_are_deterministic():
    query = build_query("Environment", ["Tamil Nadu"], ["Rural"])
    first = [n.ngo_id for n, _ in search_ngos(query, k=8)]
    second = [n.ngo_id for n, _ in search_ngos(query, k=8)]
    assert first == second


def test_empty_query_returns_nothing_rather_than_everything():
    assert search_ngos("", k=5) == []
    assert search_ngos("   ", k=5) == []


def test_index_covers_every_known_ngo():
    assert len(ngo_index().records) == 82        # 12 Temenos partners + 70 pool
