"""
What is and is not reproducible in NIDHI.

A: With retrieval and extraction fixed (knowledge base + rule-based), two runs
   must be byte-identical in every number. This is the deterministic core.
B: With live Tavily and Gemini, the candidate set itself legitimately differs
   between runs -- the web changes and the model proposes different projects.
   The scoring function is still deterministic; its inputs are not.
   This is why every run is written to the audit trail.
"""
import json, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import httpx

BASE = os.getenv("NIDHI_BASE", "http://127.0.0.1:8000")
body = json.loads((pathlib.Path(__file__).parent.parent /
                   "tests/fixtures/sample_request.json").read_text())


def run():
    scores, allocations, prose, insights = {}, {}, {}, ""
    with httpx.Client(timeout=600) as c, \
            c.stream("POST", f"{BASE}/generate", json=body) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev["section"] == "scorecard":
                k = ev["content"]["project_name"]
                scores[k] = ev["content"]["final_score"]
                prose[k] = ev["content"]["explanation"]
            elif ev["section"] == "fund_split":
                for a in ev["content"]["allocations"]:
                    allocations[a["project_name"]] = a["allocated_amount"]
            elif ev["section"] == "strategic_insights":
                insights = ev["content"]["narrative"]
    return scores, allocations, prose, insights


mode = sys.argv[1] if len(sys.argv) > 1 else "both"

if mode in ("offline", "both"):
    print("=== A. Deterministic core (knowledge base + rule-based) ===")
    print("  run 1..."); s1, a1, _, _ = run()
    print("  run 2..."); s2, a2, _, _ = run()
    same_scores = s1 == s2
    same_alloc = a1 == a2
    print(f"  same project set     : {set(s1) == set(s2)} ({len(s1)} projects)")
    print(f"  identical scores     : {same_scores}")
    print(f"  identical allocations: {same_alloc}")
    for k in sorted(set(s1) & set(s2))[:3]:
        print(f"     {s1[k]:6.2f} == {s2[k]:6.2f}   {k[:46]}")
    print("  " + ("PASS: the maths is reproducible and auditable"
                  if same_scores and same_alloc else "FAIL"))

if mode in ("live", "both"):
    print("\n=== B. Live AI (Tavily + Gemini) ===")
    print("  run 1..."); s1, a1, p1, i1 = run()
    print("  run 2..."); s2, a2, p2, i2 = run()
    overlap = set(s1) & set(s2)
    print(f"  projects run 1 / run 2 : {len(s1)} / {len(s2)}")
    print(f"  overlapping names      : {len(overlap)}")
    print(f"  insights prose differs : {i1 != i2}")
    if overlap:
        differing = sum(1 for k in overlap if p1[k] != p2[k])
        print(f"  prose differs on       : {differing}/{len(overlap)} shared projects")
    print("  Candidates differ between runs because live search and model")
    print("  extraction are not reproducible. The scoring function is still")
    print("  deterministic; this is exactly why every run is logged.")
