"""
Step 4 checks: the knowledge base parses into the exact structures the rest of
the system assumes, and blanks stay blank.
"""

from retrieval import md_parser as mp
from retrieval.text_utils import canonical_category, clean_value, normalize_name


# --- counts ----------------------------------------------------------------

def test_pool_has_exactly_seventy_ngos():
    pool = mp.load_ngo_pool()
    assert len(pool) == 70
    assert len({n.ngo_id for n in pool}) == 70


def test_pool_covers_all_seven_regions_evenly():
    from collections import Counter
    counts = Counter(n.state for n in mp.load_ngo_pool())
    assert set(counts) == {
        "Tamil Nadu", "Kerala", "Karnataka", "Andhra Pradesh",
        "Telangana", "Odisha", "All India",
    }
    assert set(counts.values()) == {10}


def test_master_has_twelve_temenos_partners():
    master = mp.load_master_ngos()
    assert len(master) == 12
    assert all(n.temenos_partner for n in master)
    # Financial-inclusion clients must never be loaded as fundable NGOs.
    names = {n.name for n in master}
    assert "CreditAccess Grameen" not in names
    assert "LOLC Cambodia" not in names


def test_historical_projects_parse():
    projects = mp.load_historical_projects()
    assert len(projects) == 15
    # FI-* rows are product enablement, not charitable grants.
    assert sum(1 for p in projects if p["is_direct_csr"]) == 7


# --- the UNKNOWN-is-not-zero contract --------------------------------------

def test_blank_fields_stay_none_never_zero():
    bhumi = next(n for n in mp.load_ngo_pool() if n.name == "Bhumi")
    assert bhumi.raw["total_csr_budget"].strip() == ""
    assert bhumi.years_experience is None
    assert bhumi.years_experience != 0
    assert bhumi.staff_capacity is None
    assert bhumi.utilization_rate is None
    assert bhumi.similar_project_experience is None
    assert bhumi.similar_project_experience is not False


def test_sentinel_strings_are_treated_as_unknown():
    for sentinel in (
        "NOT PUBLICLY DISCLOSED",
        "NOT PUBLICLY DISCLOSED IN THIS BACKUP DATASET",
        "Requires live verification",
        "NOT VERIFIED / DO NOT ASSUME",
        "NOT VERIFIED IN THIS DATASET",
        "   ",
    ):
        assert clean_value(sentinel) is None, sentinel


def test_undisclosed_budget_is_none_not_zero():
    smartbin = next(p for p in mp.load_historical_projects()
                    if p["project_id"] == "CSR-003")
    assert smartbin["budget"] is None
    assert smartbin["budget"] != 0


# --- field extraction ------------------------------------------------------

def test_compliance_and_rating_split_correctly():
    bhumi = next(n for n in mp.load_ngo_pool() if n.name == "Bhumi")
    assert bhumi.compliance == ["CSR-1", "12A", "80G", "FCRA"]
    assert bhumi.transparency_rating == "Gold"
    assert "Gold" not in bhumi.compliance


def test_backup_dataset_merges_in_by_name():
    bhumi = next(n for n in mp.load_ngo_pool() if n.name == "Bhumi")
    assert bhumi.rank == 1                      # only present in the backup file
    assert bhumi.url and "give.do" in bhumi.url


def test_name_normalisation_matches_across_files():
    assert (normalize_name("Society for Poor People Development (SPPD)")
            == normalize_name("Society for Poor People Development"))


def test_temenos_partner_flag_only_from_master_file():
    assert all(not n.temenos_partner for n in mp.load_ngo_pool())
    grow = next(n for n in mp.load_master_ngos() if n.ngo_id == "NGO-001")
    assert grow.temenos_partner is True
    assert grow.temenos_relationship == "DIRECT_CSR_PARTNER"
    assert grow.evidence_class == "VERIFIED_OFFICIAL"
    assert grow.geographic_coverage == ["Tamil Nadu", "Telangana"]


def test_primary_category_wins_over_later_mentions():
    """The knowledge base lists a project's primary category first."""
    assert canonical_category("Women's Livelihood, Environmental Sustainability") \
        == "women_livelihood"
    assert canonical_category("Environmental Sustainability") == "environment"
    assert canonical_category("Financial Inclusion") == "financial_inclusion"


# --- company profile -------------------------------------------------------

def test_company_profile_has_the_pieces_scoring_needs():
    profile = mp.load_company_profile()
    assert profile["official_name"].startswith("Temenos AG")
    assert len(profile["esg_pillars"]) == 6
    assert "Investing in Our Communities" in profile["esg_pillars"]
    assert len(profile["priority_themes"]) == 14
    assert len(profile["geographic_priorities"]) == 10
    assert len(profile["ngo_partners"]) == 13
    assert 5 in profile["sdg_alignment"]      # Gender Equality
    assert len(profile["historical_projects"]) == 15


def test_parsers_are_cached():
    assert mp.load_ngo_pool() is mp.load_ngo_pool()
    assert mp.load_company_profile() is mp.load_company_profile()
