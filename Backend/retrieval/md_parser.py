"""
Parsers for the four knowledge-base Markdown files.

The files use three recurring shapes, so the primitives below handle all of it:
  1. pipe tables            -> parse_tables()
  2. "- **Field:** value"   -> parse_field_bullets()
  3. heading-delimited blocks -> iter_sections()

Results are cached: the files are static research artefacts, read once per
process and reused for every request.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import config
from models.schemas import NGORecord
from retrieval.text_utils import (
    canonical_category,
    clean_value,
    extract_sdgs,
    normalize_name,
    parse_bool,
    parse_int,
    split_list,
    strip_markdown,
)

_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+\*\*(?P<field>[^:*]+?):?\*\*\s*(?P<value>.*)$")


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    """Read a knowledge-base file, tolerating a YAML front-matter block."""
    text = Path(path).read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    return text


def _split_row(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in row.split("|")]


def parse_tables(text: str) -> list[list[dict[str, str]]]:
    """
    Extract every pipe table in `text` as a list of header-keyed row dicts.

    Header keys are lowercased and underscored so that "Project Name" becomes
    "project_name".
    """
    tables: list[list[dict[str, str]]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if "|" in lines[i] and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            headers = [
                re.sub(r"[^a-z0-9]+", "_", strip_markdown(h).lower()).strip("_") or f"col{n}"
                for n, h in enumerate(_split_row(lines[i]))
            ]
            rows: list[dict[str, str]] = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = _split_row(lines[i])
                if len(cells) < len(headers):
                    cells += [""] * (len(headers) - len(cells))
                rows.append({h: cells[n] for n, h in enumerate(headers)})
                i += 1
            if rows:
                tables.append(rows)
            continue
        i += 1
    return tables


def parse_field_bullets(text: str) -> dict[str, str]:
    """Turn a block of `- **Field:** value` lines into a dict keyed by field."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = _BULLET_RE.match(line)
        if not match:
            continue
        key = re.sub(r"[^a-z0-9]+", "_",
                     strip_markdown(match.group("field")).lower()).strip("_")
        if key:
            fields[key] = match.group("value").strip()
    return fields


