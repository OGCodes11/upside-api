"""SQLite connection helpers. Queries are plain SQL; no ORM yet."""

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


# The only tables a public snapshot holds (scripts/build_public_db.py). A public server refuses any other.
SNAPSHOT_TABLES = frozenset({"players", "prospects", "prospect_scores", "player_progression"})


def db_path() -> Path:
    return Path(os.environ.get("UPSIDE_DB_PATH", "data/upside.db"))


def public_mode() -> bool:
    return os.environ.get("PUBLIC_MODE") == "1"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    # check_same_thread=False: FastAPI may open the connection and use it on different threads.
    # Each request gets its own connection, so this is safe.
    conn = sqlite3.connect(path or db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["name"]
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect_readonly(path: Path | str | None = None) -> sqlite3.Connection:
    """Open an existing database for reading only. A missing file is an error, not a new empty database."""
    path = Path(path or db_path())
    if not path.is_file():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def check_public_snapshot(conn: sqlite3.Connection) -> None:
    """Raise unless the database holds exactly the snapshot tables, so a full database is never served."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if tables != SNAPSHOT_TABLES:
        extra, missing = sorted(tables - SNAPSHOT_TABLES), sorted(SNAPSHOT_TABLES - tables)
        raise RuntimeError(f"Not a public snapshot: unexpected tables {extra}, missing tables {missing}")


# Columns added after a table was first created. CREATE TABLE IF NOT EXISTS skips tables
# that already exist, so older databases get these with ALTER TABLE instead.
ADDED_COLUMNS = [
    ("prospects", "cbbd_athlete_id", "INTEGER"),
    ("prospects", "college_link", "TEXT"),
    ("games", "has_box_score", "INTEGER NOT NULL DEFAULT 1"),
    ("prospects", "missing_box_share", "REAL"),
    ("prospects", "missing_box_flag", "INTEGER"),
    ("player_game_stats", "source", "TEXT"),
]


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    for table, column, sql_type in ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request, closed afterwards. Read-only in public mode."""
    conn = connect_readonly() if public_mode() else connect()
    try:
        yield conn
    finally:
        conn.close()
