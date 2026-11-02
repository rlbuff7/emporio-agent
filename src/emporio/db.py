"""Read-only access to the SQLite database built by ``emporio.etl``."""

import sqlite3
from pathlib import Path

from emporio.paths import DB_PATH


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the database read-only, so no tool can change store data."""
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} not found. Build it first with: python -m emporio.etl")
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