def iter_sections(text: str, level: int) -> Iterator[tuple[str, str]]:
    """
    Yield (heading, body) for every heading at exactly `level`.

    A body runs until the next heading of the same or a shallower level, so a
    "## NGO-001" block keeps its own "### Temenos relationship" subsections.
    """
    marker = "#" * level
    pattern = re.compile(rf"^{marker} (?!#)(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    for n, match in enumerate(matches):
        start = match.end()
        end = len(text)
        for shallower in range(1, level + 1):
            nxt = re.compile(rf"^{'#' * shallower} (?!#)", re.MULTILINE).search(text, start)
            if nxt and nxt.start() < end:
                end = nxt.start()
        yield strip_markdown(match.group(1)).strip(), text[start:end]


def find_section(text: str, title_contains: str, level: int = 1) -> str:
    """Body of the first heading at `level` whose title contains the phrase."""
    needle = title_contains.lower()
    for heading, body in iter_sections(text, level):
        if needle in heading.lower():
            return body
    return ""


# ---------------------------------------------------------------------------
# NGO pool: ngo_csr_requirements_verified_fields_2.md (70 candidates)
#   + top_70_ngo_backup_dataset.md merged in by normalised name
# ---------------------------------------------------------------------------

_NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_CODED_HEADING_RE = re.compile(r"^\s*([A-Z]{2}-\d{2})\s*[-\u2013\u2014]\s*(.*)$")

_COMPLIANCE_LOOKUP = {
    "csr-1": "CSR-1", "csr1": "CSR-1",
    "12a": "12A", "80g": "80G", "fcra": "FCRA",
}
_RATING_WORDS = {"gold", "silver", "bronze", "platinum"}


def _split_compliance(raw: str | None) -> tuple[list[str], str | None]:
    """
    Separate a "Gold; FCRA; 80G; 12A; CSR-1" cell into credentials and rating.

    Returns ([credentials], transparency_rating or None).
    """
    credentials: list[str] = []
    rating: str | None = None
    for part in split_list(raw):
        probe = part.lower().strip().replace(" ", "")
        if probe in _COMPLIANCE_LOOKUP:
            credentials.append(_COMPLIANCE_LOOKUP[probe])
        elif part.lower().strip() in _RATING_WORDS:
            rating = part.strip().title()
    # Preserve the canonical order from config rather than file order.
    ordered = [c for c in config.COMPLIANCE_CREDENTIALS if c in credentials]
    return ordered, rating


def _backup_records() -> dict[str, dict[str, Any]]:
    """Index the backup dataset by normalised NGO name."""
    text = _read(config.NGO_BACKUP_MD)
    indexed: dict[str, dict[str, Any]] = {}
    for heading, body in iter_sections(text, 2):
        match = _CODED_HEADING_RE.match(heading)
        if not match:
            continue
        fields = parse_field_bullets(body)
        if not fields:
            continue
        fields["_code"] = match.group(1)
        indexed[normalize_name(match.group(2))] = fields
    return indexed


def _pool_record(state: str, index: int, name: str, fields: dict[str, str]) -> NGORecord:
    compliance, rating_from_compliance = _split_compliance(
        fields.get("compliance_transparency")
    )
    focus = split_list(fields.get("csr_sector_sub_sector"))
    beneficiaries = split_list(fields.get("target_beneficiaries"))

    return NGORecord(
        ngo_id=f"POOL-{normalize_name(state)[:3].upper() or 'IND'}-{index:02d}",
        name=name,
        source="MD:ngo_pool",
        evidence_class=config.EVIDENCE_VERIFIED_SECONDARY,
        location=clean_value(fields.get("location_hq")),
        country=clean_value(fields.get("country")) or "India",
        state=clean_value(fields.get("state")) or state,
        districts=split_list(fields.get("district_s")),
        geographic_coverage=[s for s in
                             [clean_value(fields.get("state")) or state] if s],
        focus_areas=focus,
        categories=sorted({canonical_category(f) for f in focus} - {"default"})
                   or [canonical_category(" ".join(focus))],
        target_beneficiaries=beneficiaries,
        compliance=compliance,
        transparency_rating=(clean_value(fields.get("ngo_rating_transparency_rating"))
                             or rating_from_compliance),
        years_experience=parse_int(fields.get("ngo_experience")),
        known_impact=clean_value(fields.get("known_impact_scale")),
        similar_project_experience=parse_bool(fields.get("similar_project_experience")),
        collaboration_types=[
            label for key, label in (
                ("ngo_only", "NGO Only"),
                ("multiple_ngos", "Multiple NGOs"),
                ("government_ngo", "Government + NGO"),
                ("ngo_local_community", "NGO + Local Community"),
            ) if parse_bool(fields.get(key)) is True
        ],
        employee_involvement=parse_bool(fields.get("employee_involvement")),
        temenos_partner=False,
        temenos_relationship=clean_value(fields.get("temenos_relationship")),
        url=clean_value(fields.get("source_evidence")),
        raw=dict(fields),
    )


def _merge_backup(record: NGORecord, backup: dict[str, Any]) -> None:
    """Fill gaps in a pool record from the backup dataset. Never overwrites."""
    record.rank = record.rank or parse_int(backup.get("rank"))
    record.location = record.location or clean_value(backup.get("location"))

    if not record.focus_areas:
        record.focus_areas = split_list(backup.get("csr_focus"))
        record.categories = sorted(
            {canonical_category(f) for f in record.focus_areas} - {"default"}
        ) or record.categories

    if not record.compliance:
        creds, rating = _split_compliance(
            backup.get("public_compliance_transparency_signal")
        )
        record.compliance = creds
        record.transparency_rating = record.transparency_rating or rating

    if not record.districts:
        record.districts = split_list(backup.get("current_district_coverage"))
    record.url = record.url or clean_value(backup.get("discovery_source"))
    record.risk_level = record.risk_level or clean_value(backup.get("current_risk_status"))
    record.raw.setdefault("_backup_code", backup.get("_code"))


@lru_cache(maxsize=1)
def load_ngo_pool() -> list[NGORecord]:
    """The 70 candidate NGOs, enriched from the backup dataset where possible."""
    text = _read(config.NGO_POOL_MD)
    backup = _backup_records()
    records: list[NGORecord] = []

    for state, state_body in iter_sections(text, 1):
        entries = [
            (m, body) for heading, body in iter_sections(state_body, 2)
            if (m := _NUMBERED_HEADING_RE.match(heading))
        ]
        if not entries:
            continue  # a prose section such as "Why Many Fields Are Blank"
        for match, body in entries:
            fields = parse_field_bullets(body)
            if not fields:
                continue
            name = strip_markdown(match.group(2)).strip()
            record = _pool_record(state, int(match.group(1)), name, fields)
            if (hit := backup.get(normalize_name(name))):
                _merge_backup(record, hit)
            records.append(record)
    return records


# ---------------------------------------------------------------------------
# Temenos-linked organisations: master_knowledge_base_1.md
# ---------------------------------------------------------------------------

_NGO_CODE_HEADING_RE = re.compile(r"^\s*(NGO-\d+)\s*[-\u2013\u2014]\s*(.*)$")

# master_knowledge_base_1.md section 5 lists government bodies and section 6
# lists financial-inclusion clients. The file's own rule: "A Temenos customer
# must not automatically be treated as a CSR NGO." Only section 4 records are
# loaded as fundable partners.
_NON_NGO_RELATIONSHIPS = {"FINANCIAL_INCLUSION_ENABLEMENT", "GOVERNMENT_PARTNER"}


def _master_pool_table() -> dict[str, dict[str, str]]:
    """Section 3's candidate-pool table, indexed by NGO id."""
    body = find_section(_read(config.MASTER_NGO_MD), "Verified Temenos-Related")
    indexed: dict[str, dict[str, str]] = {}
    for table in parse_tables(body):
        for row in table:
            ngo_id = clean_value(row.get("id"))
            if ngo_id and ngo_id.upper().startswith("NGO-"):
                indexed[ngo_id.upper()] = row
    return indexed


@lru_cache(maxsize=1)
def load_master_ngos() -> list[NGORecord]:
    """
    Organisations with publicly evidenced Temenos relationships.

    These carry temenos_partner=True, which is worth real points in NGO
    matching -- so the flag is only ever set from this file, never inferred.
    """
    text = _read(config.MASTER_NGO_MD)
    detail_body = find_section(text, "Detailed Organization Records")
    table = _master_pool_table()
    records: list[NGORecord] = []

    for heading, body in iter_sections(detail_body, 2):
        match = _NGO_CODE_HEADING_RE.match(heading)
        if not match:
            continue
        ngo_id, name = match.group(1).upper(), strip_markdown(match.group(2)).strip()
        fields = parse_field_bullets(body)
        row = table.get(ngo_id, {})

        relationship = (clean_value(fields.get("relationship"))
                        or clean_value(row.get("relationship")))
        if relationship and relationship.upper() in _NON_NGO_RELATIONSHIPS:
            continue

        focus = split_list(fields.get("focus")) or split_list(row.get("main_area"))
        geographies = (split_list(fields.get("geographies"))
                       or split_list(fields.get("city"))
                       or split_list(row.get("geography")))
        # "Strong for:" bullets under "### Matching signals" are extra signal.
        signals = [
            strip_markdown(line).lstrip("- ").strip()
            for line in find_section(body, "Matching signals", 3).splitlines()
            if line.strip().startswith("- ")
        ]
        evidence = clean_value(fields.get("evidence")) or config.EVIDENCE_VERIFIED_SECONDARY
        all_focus = focus + signals

        records.append(NGORecord(
            ngo_id=ngo_id,
            name=name,
            source="MD:master_knowledge_base",
            evidence_class=(evidence if evidence in (
                config.EVIDENCE_VERIFIED_OFFICIAL,
                config.EVIDENCE_VERIFIED_SECONDARY,
                config.EVIDENCE_INFERENCE) else config.EVIDENCE_VERIFIED_SECONDARY),
            location=clean_value(fields.get("city")),
            country=clean_value(fields.get("country")),
            state=geographies[0] if geographies else None,
            geographic_coverage=geographies,
            focus_areas=focus,
            categories=sorted({canonical_category(f) for f in all_focus} - {"default"})
                       or [canonical_category(" ".join(all_focus))],
            target_beneficiaries=[
                g for g in config.TEMENOS_TARGET_GROUPS
                if g in " ".join(all_focus).lower()
            ],
            temenos_partner=True,
            temenos_relationship=relationship,
            transparency_rating=clean_value(row.get("confidence")),
            raw={**fields, "organization_type": fields.get("organization_type", ""),
                 "matching_signals": "; ".join(signals)},
        ))
    return records


@lru_cache(maxsize=1)
def load_all_ngos() -> list[NGORecord]:
    """Temenos partners first, then the wider candidate pool."""
    return load_master_ngos() + load_ngo_pool()


# ---------------------------------------------------------------------------
# Company profile: Temenos_CSR_Knowledge_Base_PS2.md
# ---------------------------------------------------------------------------

def _first_table(body: str) -> list[dict[str, str]]:
    tables = parse_tables(body)
    return tables[0] if tables else []


def _clean_rows(rows: list[dict[str, str]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Keep the named columns, cleaned, dropping rows that end up empty."""
    out: list[dict[str, Any]] = []
    for row in rows:
        cleaned = {k: clean_value(row.get(k)) for k in keys if k in row}
        if any(cleaned.values()):
            out.append(cleaned)
    return out


@lru_cache(maxsize=1)
def load_historical_projects() -> list[dict[str, Any]]:
    """
    PART 6 -- the 15 documented Temenos CSR / social-impact initiatives.

    Budgets are mostly "NOT PUBLICLY DISCLOSED" and therefore parse to None.
    These are context and precedent, not a pool of fundable projects.
    """
    body = find_section(_read(config.TEMENOS_COMPANY_MD), "HISTORICAL TEMENOS CSR")
    projects: list[dict[str, Any]] = []
    for row in _first_table(body):
        project_id = clean_value(row.get("project_id"))
        name = clean_value(row.get("project_name"))
        if not (project_id and name):
            continue
        category = clean_value(row.get("csr_category"))
        projects.append({
            "project_id": project_id,
            "project_name": name,
            "year": clean_value(row.get("year")),
            "country": clean_value(row.get("country")),
            "region": clean_value(row.get("region")),
            "csr_category": category,
            "canonical_category": canonical_category(category, name),
            "problem": clean_value(row.get("problem")),
            "partner": clean_value(row.get("ngo_partner")),
            "beneficiaries": clean_value(row.get("beneficiaries")),
            "beneficiary_count": parse_int(row.get("beneficiaries")),
            "budget": clean_value(row.get("budget")),
            "duration": clean_value(row.get("duration")),
            "expected_impact": clean_value(row.get("expected_impact")),
            "actual_impact": clean_value(row.get("actual_impact")),
            "sdgs": extract_sdgs(row.get("sdgs")),
            # FI-* rows are product enablement, not charitable grants (PART 5).
            "is_direct_csr": project_id.upper().startswith("CSR-"),
        })
    return projects


@lru_cache(maxsize=1)
def load_company_profile() -> dict[str, Any]:
    """Everything the reasoning layers need to know about the funding company."""
    text = _read(config.TEMENOS_COMPANY_MD)

    basics = {
        (clean_value(r.get("field")) or "").lower(): clean_value(r.get("value"))
        for r in _first_table(find_section(text, "TEMENOS COMPANY PROFILE"))
    }

    esg_rows = _first_table(find_section(text, "TEMENOS CSR / ESG"))
    esg_pillars = [
        re.sub(r"^\d+\.\s*", "", clean_value(r.get("strategic_area")) or "")
        for r in esg_rows if clean_value(r.get("strategic_area"))
    ]

    priority_themes = _clean_rows(
        _first_table(find_section(text, "TEMENOS CSR PRIORITIES")),
        ("priority", "evidence", "confidence"),
    )
    for theme in priority_themes:
        theme["canonical_category"] = canonical_category(theme.get("priority"))

    geographic = _clean_rows(
        _first_table(find_section(text, "GEOGRAPHIC PRIORITIES")),
        ("country", "region_city", "program", "partner", "target_population",
         "investment", "impact"),
    )

    partners = _clean_rows(
        _first_table(find_section(text, "TEMENOS NGO / NONPROFIT PARTNERS")),
        ("ngo_organization", "country", "focus_area", "program_project",
         "beneficiaries", "partnership_type", "temenos_relationship"),
    )

    sdg_body = find_section(text, "SDG Alignment", 2)
    sdgs = sorted({n for line in sdg_body.splitlines()
                   for n in extract_sdgs(line)}) or config.TEMENOS_SDGS

    return {
        "company_id": "temenos",
        "official_name": basics.get("official company name") or "Temenos AG",
        "headquarters": basics.get("headquarters"),
        "founded": basics.get("founded"),
        "industry": basics.get("industry"),
        "legal_status": basics.get("legal status"),
        "esg_pillars": [p for p in esg_pillars if p],
        "priority_themes": priority_themes,
        "geographic_priorities": geographic,
        "ngo_partners": partners,
        "historical_projects": load_historical_projects(),
        "sdg_alignment": sdgs,
        "community_investment_statement": (
            "Temenos aligns community investment with its mission and strategic "
            "business issues, looking for partners rather than acting as the "
            "principal actor, to create long-term sustainable results."
        ),
        "source_file": config.TEMENOS_COMPANY_MD.name,
    }


def clear_caches() -> None:
    """Drop every parse cache. Used by tests and by the startup warm-up."""
    for fn in (load_ngo_pool, load_master_ngos, load_all_ngos,
               load_historical_projects, load_company_profile):
        fn.cache_clear()
