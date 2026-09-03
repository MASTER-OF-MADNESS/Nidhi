"""Shared pytest fixtures. Adds the Backend root to sys.path."""

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from models.schemas import GenerateRequest  # noqa: E402


@pytest.fixture
def sample_request() -> GenerateRequest:
    """A realistic Temenos CSR request: education across TN + Karnataka."""
    return GenerateRequest(
        total_csr_budget=20_000_000,
        funding_type="Grant",
        project_duration=18,
        number_of_projects=5,
        min_project_funding=1_000_000,
        max_project_funding=6_000_000,
        country="India",
        states=["Tamil Nadu", "Karnataka"],
        districts=["Chennai", "Bengaluru Urban"],
        csr_sector="Education",
        csr_sub_sector="Digital literacy",
        beneficiary_categories=["Children", "Youth", "Women"],
        ngo_experience=5,
        similar_project_experience=True,
        geographic_capability="Multi-state",
        ngo_rating="Gold",
        compliance_requirements=["CSR-1", "12A", "80G"],
        collaboration_type="NGO + Local Community",
        employee_involvement=True,
        additional_comments="Prefer measurable learning outcomes.",
        currency="INR",
    )
