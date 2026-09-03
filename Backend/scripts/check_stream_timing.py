"""Confirm SSE frames arrive progressively rather than in one buffered burst."""
import json, sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import httpx

body = json.loads((pathlib.Path(__file__).parent.parent /
                   "tests/fixtures/sample_request.json").read_text())
start = time.perf_counter()
stamps = []
with httpx.Client(timeout=180) as client:
    with client.stream("POST", "http://127.0.0.1:8000/generate", json=body) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data: "):
                stamps.append((time.perf_counter() - start,
                               json.loads(line[6:])["section"]))

print(f"{len(stamps)} frames over {stamps[-1][0]:.2f}s\n")
for t, section in stamps:
    print(f"  {t:6.3f}s  {section}")
distinct = len({round(t, 3) for t, _ in stamps})
print(f"\ndistinct arrival times: {distinct}/{len(stamps)}")
print("PROGRESSIVE" if distinct > len(stamps) * 0.5 else "LOOKS BUFFERED")
