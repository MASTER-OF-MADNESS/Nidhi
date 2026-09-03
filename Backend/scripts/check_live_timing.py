"""Timed live run: how quickly does each section reach the user?"""
import json, sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import httpx

body = json.loads((pathlib.Path(__file__).parent.parent /
                   "tests/fixtures/sample_request.json").read_text())
start = time.perf_counter()
rows = []
with httpx.Client(timeout=600) as c:
    with c.stream("POST", "http://127.0.0.1:8000/generate", json=body) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data: "):
                ev = json.loads(line[6:])
                rows.append((time.perf_counter() - start, ev))

total = rows[-1][0]
print(f"{len(rows)} frames in {total:.1f}s\n")
for t, ev in rows:
    s = ev["section"]
    extra = ""
    if s == "progress":
        extra = f"{ev.get('step')} {ev.get('status')} {ev.get('source') or ''}"
    elif s == "scorecard":
        extra = f"#{ev['project_index']} {ev['content']['project_name'][:44]}"
    elif s == "overview":
        extra = (f"source={ev['content']['data_source']} "
                 f"method={ev['content']['extraction_method']} "
                 f"ngos={ev['content']['ngos_considered']}")
    elif s == "warning":
        extra = ev["content"]["message"][:70]
    elif s == "complete":
        extra = ev.get("run_id", "")
    print(f"  {t:6.1f}s  {s:20} {extra}")

first_card = next((t for t, e in rows if e["section"] == "scorecard"), None)
print(f"\nfirst scorecard at {first_card:.1f}s of a {total:.1f}s run")
