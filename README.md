# NIDHI — CSR Fund Intelligence Platform

AI-assisted decision support for Temenos CSR fund allocation. A CSR manager
enters their requirements; NIDHI retrieves NGO and project evidence, scores
candidates on ten dimensions, optimises the fund split, matches implementing
partners, and streams written explanations — logging every run for audit.

**Everything it produces is a recommendation for a human to decide on.**

## Run it

```bash
cd Backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
cp .env.example .env                                          # add your API keys
.venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000
```

Then open **http://localhost:8000** — the backend serves the UI, so there is no
second server and no CORS setup.

Sign in with the credentials in `.env` (defaults `temenos_admin` /
`temenos@nidhi2026`, pre-filled on the login screen).

> **These demo credentials are published in this repository and are not a
> secret.** Change `NIDHI_PASSWORD` in your local `.env` before exposing the app
> anywhere beyond localhost. `.env` is gitignored, so your change stays local.

## API keys and quotas

`Backend/.env` holds both keys (it is gitignored). Two things to know:

- **Model name matters.** `gemini-1.5-flash` and `gemini-2.5-flash` are closed
  to new keys; the API returns 404 and names the replacement. `GEMINI_MODEL` in
  `.env` is currently `gemini-3.6-flash`. If the key is reprovisioned, change
  that one line — nothing else refers to a model.
- **The free tier allows 20 Gemini requests per day.** A run costs about 4
  (extraction, batched explanations, portfolio insights), so roughly 5 runs a
  day. Exceeding it returns HTTP 429 and NIDHI falls back to rule-based
  extraction and deterministic prose — the run still completes, with a warning
  frame saying so. A paid key removes the limit.

Tavily has no such constraint at this volume.

## Layout

```
Backend/     FastAPI app, engines, knowledge base, tests
Frontend/    The UI (vanilla HTML/CSS/JS), served at /app
tests-e2e/   Browser-level tests driving the real UI against a live backend
```

## Tests

```bash
cd Backend && .venv/Scripts/python.exe -m pytest -m "not live"   # 126 offline
cd Backend && .venv/Scripts/python.exe -m pytest -m live         # needs API keys
cd tests-e2e && node e2e_test.js                                 # needs server up
```

`Backend/scripts/` holds diagnostic runners (`check_integration.py`,
`check_optimizer.py`, `check_matcher.py`, `check_progressive.py`).

## How a run works

1. **Retrieval** — Tavily live search first; the Markdown knowledge base is the
   fallback when it fails, times out, or returns fewer than three usable NGOs.
   Candidates are drawn per requested state so one state cannot crowd out another.
2. **Extraction** — Gemini turns the evidence into candidate projects, each
   anchored to a real retrieved NGO. A rule-based path covers model failure.
3. **Scoring** — ten dimensions, pure deterministic maths, no AI.
4. **Optimisation** — OR-Tools CP-SAT allocates the budget, maximising impact
   weighted by money deployed, under the portfolio constraints.
5. **Matching** — hard eligibility filters, then weighted compatibility.
6. **Explanation** — Gemini writes the narrative; deterministic prose covers failure.

## Design rules

| Rule | Enforced in |
|---|---|
| **UNKNOWN is not ZERO.** A missing field scores a neutral prior at zero confidence and is named for review. | `engine/scoring_engine.py`, `retrieval/text_utils.clean_value` |
| **AI never scores.** Gemini extracts, classifies and explains. Every number is deterministic. | `engine/gemini_client.py`, `engine/scoring_engine.py` |
| **Always return something.** Tavily → knowledge base; Gemini → rules; CP-SAT → greedy. | every engine module |
| **Relaxations are never silent.** Any constraint the solver drops is named in the response. | `engine/ortools_optimizer.py` |
| **Audit everything.** Every run persisted with full inputs and outputs. | `db/crud.py` |
| **Decision support, not approval.** Output says "recommended", never "approved". | `engine/explainer.GUARDRAIL` |

### What is reproducible

Scoring and optimisation are pure functions: **identical inputs always give
identical scores and allocations**, verified by unit tests and by
`scripts/check_determinism.py offline`.

End-to-end runs are a different matter. With live search and model extraction,
the *candidate set itself* can differ between two identical requests — the web
moves and the model proposes different projects. Observed in practice: one pair
of runs produced 5 and 8 candidates with no overlap; another produced the same
8 both times. That is inherent to live retrieval, not a defect — and it is
precisely why every run is written to the audit trail with its full inputs and
outputs, so any past recommendation can be reconstructed even though re-running
it may not reproduce it.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/login` | Sign in; returns the company identity |
| POST | `/generate` | Run the analysis (SSE stream) |
| GET | `/generate/sections` | The SSE contract, machine-readable |
| GET | `/company/{id}` | Company profile (404 if unregistered) |
| GET | `/history`, `/history/{run_id}` | Audit trail |
| GET/POST | `/health` | Service and dependency status |
| GET | `/api`, `/docs` | Endpoint index, interactive docs |

Full SSE section reference is in [Backend/README.md](Backend/README.md).
