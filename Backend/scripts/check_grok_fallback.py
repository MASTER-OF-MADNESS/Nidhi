"""
Prove the Grok path end to end.

The live xAI account has no credits, so this drives the real chain with Gemini
disabled and the xAI transport stubbed at the HTTP boundary -- the same code
path a funded key would take.
"""
import asyncio, json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx
import config
config.GEMINI_API_KEY = ""            # force the chain past Gemini
config.XAI_API_KEY = "stub-key"

from engine import gemini_client as gc, evidence_builder as eb, xai_client
from engine.scoring_engine import score_projects
from models.schemas import GenerateRequest

req = GenerateRequest(**json.loads(
    (pathlib.Path(__file__).parent.parent / "tests/fixtures/sample_request.json").read_text()))


def make_transport(pack):
    """Answer like a funded Grok account would."""
    names = [n.name for n in pack.ngos[:4]]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        if "candidate CSR projects" in prompt:
            content = json.dumps({"projects": [{
                "project_name": f"Grok-proposed digital literacy programme with {n}",
                "category": "Education", "region": "Tamil Nadu",
                "derived_from_ngo": n, "estimated_budget": 4_000_000,
                "estimated_beneficiaries": 1500, "sdgs": [4, 10],
                "alignment_score": 8, "uncertain_fields": [],
            } for n in names]})
        else:
            content = json.dumps({"explanations": [
                {"project_id": f"CAND-{i:02d}",
                 "text": "Written by Grok: this project is prioritised for its "
                         "reach and sector fit. Figures remain recommendations "
                         "for human review."}
                for i in range(1, len(names) + 1)]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


async def main():
    pack = await eb.build_evidence_pack(req)
    transport = make_transport(pack)
    real = httpx.AsyncClient

    def patched(*a, **kw):
        kw["transport"] = transport
        return real(*a, **kw)

    httpx.AsyncClient = patched
    try:
        candidates, warnings = await gc.extract_candidates(pack, req)
        print(f"providers enabled : {gc._enabled_providers()}")
        print(f"answered by       : {gc.last_provider}")
        print(f"candidates        : {len(candidates)}")
        for c in candidates[:3]:
            print(f"   [{c.extraction_method}] {c.project_name[:62]}")
        for w in warnings:
            print(f"   warning: {w[:96]}")

        scores = score_projects(candidates, {n.ngo_id: n for n in pack.ngos},
                                req, pack.company_profile)
        items = [(s, None, None) for s in scores]
        explanations, ex_warnings = await gc.explain_projects(items, req)
        wrote = sum(1 for t in explanations.values() if t.startswith("Written by Grok"))
        print(f"explanations      : {wrote}/{len(explanations)} written by Grok")
        for w in ex_warnings:
            print(f"   warning: {w[:96]}")

        ok = (gc.last_provider == "xai" and candidates
              and all(c.extraction_method == "xai" for c in candidates)
              and wrote == len(explanations))
        print("\n" + ("PASS: Grok serves the full pipeline when Gemini is down"
                      if ok else "FAIL"))
    finally:
        httpx.AsyncClient = real

asyncio.run(main())
