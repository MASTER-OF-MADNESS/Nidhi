"""
NIDHI - CSR Fund Intelligence Platform.

FastAPI entry point. Run with:
    uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import config
from db.database import init_db
from retrieval import bm25_search, md_parser
from routes import auth, company, generate, health, history

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("nidhi")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Prepare the database and warm the knowledge-base parse before serving."""
    init_db()
    log.info("SQLite ready at %s", config.SQLITE_DB_PATH)

    try:
        profile = md_parser.load_company_profile()
        ngos = md_parser.load_all_ngos()
        bm25_search.ngo_index()
        bm25_search.project_index()
        log.info("Knowledge base loaded: %s, %d NGOs, %d historical projects",
                 profile.get("official_name"), len(ngos),
                 len(profile.get("historical_projects", [])))
    except Exception:
        # Parsing failures must not stop the service starting -- the health
        # endpoint will report the problem and routes will surface it clearly.
        log.exception("Knowledge base warm-up failed")

    if not config.GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY is not set: candidate extraction and "
                    "explanations will use rule-based fallbacks.")
    if not config.TAVILY_API_KEY:
        log.warning("TAVILY_API_KEY is not set: retrieval will use the "
                    "Markdown knowledge base.")

    yield
    log.info("NIDHI shutting down")


app = FastAPI(
    title=config.API_TITLE,
    version=config.API_VERSION,
    description=(
        "Decision support for CSR fund allocation. Retrieval is live-first with "
        "a knowledge-base fallback; all scoring and allocation are "
        "deterministic; AI is used only to extract, classify and explain. "
        "Every output is a recommendation for human review."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,   # cannot be combined with allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Type", "Cache-Control"],
)

for module in (auth, health, company, history, generate):
    app.include_router(module.router)


FRONTEND_DIR = config.BASE_DIR.parent / "Frontend"


@app.get("/")
async def root() -> RedirectResponse:
    """Send browsers to the UI; the API index lives at /api."""
    return RedirectResponse(url="/app/")


@app.get("/api")
async def api_index() -> JSONResponse:
    return JSONResponse({
        "name": config.API_TITLE,
        "version": config.API_VERSION,
        "company": config.COMPANY_ID,
        "endpoints": {
            "POST /auth/login": "Sign in with the configured credential",
            "GET  /auth/session": "Which company this deployment serves",
            "POST /generate": "Run the analysis (SSE stream)",
            "GET  /generate/sections": "The SSE contract",
            "GET  /company/{company_id}": "Company profile",
            "GET  /companies": "Registered companies",
            "GET  /history": "Past runs",
            "GET  /history/{run_id}": "One run in full",
            "GET|POST /health": "Service and dependency status",
            "GET  /docs": "Interactive API documentation",
        },
    })


if FRONTEND_DIR.is_dir():
    # html=True serves index.html for the mount root.
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    log.info("Serving the NIDHI UI from %s at /app", FRONTEND_DIR)
else:
    log.warning("Frontend directory not found at %s; API only.", FRONTEND_DIR)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
