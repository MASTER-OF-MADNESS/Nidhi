"""
Company profile endpoint.

Resolves through config.COMPANY_REGISTRY, so an unregistered id returns 404
rather than a Temenos profile under someone else's name.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

import config
from models.schemas import CompanyProfile
from retrieval import md_parser
from retrieval.text_utils import parse_int

router = APIRouter(tags=["company"])


@router.get("/company/{company_id}", response_model=CompanyProfile)
async def get_company(company_id: str) -> CompanyProfile:
    registered = config.get_company(company_id)
    if registered is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(f"No company registered with id '{company_id}'. "
                    f"Registered: {', '.join(config.COMPANY_REGISTRY)}."),
        )

    profile = md_parser.load_company_profile()
    return CompanyProfile(
        company_id=registered["company_id"],
        display_name=registered["display_name"],
        legal_name=profile.get("official_name") or registered["legal_name"],
        headquarters=profile.get("headquarters") or registered.get("headquarters"),
        # The source states "November 1993 (September 22, 1993 per some
        # sources)"; the year is the part worth exposing as a number.
        founded=parse_int(profile.get("founded")) or registered.get("founded"),
        country=registered.get("country"),
        default_currency=registered.get("default_currency", "INR"),
        esg_pillars=profile.get("esg_pillars", []),
        priority_themes=profile.get("priority_themes", []),
        geographic_priorities=profile.get("geographic_priorities", []),
        historical_projects=profile.get("historical_projects", []),
        ngo_partners=profile.get("ngo_partners", []),
        sdg_alignment=profile.get("sdg_alignment", []),
    )


@router.get("/companies")
async def list_companies() -> dict:
    """Every registered company. One today; the shape supports more."""
    return {
        "companies": [
            {
                "company_id": entry["company_id"],
                "display_name": entry["display_name"],
                "legal_name": entry["legal_name"],
                "country": entry.get("country"),
                "default_currency": entry.get("default_currency", "INR"),
            }
            for entry in config.COMPANY_REGISTRY.values()
        ],
        "registered_count": len(config.COMPANY_REGISTRY),
    }
