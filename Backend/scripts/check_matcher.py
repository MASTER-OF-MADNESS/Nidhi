import sys, pathlib, asyncio
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from models.schemas import GenerateRequest
from engine.evidence_builder import build_evidence_pack
from engine.gemini_client import extract_candidates
from engine.scoring_engine import score_projects
from engine.ortools_optimizer import optimize
from engine.ngo_matcher import match_all, build_comparison

req = GenerateRequest(
    total_csr_budget=20_000_000, number_of_projects=4,
    min_project_funding=1_000_000, max_project_funding=8_000_000,
    states=["Tamil Nadu", "Karnataka"], csr_sector="Education",
    beneficiary_categories=["Children", "Youth"], ngo_experience=0,
    compliance_requirements=["CSR-1", "12A"], currency="INR")

pack = asyncio.run(build_evidence_pack(req))
cands, _ = asyncio.run(extract_candidates(pack, req))
scores = score_projects(cands, {n.ngo_id: n for n in pack.ngos}, req, pack.company_profile)
out = optimize(scores, req)

funded_ids = {a.project_id for a in out.selected}
funded = [s for s in scores if s.project_id in funded_ids]
recs = match_all(funded, pack.ngos, req)

for r in recs:
    print(f"\n{r.project_name[:60]}")
    print(f"  ({r.ineligible_count} NGOs failed hard eligibility)")
    for m in r.matches:
        print(f"  * {m.ngo_name[:36]:38} {m.compatibility_score:5.1f}  "
              f"{'*' * int(m.star_rating)}{'.' if m.star_rating % 1 else ''}"
              f"  partner={m.temenos_partner}")
        print(f"      + {m.strengths[0] if m.strengths else '-'}")
        print(f"      - {m.limitations[0] if m.limitations else '-'}")

comp = build_comparison(recs)
print(f"\ncomparison rows: {len(comp['rows'])} | weights: {comp['weights']}")
