"""Where does a run spend its time? Deterministic stages only (no network)."""
import asyncio, json, sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import config
config.GEMINI_API_KEY = ""; config.TAVILY_API_KEY = ""; config.XAI_API_KEY = ""

from engine import evidence_builder as eb, gemini_client as gc, ngo_matcher
from engine.ortools_optimizer import optimize
from engine.scoring_engine import score_projects
from models.schemas import GenerateRequest
from retrieval import bm25_search, md_parser

req = GenerateRequest(**json.loads(
    (pathlib.Path(__file__).parent.parent / "tests/fixtures/sample_request.json").read_text()))


def timed(label, fn):
    t = time.perf_counter(); out = fn(); ms = (time.perf_counter() - t) * 1000
    print(f"  {label:34} {ms:8.1f} ms"); return out


md_parser.clear_caches(); bm25_search.clear_caches()
timed("parse knowledge base (cold)", md_parser.load_all_ngos)
timed("build BM25 index (cold)", bm25_search.ngo_index)
timed("parse knowledge base (cached)", md_parser.load_all_ngos)

pack = timed("assemble evidence pack", lambda: asyncio.run(eb.build_evidence_pack(req)))
cands = timed("rule-based extraction", lambda: gc.rule_based_candidates(pack, req))
scores = timed("score 8 projects x 10 dims",
               lambda: score_projects(cands, {n.ngo_id: n for n in pack.ngos},
                                      req, pack.company_profile))
out = timed("CP-SAT optimisation", lambda: optimize(scores, req))
funded = [s for s in scores if s.project_id in {a.project_id for a in out.selected}]
timed("NGO matching", lambda: ngo_matcher.match_all(funded, pack.ngos, req))

print()
t = time.perf_counter()
for _ in range(5):
    optimize(scores, req)
print(f"  CP-SAT mean over 5 runs            {(time.perf_counter()-t)/5*1000:8.1f} ms")
print(f"\n  candidates={len(cands)} scored={len(scores)} funded={len(out.selected)}")
