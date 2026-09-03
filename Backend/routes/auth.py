"""
Login.

Deliberately simple: one hardcoded credential read from .env, validated here,
returning the company identity the rest of the app is scoped to. No token is
issued and the other endpoints are not gated -- this identifies the operator
for the UI, it is not an access-control boundary.

If this ever needs to become real auth, this is the file to change: the company
lookup already flows through config.COMPANY_REGISTRY rather than a constant.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, status

import config
from models.schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def verify_credentials(username: str, password: str) -> bool:
    """Constant-time comparison, so response timing reveals nothing."""
    return (
        secrets.compare_digest((username or "").strip(), config.NIDHI_USERNAME)
        and secrets.compare_digest(password or "", config.NIDHI_PASSWORD)
    )


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest) -> LoginResponse:
    if not verify_credentials(payload.username, payload.password):
        # One message for both cases: never reveal which half was wrong.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    company = config.get_company(config.COMPANY_ID)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Configured COMPANY_ID '{config.COMPANY_ID}' is not registered.",
        )

    return LoginResponse(
        success=True,
        company_id=company["company_id"],
        company_name=company["legal_name"],
        message=f"Signed in to {company['display_name']}.",
    )


@router.get("/session", response_model=LoginResponse)
async def session() -> LoginResponse:
    """
    Which company this deployment is configured for.

    Lets the frontend show the company on the login screen before sign-in.
    """
    company = config.get_company(config.COMPANY_ID)
    if company is None:
        return LoginResponse(success=False,
                             message="No company registered for this deployment.")
    return LoginResponse(
        success=True,
        company_id=company["company_id"],
        company_name=company["legal_name"],
        message="Registered company for this deployment.",
    )
