"""
Portfolio optimisation with OR-Tools CP-SAT.

Chooses which candidate projects to recommend funding and how much to allocate
to each, maximising total expected impact subject to the CSR manager's budget
and the portfolio balance rules from PS2 PART 19.

Three design decisions worth knowing about:

1. CP-SAT is integer-only, so money is expressed in ALLOCATION_UNIT-sized
   buckets (default 1,000) rather than the master prompt's suggested x100
   paise/cents scaling, which would inflate the solver domains by six orders of
   magnitude for no gain in decision fidelity.

2. The objective is lexicographic, solved in two passes: first maximise the
   score of the selected set, then -- holding that selection quality fixed --
   push money toward the higher-scoring projects. Maximising
   `sum(score * y)` alone would leave the allocation amounts undetermined,
   since every feasible split of the budget ties.

3. Constraints are applied as a ladder. An exact project count combined with
   40% concentration caps and 15% category floors goes infeasible easily, and
   silently dropping a rule would misrepresent the result. Each rung records
   what it relaxed, and the relaxations are reported to the user.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

import config
from models.schemas import (
    AllocationResult,
    ConstraintReport,
    ExcludedProject,
    GenerateRequest,
    OptimizationOutcome,
    ProjectScore,
)

log = logging.getLogger("nidhi.optimizer")


@dataclass(frozen=True)
class ConstraintSet:
    """Which optional rules are active. The budget cap is never optional."""

    exact_count: bool = True
    region_cap: bool = True
    sector_cap: bool = True
    category_floors: bool = True
    min_score: bool = True

    def describe_dropped(self, other: "ConstraintSet") -> list[str]:
        labels = {
            "exact_count": "exact project count",
            "region_cap": f"{config.REGION_CAP:.0%} per-region concentration cap",
            "sector_cap": f"{config.SECTOR_CAP:.0%} per-sector concentration cap",
            "category_floors": (
                f"{config.MIN_WOMEN_SHARE:.0%} women's livelihood and "
                f"{config.MIN_ENVIRONMENT_SHARE:.0%} environment minimums"),
            "min_score": f"minimum score of {config.THRESHOLD_REJECT:.0f} to be funded",
        }
        return [labels[k] for k in labels
                if getattr(self, k) and not getattr(other, k)]


@dataclass
class Solution:
    allocations: dict[int, float] = field(default_factory=dict)
    selected: list[int] = field(default_factory=list)
    status: str = "INFEASIBLE"

    @property
    def feasible(self) -> bool:
        return self.status in ("OPTIMAL", "FEASIBLE")


def _units(amount: float) -> int:
    return int(round(amount / config.ALLOCATION_UNIT))


def _money(units: int) -> float:
    return float(units * config.ALLOCATION_UNIT)


def _group_key(score: ProjectScore, by: str) -> str:
    return (score.region if by == "region" else score.category).strip().lower()


def _inapplicable_caps(scores: list[ProjectScore]) -> list[tuple[str, str, str]]:
    """
    Concentration caps that cannot be enforced because every candidate falls in
    the same group.

    A 40% per-region cap is unsatisfiable when all eight candidates are in one
    state: enforcing it would return an empty portfolio, which helps nobody.
    The cap is therefore skipped -- but skipping it silently would let the
    report claim a violation with no explanation, so each one is named here and
    surfaced to the user as a relaxation.

    Returns (dimension, group name, human label) triples.
    """
    out: list[tuple[str, str, str]] = []
    for by, ratio in (("region", config.REGION_CAP), ("category", config.SECTOR_CAP)):
        groups = {_group_key(s, by) for s in scores}
        if len(groups) == 1 and scores:
            out.append((by, next(iter(groups)),
                        f"{ratio:.0%} per-{by} concentration cap "
                        f"(every candidate is in '{next(iter(groups))}')"))
    return out


# ---------------------------------------------------------------------------
# CP-SAT model
# ---------------------------------------------------------------------------

def _solve(
    scores: list[ProjectScore],
    request: GenerateRequest,
    constraints: ConstraintSet,
    forced: int | None = None,
) -> Solution:
    """
    Build and solve the model for one rung of the ladder.

    `forced` pins a project as funded; used afterwards to work out exactly which
    constraint blocked each excluded project.
    """
    try:
        from ortools.sat.python import cp_model
    except ImportError:  # pragma: no cover - dependency is pinned
        return Solution(status="SOLVER_UNAVAILABLE")

    n = len(scores)
    if n == 0:
        return Solution(status="NO_CANDIDATES")

    budget_units = _units(request.total_csr_budget)
    min_units = _units(request.min_project_funding or 0)
    max_units = _units(request.effective_max()) or budget_units
    max_units = min(max_units, budget_units)
    if min_units > max_units:
        return Solution(status="INVALID_FUNDING_RANGE")

    model = cp_model.CpModel()
    y = [model.NewBoolVar(f"y{i}") for i in range(n)]
    x = [model.NewIntVar(0, max_units, f"x{i}") for i in range(n)]

    for i in range(n):
        # Money flows only to funded projects, and a funded project must clear
        # the manager's minimum ticket size.
        model.Add(x[i] <= max_units * y[i])
        model.Add(x[i] >= min_units * y[i])

    model.Add(sum(x) <= budget_units)

    if forced is not None:
        model.Add(y[forced] == 1)

    eligible = [i for i in range(n)
                if scores[i].final_score >= config.THRESHOLD_REJECT]
    if constraints.min_score:
        for i in range(n):
            if i not in eligible and i != forced:
                model.Add(y[i] == 0)

    wanted = max(1, min(request.number_of_projects, n))
    if constraints.exact_count:
        model.Add(sum(y) == wanted)
    else:
        model.Add(sum(y) <= wanted)
        model.Add(sum(y) >= 1)

    # Concentration caps (PS2 PART 19).
    for by, active in (("region", constraints.region_cap),
                       ("category", constraints.sector_cap)):
        if not active:
            continue
        cap_ratio = config.REGION_CAP if by == "region" else config.SECTOR_CAP
        cap = int(cap_ratio * budget_units)
        groups: dict[str, list[int]] = {}
        for i, score in enumerate(scores):
            groups.setdefault(_group_key(score, by), []).append(i)
        # A cap only bites when the portfolio could actually spread wider.
        if len(groups) > 1:
            for members in groups.values():
                model.Add(sum(x[i] for i in members) <= cap)

    # Category minimums, applied only when such projects are on the table.
    if constraints.category_floors:
        for categories, share in (
            (config.WOMEN_CATEGORIES, config.MIN_WOMEN_SHARE),
            (config.ENVIRONMENT_CATEGORIES, config.MIN_ENVIRONMENT_SHARE),
        ):
            members = [i for i, s in enumerate(scores) if s.category in categories]
            if members:
                model.Add(sum(x[i] for i in members) >= int(share * budget_units))

    score_int = [int(round(s.final_score * 100)) for s in scores]

    # Pass 1: maximise impact weighted by the money actually deployed
    # (PS2 PART 20). Selecting purely on score first would be wrong here:
    # if the five highest-scoring candidates all sit in one state, the 40%
    # per-region cap strands most of the budget. Weighting by allocation makes
    # the solver choose a set it can actually fund.
    model.Maximize(sum(score_int[i] * x[i] for i in range(n)))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config.OPTIMIZER_TIME_LIMIT
    solver.parameters.num_search_workers = 4
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return Solution(status=solver.StatusName(status))

    best_impact = int(round(solver.ObjectiveValue()))

    # Pass 2: among allocations that achieve that impact, prefer the
    # higher-scoring set of projects. This makes the result deterministic
    # rather than leaving equally-good portfolios to solver tie-breaking.
    model.Add(sum(score_int[i] * x[i] for i in range(n)) >= best_impact)
    model.Maximize(sum(score_int[i] * y[i] for i in range(n)))
    status2 = solver.Solve(model)
    if status2 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Pass 1 was feasible, so keep its result rather than discarding a
        # valid portfolio.
        status2 = status

    selected = [i for i in range(n) if solver.Value(y[i]) == 1]
    return Solution(
        allocations={i: _money(solver.Value(x[i])) for i in selected},
        selected=selected,
        status=solver.StatusName(status2),
    )


# The order matters: give up the softest portfolio-shaping rules first and the
# manager's own budget/score intent last.
RELAXATION_LADDER: list[ConstraintSet] = [
    ConstraintSet(),
    ConstraintSet(category_floors=False),
    ConstraintSet(category_floors=False, region_cap=False, sector_cap=False),
    ConstraintSet(category_floors=False, region_cap=False, sector_cap=False,
                  exact_count=False),
    ConstraintSet(category_floors=False, region_cap=False, sector_cap=False,
                  exact_count=False, min_score=False),
]


def _greedy(scores: list[ProjectScore], request: GenerateRequest) -> Solution:
    """
    Last-resort allocation when CP-SAT cannot help.

    Highest score first, respecting only the budget and the per-project range,
    so the system always returns a defensible portfolio rather than nothing.
    """
    budget = request.total_csr_budget
    low = request.min_project_funding or 0
    high = request.effective_max() or budget
    wanted = max(1, min(request.number_of_projects, len(scores)))

    order = sorted(range(len(scores)),
                   key=lambda i: (-scores[i].final_score, scores[i].project_id))
    allocations: dict[int, float] = {}
    remaining = budget

    for i in order:
        if len(allocations) >= wanted:
            break
        share = min(high, remaining / max(wanted - len(allocations), 1))
        if share < low or share <= 0:
            continue
        amount = round(min(share, remaining), 2)
        allocations[i] = amount
        remaining -= amount

    return Solution(allocations=allocations, selected=list(allocations),
                    status="GREEDY" if allocations else "INFEASIBLE")


def _blocking_constraint(
    scores: list[ProjectScore], request: GenerateRequest,
    constraints: ConstraintSet, index: int,
) -> str | None:
    """
    Identify which rule blocks a project, by forcing it in and relaxing one rule
    at a time until the model becomes feasible.
    """
    if _solve(scores, request, constraints, forced=index).feasible:
        return None
    for rule, label in (
        ("category_floors", f"category minimums "
                            f"({config.MIN_WOMEN_SHARE:.0%} women's livelihood, "
                            f"{config.MIN_ENVIRONMENT_SHARE:.0%} environment)"),
        ("region_cap", f"{config.REGION_CAP:.0%} per-region concentration cap"),
        ("sector_cap", f"{config.SECTOR_CAP:.0%} per-sector concentration cap"),
        ("exact_count", f"exact count of {request.number_of_projects} projects"),
        ("min_score", f"minimum score of {config.THRESHOLD_REJECT:.0f}"),
    ):
        if not getattr(constraints, rule):
            continue
        if _solve(scores, request, replace(constraints, **{rule: False}),
                  forced=index).feasible:
            return label
    return "budget and per-project funding limits"


def _explain_exclusion(
    score: ProjectScore, request: GenerateRequest, blocking: str | None,
) -> ExcludedProject:
    if score.final_score < config.THRESHOLD_REJECT:
        return ExcludedProject(
            project_id=score.project_id, project_name=score.project_name,
            final_score=score.final_score,
            reason=(f"Score {score.final_score:.1f} is below the "
                    f"{config.THRESHOLD_REJECT:.0f} minimum for a funding "
                    f"recommendation"),
            binding_constraint="minimum score",
            what_would_change_it=(
                "Strengthen the weakest dimensions, or supply evidence for "
                + ", ".join(score.unknown_fields[:3])
                + " -- unevidenced fields are scored at a neutral prior, not "
                  "credited." if score.unknown_fields else
                "Strengthen the weakest scoring dimensions."),
        )
    if blocking:
        return ExcludedProject(
            project_id=score.project_id, project_name=score.project_name,
            final_score=score.final_score,
            reason=f"Blocked by the {blocking}",
            binding_constraint=blocking,
            what_would_change_it=(
                f"Relaxing the {blocking}, or adding candidates in other "
                f"regions and sectors so the portfolio can satisfy it, would "
                f"let this project be considered."),
        )
    return ExcludedProject(
        project_id=score.project_id, project_name=score.project_name,
        final_score=score.final_score,
        reason=(f"Not selected: {request.number_of_projects} places were filled "
                f"by higher-scoring projects"),
        binding_constraint="portfolio size",
        what_would_change_it=(
            f"Raising the project count above {request.number_of_projects}, or "
            f"improving this project's score above the selected set, would "
            f"bring it into the portfolio."),
    )


def _constraint_checks(
    scores: list[ProjectScore], allocations: dict[int, float],
    request: GenerateRequest,
) -> list[dict]:
    """Verify the returned portfolio against each rule, for the audit trail."""
    total = sum(allocations.values())
    budget = request.total_csr_budget
    checks: list[dict] = [{
        "constraint": "Total budget",
        "limit": budget,
        "actual": round(total, 2),
        "satisfied": total <= budget + 1e-6,
    }]

    for by, ratio in (("region", config.REGION_CAP), ("category", config.SECTOR_CAP)):
        groups: dict[str, float] = {}
        for i, amount in allocations.items():
            key = _group_key(scores[i], by)
            groups[key] = groups.get(key, 0.0) + amount
        skipped = {g for dim, g, _ in _inapplicable_caps(scores) if dim == by}
        for name, amount in sorted(groups.items()):
            entry = {
                "constraint": f"Max {ratio:.0%} per {by}",
                "group": name,
                "limit": round(ratio * budget, 2),
                "actual": round(amount, 2),
                "satisfied": amount <= ratio * budget + 1e-6,
            }
            if name in skipped:
                # Not enforceable against this candidate pool, so it is not a
                # violation -- but the concentration itself is reported loudly.
                entry["satisfied"] = True
                entry["note"] = (
                    f"Not enforceable: every candidate project is in "
                    f"'{name}', so this cap was not applied. The portfolio is "
                    f"{100.0 * amount / budget:.0f}% concentrated in one {by}.")
            checks.append(entry)

    for label, categories, share in (
        ("Minimum women's livelihood share", config.WOMEN_CATEGORIES,
         config.MIN_WOMEN_SHARE),
        ("Minimum environment share", config.ENVIRONMENT_CATEGORIES,
         config.MIN_ENVIRONMENT_SHARE),
    ):
        available = [i for i, s in enumerate(scores) if s.category in categories]
        if not available:
            checks.append({
                "constraint": label, "limit": round(share * budget, 2),
                "actual": 0.0, "satisfied": True,
                "note": "Not applicable: no candidate projects in this category",
            })
            continue
        amount = sum(a for i, a in allocations.items()
                     if scores[i].category in categories)
        checks.append({
            "constraint": label,
            "limit": round(share * budget, 2),
            "actual": round(amount, 2),
            "satisfied": amount >= share * budget - 1e-6,
        })

    for i, amount in allocations.items():
        if request.min_project_funding and amount < request.min_project_funding - 1e-6:
            checks.append({
                "constraint": "Minimum per-project funding",
                "group": scores[i].project_id,
                "limit": request.min_project_funding,
                "actual": round(amount, 2), "satisfied": False,
            })
        if amount > request.effective_max() + 1e-6:
            checks.append({
                "constraint": "Maximum per-project funding",
                "group": scores[i].project_id,
                "limit": request.effective_max(),
                "actual": round(amount, 2), "satisfied": False,
            })
    return checks


def optimize(
    scores: list[ProjectScore], request: GenerateRequest
) -> OptimizationOutcome:
    """
    Recommend a funded portfolio and a fund split.

    Always returns an outcome. When the full constraint set is infeasible the
    ladder relaxes rules in a documented order, and every relaxation is named in
    the constraint report rather than being applied silently.
    """
    if not scores:
        return OptimizationOutcome(constraint_report=ConstraintReport(
            satisfied=False, status="NO_CANDIDATES",
            notes=["No candidate projects were available to optimise over."]))

    solution = Solution()
    applied = RELAXATION_LADDER[0]
    relaxations: list[str] = []
    notes: list[str] = []

    for rung in RELAXATION_LADDER:
        solution = _solve(scores, request, rung)
        if solution.feasible:
            applied = rung
            relaxations = RELAXATION_LADDER[0].describe_dropped(rung)
            break
        if solution.status in ("SOLVER_UNAVAILABLE", "INVALID_FUNDING_RANGE"):
            notes.append(f"Solver could not run ({solution.status}); "
                         f"used greedy allocation instead.")
            break

    if not solution.feasible:
        solution = _greedy(scores, request)
        applied = RELAXATION_LADDER[-1]
        relaxations = ["all portfolio-shaping constraints (greedy fallback)"]
        notes.append(
            "No feasible solution satisfied the constraint set, so allocation "
            "fell back to a greedy highest-score-first split within the budget "
            "and per-project limits.")

    selected = [
        AllocationResult(
            project_id=scores[i].project_id,
            project_name=scores[i].project_name,
            category=scores[i].category,
            region=scores[i].region,
            allocated_amount=round(amount, 2),
            percentage_of_budget=round(100.0 * amount / request.total_csr_budget, 2),
            final_score=scores[i].final_score,
            confidence=scores[i].confidence,
            ngo_id=scores[i].ngo_id,
        )
        for i, amount in sorted(solution.allocations.items(),
                                key=lambda kv: (-scores[kv[0]].final_score,
                                                scores[kv[0]].project_id))
    ]

    chosen = set(solution.allocations)
    is_greedy = solution.status == "GREEDY"

    # Diagnosing which constraint blocked a project costs one CP-SAT solve per
    # relaxation rung, so it is only worth doing for projects that could
    # otherwise have been funded. A project below the score threshold already
    # has its answer, and probing the model for it would be pure waste --
    # at 64 candidates that was several seconds of solving nobody reads.
    # Diagnosis is bounded twice over: to the near-misses (a project below the
    # score threshold already has its answer), and to the highest-scoring few
    # of those. The reader wants to know why their best option missed out, not
    # why the fortieth did, and an unbounded loop here is quadratic in solves.
    candidates_for_diagnosis = sorted(
        (i for i in range(len(scores))
         if i not in chosen and scores[i].final_score >= config.THRESHOLD_REJECT),
        key=lambda i: -scores[i].final_score,
    )[:config.MAX_EXCLUSIONS_DIAGNOSED]
    diagnose = set() if is_greedy else set(candidates_for_diagnosis)

    excluded = []
    for i in range(len(scores)):
        if i in chosen:
            continue
        blocking = (_blocking_constraint(scores, request, applied, i)
                    if i in diagnose else None)
        excluded.append(_explain_exclusion(scores[i], request, blocking))

    checks = _constraint_checks(scores, solution.allocations, request)
    total = sum(solution.allocations.values())

    # Caps that could not bind are relaxations too, and must be reported as
    # such rather than leaving an unexplained violation in the check list.
    for _, group, label in _inapplicable_caps(scores):
        relaxations.append(label)
        notes.append(
            f"Concentration risk: every candidate project sits in '{group}', so "
            f"the {label.split(' (')[0]} could not be applied. Widening the "
            f"search to more regions or sectors would restore portfolio "
            f"diversification.")

    if relaxations:
        notes.append(
            "The full constraint set could not be satisfied with these "
            "candidates. The result relaxes: " + "; ".join(relaxations) + ".")

    # Leaving budget unspent is a real outcome, not an error -- but it must be
    # explained, or the user is left wondering where the money went.
    utilisation = 100.0 * total / request.total_csr_budget if request.total_csr_budget else 0.0
    if utilisation < 90.0 and selected:
        binding = [c for c in checks
                   if not c.get("note")
                   and c["constraint"].startswith("Max ")
                   and c["actual"] >= c["limit"] - 1e-6]
        if binding:
            groups = ", ".join(f"{c['group']}" for c in binding)
            notes.append(
                f"{100.0 - utilisation:.0f}% of the budget is unallocated "
                f"because the concentration cap is already met in: {groups}. "
                f"Adding candidates in other regions or sectors, or raising the "
                f"cap, would let more of the budget be deployed.")
        else:
            notes.append(
                f"{100.0 - utilisation:.0f}% of the budget is unallocated: the "
                f"per-project maximum and the requested project count together "
                f"cap total deployable funding. Increase the project count or "
                f"the per-project maximum to deploy more.")

    return OptimizationOutcome(
        selected=selected,
        excluded=sorted(excluded, key=lambda e: -e.final_score),
        constraint_report=ConstraintReport(
            satisfied=all(c["satisfied"] for c in checks) and not relaxations,
            status=solution.status,
            solver="greedy-fallback" if is_greedy else "CP-SAT",
            relaxations_applied=relaxations,
            checks=checks,
            notes=notes,
        ),
        total_allocated=round(total, 2),
        budget_utilisation=round(100.0 * total / request.total_csr_budget, 2),
    )
