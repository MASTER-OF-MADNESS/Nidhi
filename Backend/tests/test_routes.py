"""Steps 12-14: endpoint behaviour, including the full SSE stream."""

import json

import config
import pytest
from fastapi.testclient import TestClient

from db.database import init_db, reset_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SQLITE_DB_PATH", tmp_path / "routes.db")
    monkeypatch.setattr(config, "TAVILY_API_KEY", "")   # deterministic MD path
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    init_db()
    reset_db()
    from main import app
    with TestClient(app) as c:
        yield c


def _sse(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ")]


# --- auth ------------------------------------------------------------------

def test_login_succeeds_with_configured_credentials(client):
    response = client.post("/auth/login", json={
        "username": config.NIDHI_USERNAME, "password": config.NIDHI_PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["company_id"] == "temenos"
    assert "Temenos" in body["company_name"]


@pytest.mark.parametrize("username,password", [
    ("temenos_admin", "wrong"), ("wrong", "temenos@nidhi2026"), ("", ""),
])
def test_login_rejects_bad_credentials(client, username, password):
    response = client.post("/auth/login",
                           json={"username": username, "password": password})
    assert response.status_code == 401
    # The same message either way: never reveal which half was wrong.
    assert response.json()["detail"] == "Invalid username or password."


def test_session_reports_the_registered_company(client):
    body = client.get("/auth/session").json()
    assert body["company_id"] == "temenos"


# --- company ---------------------------------------------------------------

def test_company_profile_returns_knowledge_base_content(client):
    body = client.get("/company/temenos").json()
    assert body["legal_name"].startswith("Temenos AG")
    assert body["founded"] == 1993
    assert len(body["esg_pillars"]) == 6
    assert len(body["historical_projects"]) == 15
    assert 5 in body["sdg_alignment"]


def test_unregistered_company_is_404(client):
    response = client.get("/company/acme")
    assert response.status_code == 404
    assert "acme" in response.json()["detail"]


def test_companies_listing(client):
    body = client.get("/companies").json()
    assert body["registered_count"] == 1
    assert body["companies"][0]["company_id"] == "temenos"


# --- health ----------------------------------------------------------------

def test_health_on_both_verbs(client):
    for response in (client.get("/health"), client.post("/health")):
        assert response.status_code == 200
        body = response.json()
        assert body["database"] is True
        assert set(body) >= {"status", "gemini", "tavily", "database", "model"}


# --- history ---------------------------------------------------------------

def test_history_is_empty_before_any_run(client):
    assert client.get("/history").json() == []


def test_unknown_run_id_is_404(client):
    assert client.get("/history/nope").status_code == 404


# --- the SSE contract ------------------------------------------------------

def test_sections_contract_is_published(client):
    body = client.get("/generate/sections").json()
    assert body["order"][0] == "progress"
    assert body["order"][-1] == "complete"
    assert "scorecard" in body["sections"]


# --- the full pipeline over SSE -------------------------------------------

REQUEST = {
    "total_csr_budget": 20_000_000, "funding_type": "Grant",
    "project_duration": 18, "number_of_projects": 4,
    "min_project_funding": 1_000_000, "max_project_funding": 8_000_000,
    "country": "India", "states": ["Tamil Nadu", "Karnataka"],
    "districts": ["Chennai"], "csr_sector": "Education",
    "csr_sub_sector": "Digital literacy",
    "beneficiary_categories": ["Children", "Youth"],
    "ngo_experience": 0, "similar_project_experience": False,
    "geographic_capability": "Multi-state", "ngo_rating": "Gold",
    "compliance_requirements": ["CSR-1", "12A"],
    "collaboration_type": "NGO + Local Community", "employee_involvement": True,
    "additional_comments": "Prefer measurable learning outcomes.",
    "currency": "INR",
}


@pytest.fixture
def stream(client):
    response = client.post("/generate", json=REQUEST)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return _sse(response)


def test_stream_emits_every_section_in_order(stream):
    seen = [e["section"] for e in stream]
    assert seen[0] == "progress"
    assert seen[-1] == "complete"
    for section in ("overview", "scorecard", "fund_split",
                    "ngo_recommendations", "ngo_comparison",
                    "strategic_insights", "human_review_flags"):
        assert section in seen, section
    # Sections arrive in the documented order.
    order = {s: i for i, s in enumerate(
        ["overview", "scorecard", "fund_split", "ngo_recommendations",
         "ngo_comparison", "strategic_insights", "human_review_flags"])}
    positions = [order[s] for s in seen if s in order]
    assert positions == sorted(positions)


def test_progress_reports_the_data_source(stream):
    done = [e for e in stream
            if e["section"] == "progress" and e.get("step") == "retrieval"
            and e.get("status") == "done"]
    assert done and done[0]["source"] == config.DATA_SOURCE_MD


def test_every_project_gets_its_own_indexed_scorecard(stream):
    cards = [e for e in stream if e["section"] == "scorecard"]
    assert len(cards) >= 3
    assert [c["project_index"] for c in cards] == list(range(len(cards)))
    first = cards[0]["content"]
    for dimension in config.SCORING_WEIGHTS:
        assert dimension in first
    assert first["explanation"]
    assert 0 <= first["confidence"] <= 1


def test_fund_split_respects_the_budget_and_explains_exclusions(stream):
    content = next(e["content"] for e in stream if e["section"] == "fund_split")
    assert content["total_allocated"] <= REQUEST["total_csr_budget"] + 1e-6
    assert content["allocations"]
    for allocation in content["allocations"]:
        assert (REQUEST["min_project_funding"] <= allocation["allocated_amount"]
                <= REQUEST["max_project_funding"])
    for excluded in content["excluded"]:
        assert excluded["reason"] and excluded["what_would_change_it"]
    assert content["constraint_report"]["checks"]


def test_ngo_recommendations_carry_evidence_and_limitations(stream):
    content = next(e["content"] for e in stream
                   if e["section"] == "ngo_recommendations")
    assert content["recommendations"]
    matched = [r for r in content["recommendations"] if r["matches"]]
    assert matched, "at least one project should have an eligible partner"
    match = matched[0]["matches"][0]
    assert 0 <= match["compatibility_score"] <= 100
    assert 0 <= match["star_rating"] <= 5
    assert match["evidence_source"]
    assert match["limitations"], "unknowns must be surfaced, not hidden"


def test_review_flags_surface_low_confidence(stream):
    content = next(e["content"] for e in stream
                   if e["section"] == "human_review_flags")
    assert content["flags"]
    categories = {f["category"] for f in content["flags"]}
    # The MD fallback path must always declare its evidence freshness.
    assert "Evidence freshness" in categories


def test_complete_frame_carries_a_real_run_id(stream, client):
    final = stream[-1]
    assert final["section"] == "complete"
    run_id = final["run_id"]
    assert run_id and len(run_id) == 36

    detail = client.get(f"/history/{run_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["company_id"] == "temenos"
    assert body["projects_evaluated"] > 0
    assert body["input_parameters"]["csr_sector"] == "Education"
    assert body["output_summary"]["recommended"]


def test_run_appears_in_history_after_the_stream(stream, client):
    runs = client.get("/history").json()
    assert len(runs) == 1
    assert runs[0]["data_source"] == config.DATA_SOURCE_MD


def test_language_is_decision_support_not_approval(stream):
    """NIDHI recommends; a human approves. The wording must reflect that."""
    blob = json.dumps(stream).lower()
    for banned in ("has been approved", "we approve", "is approved",
                   "automatically approved"):
        assert banned not in blob, banned
    assert "recommend" in blob


def test_generate_rejects_an_unregistered_company(client):
    response = client.post("/generate?company_id=acme", json=REQUEST)
    assert response.status_code == 404
