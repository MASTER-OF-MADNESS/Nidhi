import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import asyncio
from models.schemas import GenerateRequest
from engine.evidence_builder import build_evidence_pack
from engine.gemini_client import extract_candidates
from engine.scoring_engine import score_projects
from engine.ortools_optimizer import optimize

req = GenerateRequest(
    total_csr_budget=20_000_000, number_of_projects=5,
    min_project_funding=1_000_000, max_project_funding=6_000_000,
    states=["Tamil Nadu", "Karnataka"], csr_sector="Education",
    beneficiary_categories=["Children", "Youth"],
    compliance_requirements=["CSR-1"], currency="INR")

pack = asyncio.run(build_evidence_pack(req))
cands, _ = asyncio.run(extract_candidates(pack, req))
scores = score_projects(cands, {n.ngo_id: n for n in pack.ngos}, req, pack.company_profile)
out = optimize(scores, req)

cr = out.constraint_report
print("status:", cr.status, "| solver:", cr.solver)
print("satisfied:", cr.satisfied, "| relaxations:", cr.relaxations_applied)
print(f"allocated: {out.total_allocated:,.0f} of {req.total_csr_budget:,.0f}"
      f" ({out.budget_utilisation}%)\n")
for a in out.selected:
    print(f"  {a.project_name[:42]:44} {a.allocated_amount:>12,.0f}"
          f"  {a.percentage_of_budget:5.1f}%  score {a.final_score}")
print()
for e in out.excluded:
    print(f"  EXCLUDED {e.project_name[:36]:38} {e.final_score:5.1f} | {e.reason[:58]}")
print("\nconstraint checks:")
for c in cr.checks:
    mark = "OK " if c["satisfied"] else "XX "
    print(f"  {mark}{c['constraint']:34} {c.get('group','')!s:14}"
          f" actual={c['actual']:>12,.0f} limit={c['limit']:>12,.0f}")
if cr.notes:
    print("\nnotes:", *cr.notes, sep="\n  ")
