"""
NIDHI - CSR Fund Intelligence Platform
Central configuration.

Every tunable lives here so that no magic numbers appear in engine logic.
Values that vary by deployment are read from .env; values that encode domain
knowledge (scoring weights, cost benchmarks, Temenos priority themes) are
derived from the knowledge-base Markdown files and documented with their source.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key) or default)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key) or default)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = Path(_env("MD_DATA_PATH") or (BASE_DIR / "data"))
if not DATA_DIR.is_absolute():
    DATA_DIR = (BASE_DIR / DATA_DIR).resolve()

# The master prompt refers to these as temenos_company.md / master_ngo.md /
# ngo_pool.md. We keep the original research filenames and map them here rather
# than duplicating ~300KB of Markdown, which would be free to drift out of sync.
TEMENOS_COMPANY_MD = DATA_DIR / "Temenos_CSR_Knowledge_Base_PS2.md"
MASTER_NGO_MD = DATA_DIR / "master_knowledge_base_1.md"
NGO_POOL_MD = DATA_DIR / "ngo_csr_requirements_verified_fields_2.md"
# Not named in the master prompt, but it carries per-NGO rank and compliance
# signals that the pool file leaves blank. Merged by normalised name.
NGO_BACKUP_MD = DATA_DIR / "top_70_ngo_backup_dataset.md"

SQLITE_DB_PATH = Path(_env("SQLITE_DB_PATH") or (BASE_DIR / "db" / "nidhi.db"))
if not SQLITE_DB_PATH.is_absolute():
    SQLITE_DB_PATH = (BASE_DIR / SQLITE_DB_PATH).resolve()


# ---------------------------------------------------------------------------
# External services
# ---------------------------------------------------------------------------

GEMINI_API_KEY = _env("GEMINI_API_KEY")
TAVILY_API_KEY = _env("TAVILY_API_KEY")

# gemini-1.5-flash is deprecated; google-genai is the current SDK. Overridable
# so a key provisioned for a different model needs only an .env change.
GEMINI_MODEL = _env("GEMINI_MODEL") or "gemini-2.5-flash"
GEMINI_TIMEOUT = _env_int("GEMINI_TIMEOUT", 60)
GEMINI_MAX_RETRIES = _env_int("GEMINI_MAX_RETRIES", 2)

# xAI (Grok) is the second model provider: used when Gemini is unavailable,
# rate-limited, or out of quota, before falling back to rule-based output.
XAI_API_KEY = _env("XAI_API_KEY")
XAI_MODEL = _env("XAI_MODEL") or "grok-4-fast"
XAI_BASE_URL = _env("XAI_BASE_URL") or "https://api.x.ai/v1"
XAI_TIMEOUT = _env_int("XAI_TIMEOUT", 60)

# Order the providers are tried in. Trimmed automatically to whichever have
# keys configured.
LLM_PROVIDER_ORDER = tuple(
    p.strip().lower() for p in (_env("LLM_PROVIDER_ORDER") or "gemini,xai").split(",")
    if p.strip()
)

TAVILY_TIMEOUT = _env_int("TAVILY_TIMEOUT", 8)
MAX_TAVILY_RESULTS = _env_int("MAX_TAVILY_RESULTS", 10)
# Below this many usable NGOs, Tavily is considered insufficient -> MD fallback.
TAVILY_MIN_NGOS = _env_int("TAVILY_MIN_NGOS", 3)

STREAMING_ENABLED = _env_bool("STREAMING_ENABLED", True)


# ---------------------------------------------------------------------------
# Authentication (deliberately simple: one hardcoded credential from .env)
# ---------------------------------------------------------------------------

NIDHI_USERNAME = _env("NIDHI_USERNAME") or "temenos_admin"
NIDHI_PASSWORD = _env("NIDHI_PASSWORD") or "temenos@nidhi2026"
COMPANY_ID = _env("COMPANY_ID") or "temenos"


# ---------------------------------------------------------------------------
# Company registry
#
# NIDHI is designed as a multi-company platform. Temenos is the only registered
# company today. Every module takes company_id and resolves through this map,
# so registering a second company is a data change, not a code change.
# ---------------------------------------------------------------------------

COMPANY_REGISTRY: dict[str, dict] = {
    "temenos": {
        "company_id": "temenos",
        "display_name": "Temenos",
        "legal_name": "Temenos AG",
        "headquarters": "Geneva, Switzerland",
        "founded": 1993,
        "country": "Switzerland",
        "default_currency": "INR",
        "company_md": TEMENOS_COMPANY_MD,
        "master_ngo_md": MASTER_NGO_MD,
        "ngo_pool_md": NGO_POOL_MD,
        "ngo_backup_md": NGO_BACKUP_MD,
    }
}


def get_company(company_id: str) -> dict | None:
    """Resolve a company_id to its registry entry, or None if not registered."""
    return COMPANY_REGISTRY.get((company_id or "").strip().lower())


# ---------------------------------------------------------------------------
# Scoring model
#
# Ten dimensions, weights per the NIDHI master specification. The knowledge base
# (PS2 PART 18) proposes Risk 5 / Sustainability 5 instead of 3 / 2; the master
# spec wins, and changing it is a one-line edit here.
# ---------------------------------------------------------------------------

SCORING_WEIGHTS: dict[str, float] = {
    "strategic_alignment": 20.0,
    "social_impact": 20.0,
    "cost_effectiveness": 15.0,
    "beneficiary_relevance": 10.0,
    "geographic_need": 10.0,
    "feasibility": 8.0,
    "ngo_capability": 7.0,
    "ngo_credibility": 5.0,
    "risk": 3.0,
    "sustainability": 2.0,
}

SCORE_DIMENSIONS: list[str] = list(SCORING_WEIGHTS.keys())

# PS2 PART 18: <60 reject, 60-75 review, >75 recommend.
THRESHOLD_REJECT = _env_float("THRESHOLD_REJECT", 60.0)
THRESHOLD_RECOMMEND = _env_float("THRESHOLD_RECOMMEND", 75.0)

# Score assigned to a dimension whose inputs are entirely UNKNOWN.
# A neutral prior, NOT zero -- an absent field is not a negative finding.
# Confidence for that dimension drops to 0 and the field is surfaced for review.
NEUTRAL_PRIOR = 50.0

# Runs below these levels are flagged for human review.
LOW_CONFIDENCE_THRESHOLD = _env_float("LOW_CONFIDENCE_THRESHOLD", 0.55)
LOW_COMPLETENESS_THRESHOLD = _env_float("LOW_COMPLETENESS_THRESHOLD", 50.0)


# ---------------------------------------------------------------------------
# Cost-effectiveness benchmarks (PS2 PART 16: cost per beneficiary is PRIMARY)
#
# Cost per beneficiary, by category, per currency. "good" = highly efficient;
# above "poor" is expensive for the category. Scored by linear interpolation
# between the anchors in scoring_engine.score_cost_effectiveness.
# ---------------------------------------------------------------------------

COST_BENCHMARKS: dict[str, dict[str, dict[str, float]]] = {
    "INR": {
        "education":            {"good": 2000, "median": 6000, "poor": 18000},
        "healthcare":           {"good": 1500, "median": 5000, "poor": 15000},
        "women_livelihood":     {"good": 8000, "median": 20000, "poor": 50000},
        "skill_development":    {"good": 6000, "median": 18000, "poor": 45000},
        "environment":          {"good": 200, "median": 1000, "poor": 5000},
        "water_sanitation":     {"good": 1200, "median": 4000, "poor": 12000},
        "disability_inclusion": {"good": 10000, "median": 25000, "poor": 60000},
        "youth_development":    {"good": 2500, "median": 8000, "poor": 22000},
        "poverty_alleviation":  {"good": 5000, "median": 15000, "poor": 40000},
        "financial_inclusion":  {"good": 800, "median": 3000, "poor": 10000},
        "food_nutrition":       {"good": 1000, "median": 3500, "poor": 10000},
        "default":              {"good": 3000, "median": 10000, "poor": 30000},
    },
    "USD": {
        "education":            {"good": 24, "median": 72, "poor": 216},
        "healthcare":           {"good": 18, "median": 60, "poor": 180},
        "women_livelihood":     {"good": 96, "median": 240, "poor": 600},
        "skill_development":    {"good": 72, "median": 216, "poor": 540},
        "environment":          {"good": 3, "median": 12, "poor": 60},
        "water_sanitation":     {"good": 15, "median": 48, "poor": 144},
        "disability_inclusion": {"good": 120, "median": 300, "poor": 720},
        "youth_development":    {"good": 30, "median": 96, "poor": 264},
        "poverty_alleviation":  {"good": 60, "median": 180, "poor": 480},
        "financial_inclusion":  {"good": 10, "median": 36, "poor": 120},
        "food_nutrition":       {"good": 12, "median": 42, "poor": 120},
        "default":              {"good": 36, "median": 120, "poor": 360},
    },
}

SUPPORTED_CURRENCIES = ("INR", "USD")
CURRENCY_SYMBOLS = {"INR": "Rs.", "USD": "$"}

# Used only to reconcile the disclosed Temenos USD 2.8M community investment
# against an INR-denominated request. Not a live FX feed.
FX_USD_TO_INR = _env_float("FX_USD_TO_INR", 83.0)


# ---------------------------------------------------------------------------
# Temenos strategic taxonomy
# Source: master_knowledge_base_1.md section 8 (Tier A/B/C) and
#         Temenos_CSR_Knowledge_Base_PS2.md PART 3 (priority themes).
#
# Tier A = explicitly documented community priorities
# Tier B = demonstrated through actual delivered projects
# Tier C = broader ESG / business alignment (NOT automatically charitable)
# ---------------------------------------------------------------------------

THEME_TIERS: dict[str, list[str]] = {
    "A": [
        "poverty alleviation", "local economic development", "children",
        "youth development", "emergency relief", "community volunteering",
        "environmental volunteering", "un international days",
    ],
    "B": [
        "education", "digital inclusion", "women empowerment",
        "women livelihood", "disability inclusion",
        "environmental sustainability", "reforestation", "biodiversity",
        "healthcare", "community health", "sustainable mobility",
        "circular economy", "skill development",
    ],
    "C": [
        "financial inclusion", "technology and innovation",
        "access to financial services", "responsible technology",
        "employee engagement",
    ],
}

TIER_SCORES: dict[str, float] = {"A": 100.0, "B": 80.0, "C": 55.0}

# Geographies with documented Temenos CSR presence (PS2 PART 4).
# Used INVERSELY for geographic need: a region Temenos already serves scores
# lower on need than a comparable region it does not.
TEMENOS_PRESENCE: dict[str, float] = {
    "chennai": 1.0, "tamil nadu": 0.9, "bangalore": 0.8, "bengaluru": 0.8,
    "karnataka": 0.7, "telangana": 0.6, "hyderabad": 0.5, "india": 0.4,
    "romania": 0.9, "bucharest": 1.0, "kenya": 0.8, "cambodia": 0.3,
    "peru": 0.3, "ethiopia": 0.3, "saudi arabia": 0.3,
}

# PS2 PART 9 / PART 18 dimension 4.
TEMENOS_TARGET_GROUPS: list[str] = [
    "women", "children", "youth", "disabled", "persons with disabilities",
    "underserved", "underprivileged", "marginalized", "rural", "tribal",
    "girls", "students", "elderly", "single mothers",
]

TEMENOS_SDGS: list[int] = [1, 4, 5, 6, 8, 9, 10, 11, 12, 13, 15]

# States with recognised high development need, used as a positive geographic
# need signal. Derived from PS2 PART 14 need variables (MPI / SDG index).
HIGH_NEED_REGIONS: dict[str, float] = {
    "odisha": 0.85, "andhra pradesh": 0.6, "telangana": 0.55,
    "tamil nadu": 0.4, "karnataka": 0.45, "kerala": 0.25,
}

# Free-text sector -> canonical category key (must match COST_BENCHMARKS keys).
CATEGORY_ALIASES: dict[str, str] = {
    "education": "education", "school": "education", "literacy": "education",
    "scholarship": "education", "stem": "education", "teaching": "education",
    "health": "healthcare", "healthcare": "healthcare", "medical": "healthcare",
    "nutrition": "food_nutrition", "food": "food_nutrition",
    "hunger": "food_nutrition",
    "women": "women_livelihood", "gender": "women_livelihood",
    "livelihood": "women_livelihood", "womens livelihood": "women_livelihood",
    "skill": "skill_development", "employment": "skill_development",
    "vocational": "skill_development", "employability": "skill_development",
    "environment": "environment", "climate": "environment",
    "tree": "environment", "reforestation": "environment",
    "biodiversity": "environment", "waste": "environment",
    "circular economy": "environment", "energy": "environment",
    "water": "water_sanitation", "sanitation": "water_sanitation",
    "wash": "water_sanitation", "hygiene": "water_sanitation",
    "disability": "disability_inclusion", "disabled": "disability_inclusion",
    "inclusion": "disability_inclusion", "autism": "disability_inclusion",
    "blind": "disability_inclusion",
    "youth": "youth_development", "child": "youth_development",
    "children": "youth_development", "sports": "youth_development",
    "poverty": "poverty_alleviation", "rural": "poverty_alleviation",
    "rural development": "poverty_alleviation",
    "governance": "poverty_alleviation",
    "financial inclusion": "financial_inclusion",
    "digital inclusion": "financial_inclusion",
    "microfinance": "financial_inclusion",
}

# Categories the optimizer applies minimum-allocation floors to (PS2 PART 19).
WOMEN_CATEGORIES = ("women_livelihood",)
ENVIRONMENT_CATEGORIES = ("environment", "water_sanitation")


# ---------------------------------------------------------------------------
# Optimizer (OR-Tools CP-SAT). Source: PS2 PART 19 / PART 20.
#
# CP-SAT is integer-only. The master prompt suggests scaling money by 100
# (paise/cents); on a 20,000,000 budget that is 2e9 units and needlessly
# inflates the solver domains. We work in ALLOCATION_UNIT-sized buckets
# instead: identical fidelity for a funding decision, far smaller search space.
# ---------------------------------------------------------------------------

ALLOCATION_UNIT = _env_int("ALLOCATION_UNIT", 1000)
REGION_CAP = _env_float("REGION_CAP", 0.40)
SECTOR_CAP = _env_float("SECTOR_CAP", 0.40)
MIN_WOMEN_SHARE = _env_float("MIN_WOMEN_SHARE", 0.15)
MIN_ENVIRONMENT_SHARE = _env_float("MIN_ENVIRONMENT_SHARE", 0.15)
OPTIMIZER_TIME_LIMIT = _env_float("OPTIMIZER_TIME_LIMIT", 10.0)
# Working out which constraint blocked a project costs a solve per
# relaxation rung, so only the strongest near-misses are diagnosed.
MAX_EXCLUSIONS_DIAGNOSED = _env_int("MAX_EXCLUSIONS_DIAGNOSED", 10)

# Selection must dominate the allocation term in the blended objective, so that
# the solver never drops a high-scoring project to shift money elsewhere.
OBJECTIVE_SELECTION_WEIGHT = 1_000_000


# ---------------------------------------------------------------------------
# Evidence classification (master spec section 3; master_knowledge_base_1.md 1)
# ---------------------------------------------------------------------------

EVIDENCE_VERIFIED_OFFICIAL = "VERIFIED_OFFICIAL"
EVIDENCE_VERIFIED_SECONDARY = "VERIFIED_SECONDARY"
EVIDENCE_INFERENCE = "INFERENCE"
EVIDENCE_UNKNOWN = "UNKNOWN"

DATA_SOURCE_TAVILY = "TAVILY_PRIMARY"
DATA_SOURCE_MD = "MD_FALLBACK"

# Strings that appear in the knowledge base to mean "we looked and did not find
# this", plus plain blanks. All map to None/UNKNOWN -- never to 0 or False.
UNKNOWN_SENTINELS: tuple[str, ...] = (
    "", "-", "--", "n/a", "na", "none", "null", "tbd", "unknown",
    "not disclosed",
    "not publicly disclosed",
    "not publicly disclosed in this backup dataset",
    "not verified in this dataset",
    "not verified / do not assume",
    "not verified",
    "requires live verification",
    "requires live due diligence",
    "not found",
)

# Compliance credentials recognised for Indian CSR (PS2 PART 10).
COMPLIANCE_CREDENTIALS = ("CSR-1", "12A", "80G", "FCRA")

# PS2 PART 17 risk indicators, keyed by detection phrase -> severity.
RISK_SEVERITY = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


# ---------------------------------------------------------------------------
# NGO matching model (master spec section 7)
# ---------------------------------------------------------------------------

NGO_MATCH_WEIGHTS: dict[str, float] = {
    "sector": 30.0,
    "geography": 25.0,
    "experience": 20.0,
    "compliance": 15.0,
    "temenos_relationship": 10.0,
}

TEMENOS_PARTNER_BONUS = 10.0
NGO_MATCHES_PER_PROJECT = _env_int("NGO_MATCHES_PER_PROJECT", 2)
MAX_CANDIDATE_PROJECTS = _env_int("MAX_CANDIDATE_PROJECTS", 8)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

CORS_ORIGINS = ["*"]
API_TITLE = "NIDHI - CSR Fund Intelligence Platform"
API_VERSION = "1.0.0"
SSE_KEEPALIVE_SECONDS = _env_float("SSE_KEEPALIVE_SECONDS", 15.0)
