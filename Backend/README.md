# NIDHI — CSR Fund Intelligence Platform (Backend)

AI-assisted decision support for CSR fund allocation. Takes a CSR manager's
requirements, retrieves NGO and project evidence, scores candidates on ten
dimensions, optimises the fund split, matches implementing NGOs, and streams
written explanations — logging every run for audit.

**Everything it produces is a recommendation for a human to decide on.**

## Setup

```bash
cd Backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
cp .env.example .env          # then fill in the two API keys
.venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000
```

Open http://localhost:8000/docs for interactive API docs.

## Tests

```bash
.venv/Scripts/python.exe -m pytest -m "not live"   # offline, no keys needed
.venv/Scripts/python.exe -m pytest -m live         # hits Gemini and Tavily
```

## Design rules

These are enforced in code and covered by tests, not just documented.

| Rule | Where |
|---|---|
| **UNKNOWN is not ZERO.** A missing field scores the neutral prior (50) at confidence 0 and is named for review — never scored as 0. | `engine/scoring_engine.py`, `retrieval/text_utils.clean_value` |
| **AI never scores.** Gemini extracts, classifies and explains. Every number comes from deterministic code. | `engine/gemini_client.py` prompt, `engine/scoring_engine.py` |
| **Model calls are batched.** All project explanations come from one request, not one per project, so a free-tier key (20/day) survives several runs. | `engine/gemini_client.explain_projects` |
| **Tavily first, knowledge base as fallback.** Falls back on failure, timeout, or fewer than 3 usable NGOs. | `engine/evidence_builder.py` |
| **Always return something.** Tavily → MD; Gemini → Grok (xAI) → rule-based; CP-SAT → greedy. | `engine/gemini_client._generate`, `engine/xai_client.py` |
| **Audit everything.** Every run persisted with full inputs and outputs; every score carries its reasons. | `db/crud.py`, `ProjectScore.rca` |
| **Decision support, not approval.** Output says "recommended", never "approved". | `engine/explainer.GUARDRAIL` |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/login` | Sign in. `{username, password}` → `{success, company_id, company_name}`; 401 on mismatch. |
| GET | `/auth/session` | Which company this deployment serves. |
| POST | `/generate` | Run the analysis. Returns an SSE stream. |
| GET | `/generate/sections` | The SSE contract, machine-readable. |
| GET | `/company/{company_id}` | Company profile. 404 if unregistered. |
| GET | `/companies` | Registered companies. |
| GET | `/history` | Past runs, newest first. |
| GET | `/history/{run_id}` | One run with full inputs and outputs. |
| GET/POST | `/health` | Service and dependency status. |

## The SSE stream

`POST /generate` returns `text/event-stream`. Each frame is `data: {json}\n\n`
with a `section` field. Sections arrive in this order:

| Section | Content |
|---|---|
| `progress` | Stage updates (`retrieval`, `extraction`, `scoring`, `optimization`, `ngo_matching`) with `step`, `status`, and `source` on retrieval. |
| `overview` | Run metadata, budget totals, evidence counts by class. |
| `scorecard` | **One frame per project**, with `project_index`. All ten dimensions, `final_score`, `confidence`, `completeness`, `unknown_fields`, and `explanation`. |
| `fund_split` | `allocations`, `excluded` (each with a reason and what would change it), and the `constraint_report`. |
| `ngo_recommendations` | Top NGO matches per funded project, with strengths, limitations and star ratings. |
| `ngo_comparison` | Flat comparison table across all matches. |
| `strategic_insights` | Portfolio narrative plus regional and sector distributions. |
| `human_review_flags` | What a human should check, by severity. |
| `warning` | Non-fatal issue; the pipeline continued via a fallback. Can appear at any point. |
| `complete` | Terminal frame, carries `run_id`. |

### Frontend example

```js
const res = await fetch("http://localhost:8000/generate", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(requestPayload),
});

const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  const frames = buffer.split("\n\n");
  buffer = frames.pop();                    // keep the incomplete tail

  for (const frame of frames) {
    if (!frame.startsWith("data: ")) continue;
    const event = JSON.parse(frame.slice(6));
    switch (event.section) {
      case "progress":   showStage(event.step, event.status); break;
      case "scorecard":  renderCard(event.project_index, event.content); break;
      case "fund_split": renderSplit(event.content); break;
      case "complete":   finish(event.run_id); break;
    }
  }
}
```

Note `EventSource` cannot be used: it only issues GET requests, and `/generate`
needs a POST body. Use `fetch` with a stream reader as above.

## Scoring model

Ten dimensions, weights from the NIDHI specification (`config.SCORING_WEIGHTS`):

| # | Dimension | Weight | # | Dimension | Weight |
|---|---|---|---|---|---|
| 1 | Strategic alignment | 20% | 6 | Feasibility | 8% |
| 2 | Social impact | 20% | 7 | NGO capability | 7% |
| 3 | Cost effectiveness | 15% | 8 | NGO credibility | 5% |
| 4 | Beneficiary relevance | 10% | 9 | Risk | 3% |
| 5 | Geographic need | 10% | 10 | Sustainability | 2% |

Thresholds: below 60 REJECT, 60–75 REVIEW, above 75 FUND.

`final_score`, `confidence` and `completeness` are three independent numbers. A
project can score well on thin evidence — that is what `confidence` is for.

## Data

Four research Markdown files under `data/`, mapped in `config.py`:

| File | Contents |
|---|---|
| `Temenos_CSR_Knowledge_Base_PS2.md` | Company profile, ESG pillars, priority themes, 15 historical projects, cost benchmarks, risk indicators, scoring and constraint models |
| `master_knowledge_base_1.md` | 12 organisations with evidenced Temenos relationships |
| `ngo_csr_requirements_verified_fields_2.md` | The 70-NGO candidate pool |
| `top_70_ngo_backup_dataset.md` | Rank and compliance signals, merged into the pool by name |

Blank fields and the sentinels `NOT PUBLICLY DISCLOSED`, `Requires live
verification`, `NOT VERIFIED / DO NOT ASSUME` all parse to `None`. They mean
"not found", never zero.

## Multi-company

Login resolves to a `company_id`; retrieval, scoring context and run logs all
key off it via `config.COMPANY_REGISTRY`. Temenos is the only registered
company today — adding another is a registry entry plus its data files, not a
code change.
