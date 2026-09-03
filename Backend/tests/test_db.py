"""Step 3 checks: the audit trail persists and reads back faithfully."""

import config
import pytest

from db import crud
from db.database import init_db, reset_db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Point every test at a throwaway database file."""
    monkeypatch.setattr(config, "SQLITE_DB_PATH", tmp_path / "test_nidhi.db")
    init_db()
    reset_db()
    yield


def _save(run_id: str, ts: str, funded: int = 3, allocated: float = 1000.0):
    return crud.save_run_log_sync(
        run_id=run_id,
        company_id="temenos",
        input_parameters={"total_csr_budget": 20_000_000, "states": ["Tamil Nadu"]},
        data_source="MD_FALLBACK",
        projects_evaluated=8,
        projects_funded=funded,
        total_allocated=allocated,
        constraints_satisfied=True,
        output_summary={"top_project": "Digital literacy, Chennai"},
        timestamp=ts,
    )


def test_save_then_fetch_round_trip():
    _save("run-1", "2026-09-04T10:00:00+00:00")
    detail = crud.get_run_log_sync("run-1")

    assert detail is not None
    assert detail.company_id == "temenos"
    assert detail.projects_funded == 3
    assert detail.constraints_satisfied is True
    # JSON columns must survive the round trip as real structures.
    assert detail.input_parameters["states"] == ["Tamil Nadu"]
    assert detail.output_summary["top_project"] == "Digital literacy, Chennai"


def test_missing_run_returns_none():
    assert crud.get_run_log_sync("does-not-exist") is None


def test_listing_is_newest_first():
    _save("old", "2026-09-01T10:00:00+00:00")
    _save("newest", "2026-09-03T10:00:00+00:00")
    _save("middle", "2026-09-02T10:00:00+00:00")

    ids = [r.run_id for r in crud.list_run_logs_sync()]
    assert ids == ["newest", "middle", "old"]


def test_company_filter_and_count():
    _save("a", "2026-09-01T10:00:00+00:00")
    crud.save_run_log_sync(
        run_id="b", company_id="other", input_parameters={},
        data_source="TAVILY_PRIMARY", projects_evaluated=1, projects_funded=1,
        total_allocated=5.0, constraints_satisfied=False,
        timestamp="2026-09-02T10:00:00+00:00",
    )
    assert crud.count_run_logs_sync() == 2
    assert crud.count_run_logs_sync("temenos") == 1
    assert [r.run_id for r in crud.list_run_logs_sync("temenos")] == ["a"]


def test_constraints_satisfied_false_survives_as_bool():
    crud.save_run_log_sync(
        run_id="c", company_id="temenos", input_parameters={},
        data_source="MD_FALLBACK", projects_evaluated=4, projects_funded=0,
        total_allocated=0.0, constraints_satisfied=False,
    )
    assert crud.get_run_log_sync("c").constraints_satisfied is False


@pytest.mark.asyncio
async def test_async_wrappers_work():
    await crud.save_run_log(
        run_id="async-1", company_id="temenos", input_parameters={"x": 1},
        data_source="MD_FALLBACK", projects_evaluated=2, projects_funded=1,
        total_allocated=99.5, constraints_satisfied=True,
    )
    detail = await crud.get_run_log("async-1")
    assert detail.total_allocated == 99.5
    assert len(await crud.list_run_logs()) == 1
