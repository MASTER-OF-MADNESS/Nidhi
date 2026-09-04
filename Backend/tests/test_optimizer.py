"""
Step 10 checks: the CP-SAT portfolio optimiser.

The important behaviours are that a feasible portfolio honours every rule, and
that an infeasible one degrades in a way the user can see and understand --
never by silently dropping a constraint.
"""

import config

from engine.ortools_optimizer import optimize
from models.schemas import GenerateRequest, ProjectScore


def _score(pid, name, score, category="education", region="Tamil Nadu",
           confidence=0.8) -> ProjectScore:
    return ProjectScore(
        project_id=pid, project_name=name, category=category, region=region,
        final_score=score, confidence=confidence, estimated_beneficiaries=1000,
        requested_budget=2_000_000,
    )


def _request(**kwargs) -> GenerateRequest:
    base = dict(total_csr_budget=20_000_000, number_of_projects=3,
                min_project_funding=1_000_000, max_project_funding=8_000_000,
                currency="INR")
    base.update(kwargs)
    return GenerateRequest(**base)


DIVERSE = [
    _score("P1", "Education TN", 88, "education", "Tamil Nadu"),
    _score("P2", "Women livelihood KA", 84, "women_livelihood", "Karnataka"),
    _score("P3", "Environment OD", 80, "environment", "Odisha"),
    _score("P4", "Health KL", 76, "healthcare", "Kerala"),
    _score("P5", "Youth AP", 72, "youth_development", "Andhra Pradesh"),
    _score("P6", "Low scorer", 41, "education", "Telangana"),
]


# --- the feasible case -----------------------------------------------------

def test_feasible_portfolio_respects_every_constraint():
    outcome = optimize(DIVERSE, _request(number_of_projects=3))
    report = outcome.constraint_report

    assert report.status in ("OPTIMAL", "FEASIBLE")
    assert len(outcome.selected) == 3
    assert outcome.total_allocated <= 20_000_000 + 1e-6
    assert all(c["satisfied"] for c in report.checks), \
        [c for c in report.checks if not c["satisfied"]]


def test_per_project_funding_range_is_honoured():
    outcome = optimize(DIVERSE, _request(number_of_projects=4,
                                         min_project_funding=2_000_000,
                                         max_project_funding=6_000_000))
    for allocation in outcome.selected:
        assert 2_000_000 <= allocation.allocated_amount <= 6_000_000


def test_money_flows_toward_higher_scoring_projects():
    """The second objective pass exists precisely to make this true."""
    outcome = optimize(DIVERSE, _request(number_of_projects=3))
    ranked = sorted(outcome.selected, key=lambda a: -a.final_score)
    assert ranked[0].allocated_amount >= ranked[-1].allocated_amount


def test_low_scoring_projects_are_excluded_with_a_reason():
    outcome = optimize(DIVERSE, _request(number_of_projects=3))
    rejected = next(e for e in outcome.excluded if e.project_id == "P6")
    assert "below" in rejected.reason
    assert rejected.binding_constraint == "minimum score"
    assert rejected.what_would_change_it


def test_every_excluded_project_says_what_would_change_it():
    outcome = optimize(DIVERSE, _request(number_of_projects=2))
    assert outcome.excluded
    for excluded in outcome.excluded:
        assert excluded.reason
        assert excluded.what_would_change_it


def test_region_cap_is_enforced_when_it_can_be():
    """Four candidates across four regions: no region may exceed 40%."""
    outcome = optimize(DIVERSE, _request(number_of_projects=4))
    budget = 20_000_000
    by_region: dict[str, float] = {}
    for allocation in outcome.selected:
        by_region[allocation.region] = by_region.get(allocation.region, 0.0) \
            + allocation.allocated_amount
    for region, amount in by_region.items():
        assert amount <= config.REGION_CAP * budget + 1e-6, (region, amount)


# --- degradation -----------------------------------------------------------

def test_single_region_pool_reports_the_cap_as_a_named_relaxation():
    """
    A 40% cap cannot be met when every candidate is in one state. Skipping it is
    correct; skipping it silently is not.
    """
    same_region = [_score(f"P{i}", f"Project {i}", 80 - i, "education", "Tamil Nadu")
                   for i in range(5)]
    outcome = optimize(same_region, _request(number_of_projects=3))

    assert outcome.selected
    assert outcome.constraint_report.relaxations_applied
    assert any("per-region" in r for r in outcome.constraint_report.relaxations_applied)
    assert any("Concentration risk" in n for n in outcome.constraint_report.notes)
    assert outcome.constraint_report.satisfied is False


def test_over_constrained_request_relaxes_rather_than_failing():
    """Asking for more projects than clear the score bar must still return one."""
    thin = [_score("P1", "Only good one", 82), _score("P2", "Weak", 30),
            _score("P3", "Weaker", 25)]
    outcome = optimize(thin, _request(number_of_projects=3))

    assert outcome.selected, "must return a portfolio rather than nothing"
    assert outcome.constraint_report.relaxations_applied
    assert outcome.total_allocated > 0


def test_impossible_minimum_still_returns_something():
    """min funding x project count exceeds the budget: unsatisfiable as stated."""
    outcome = optimize(DIVERSE, _request(number_of_projects=5,
                                         min_project_funding=9_000_000,
                                         max_project_funding=10_000_000))
    assert outcome.selected or outcome.constraint_report.notes
    assert outcome.total_allocated <= 20_000_000 + 1e-6


def test_no_candidates_returns_an_empty_but_explained_outcome():
    outcome = optimize([], _request())
    assert outcome.selected == []
    assert outcome.constraint_report.satisfied is False
    assert outcome.constraint_report.notes


def test_greedy_fallback_when_solver_is_unavailable(monkeypatch):
    import engine.ortools_optimizer as opt
    monkeypatch.setattr(opt, "_solve",
                        lambda *a, **k: opt.Solution(status="SOLVER_UNAVAILABLE"))
    outcome = optimize(DIVERSE, _request(number_of_projects=3))

    assert outcome.selected, "greedy must still produce a portfolio"
    assert outcome.constraint_report.solver == "greedy-fallback"
    assert outcome.total_allocated <= 20_000_000 + 1e-6


def test_results_are_deterministic():
    request = _request(number_of_projects=3)
    first = optimize(DIVERSE, request)
    second = optimize(DIVERSE, request)
    assert [(a.project_id, a.allocated_amount) for a in first.selected] == \
           [(a.project_id, a.allocated_amount) for a in second.selected]
