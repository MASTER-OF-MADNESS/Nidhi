"""
SQLite setup for the NIDHI audit trail.

Every /generate run is written here with its full input parameters and output
summary, so any recommendation can be reconstructed and defended later.

Connections are opened per operation rather than shared. SQLite connections are
not safe to move between threads, and the SSE endpoint hands work to a thread
pool -- a module-level connection would be a latent crash.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS run_logs (
    run_id                TEXT PRIMARY KEY,
    timestamp             TEXT NOT NULL,
    company_id            TEXT NOT NULL,
    input_parameters      TEXT NOT NULL,
    data_source           TEXT NOT NULL,
    projects_evaluated    INTEGER NOT NULL DEFAULT 0,
    projects_funded       INTEGER NOT NULL DEFAULT 0,
    total_allocated       REAL    NOT NULL DEFAULT 0,
    constraints_satisfied INTEGER NOT NULL DEFAULT 1,
    output_summary        TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_run_logs_company
    ON run_logs (company_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_run_logs_timestamp
    ON run_logs (timestamp DESC);
"""


def _db_path() -> Path:
    return Path(config.SQLITE_DB_PATH)


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Yield a connection with row access by name, committing on clean exit."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the schema if it does not exist. Safe to call repeatedly."""
    with get_connection() as conn:
        conn.executescript(SCHEMA)


def reset_db() -> None:
    """Drop and recreate. Test-support only; never called by the app."""
    with get_connection() as conn:
        conn.executescript("DROP TABLE IF EXISTS run_logs;")
        conn.executescript(SCHEMA)
