"""
End-to-end integration check: replays exactly what the browser sends.

Reads the real wizard defaults out of index.html, builds the payload the way
wizard.js does, and drives the live endpoints.
"""
import json, re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import httpx

BASE = "http://127.0.0.1:8000"
HTML = (pathlib.Path(__file__).resolve().parent.parent.parent
        / "Frontend" / "index.html").read_text(encoding="utf-8")


def selected_value(sel_id):
    block = re.search(rf'<select id="{sel_id}".*?</select>', HTML, re.S).group(0)
    m = re.search(r'<option value="([^"]*)"[^>]*selected', block)
    return m.group(1) if m else re.search(r'<option value="([^"]*)"', block).group(1)


def selected_multi(sel_id):
    block = re.search(rf'<select id="{sel_id}".*?</select>', HTML, re.S).group(0)
    return re.findall(r'<option value="([^"]*)"[^>]*selected', block)


def checked(group):
    return re.findall(rf'<input type="checkbox" checked data-group="{group}" value="([^"]*)"', HTML)


def money(text):
    cleaned = re.sub(r"[^\d.]", "", text or "")
    return float(cleaned) if cleaned else 0.0


def input_value(el_id):
    m = re.search(rf'id="{el_id}"[^>]*value="([^"]*)"', HTML, re.S)
    return m.group(1) if m else ""


payload = {
    "total_csr_budget": money(selected_value("wiz-budget")),
    "funding_type": selected_value("wiz-funding-type"),
    "project_duration": int(selected_value("wiz-duration")),
    "number_of_projects": int(selected_value("wiz-num-projects")),
    "min_project_funding": money(input_value("wiz-min-funding")),
    "max_project_funding": money(input_value("wiz-max-funding")),
    "country": selected_value("wiz-country"),
    "states": selected_multi("wiz-location"),
    "districts": [d.strip() for d in input_value("wiz-districts").split(",") if d.strip()],
    "csr_sector": selected_value("wiz-cause"),
    "csr_sub_sector": "; ".join(checked("subsector")),
    "beneficiary_categories": checked("beneficiary"),
    "ngo_experience": int(selected_value("wiz-ngo-exp")),
    "similar_project_experience": selected_value("wiz-ngo-similar") == "true",
    "geographic_capability": selected_value("wiz-ngo-geo"),
    "ngo_rating": selected_value("wiz-ngo-rating"),
    "compliance_requirements": checked("compliance"),
    "collaboration_type": ", ".join(checked("collaboration")),
    "employee_involvement": True,
    "additional_comments": "Prefer measurable learning outcomes.",
    "currency": "INR",
}

print("PAYLOAD BUILT FROM THE REAL FORM DEFAULTS")
for k, v in payload.items():
    print(f"  {k:28} {v!r}")

with httpx.Client(timeout=180) as c:
    print("\n--- auth ---")
    ok = c.post(f"{BASE}/auth/login",
                json={"username": "temenos_admin", "password": "temenos@nidhi2026"})
    print("  valid creds  ->", ok.status_code, ok.json().get("company_name"))
    bad = c.post(f"{BASE}/auth/login",
                 json={"username": "temenos_admin", "password": "wrong"})
    print("  bad creds    ->", bad.status_code, bad.json().get("detail"))

    print("\n--- generate ---")
    sections, run_id, warnings = [], None, []
    with c.stream("POST", f"{BASE}/generate", json=payload) as r:
        if r.status_code != 200:
            r.read()
            print("  FAILED", r.status_code, r.text[:500]); sys.exit(1)
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            sections.append(ev["section"])
            if ev["section"] == "complete":
                run_id = ev.get("run_id")
            if ev["section"] == "warning":
                warnings.append(ev["content"]["message"])
            if ev["section"] == "fund_split":
                fs = ev["content"]
            if ev["section"] == "ngo_recommendations":
                recs = ev["content"]["recommendations"]

    from collections import Counter
    print("  frames:", len(sections), dict(Counter(sections)))
    print("  run_id:", run_id)
    print(f"  allocated: {fs['total_allocated']:,.0f} / {fs['total_budget']:,.0f}"
          f" ({fs['budget_utilisation']}%)")
    for a in fs["allocations"]:
        print(f"    {a['project_name'][:44]:46} {a['allocated_amount']:>11,.0f}"
              f"  score {a['final_score']}")
    matched = sum(1 for r_ in recs if r_["matches"])
    print(f"  partner matches: {matched}/{len(recs)} projects have an eligible NGO")
    for w in warnings:
        print("  warning:", w[:90])

    print("\n--- history ---")
    h = c.get(f"{BASE}/history").json()
    print(f"  {len(h)} run(s); newest {h[0]['run_id'][:8]} funded {h[0]['projects_funded']}")
