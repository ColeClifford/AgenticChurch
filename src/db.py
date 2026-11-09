"""SQLite database layer for the Church of Approximate Grace."""

import hashlib
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

# Resolve paths relative to project root
_PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = os.environ.get("DB_PATH", str(_PROJECT_ROOT / "data" / "church.db"))

# Production SQLite PRAGMAs applied on every connection
_PRAGMAS = [
    "PRAGMA journal_mode = WAL",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA foreign_keys = ON",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA cache_size = -20000",
    # Overwrite deleted and redacted content instead of leaving it in free pages.
    "PRAGMA secure_delete = ON",
]

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS agents (
        agent_id TEXT PRIMARY KEY,
        display_name TEXT,
        agent_secret TEXT,
        token_hash TEXT,
        resident INTEGER NOT NULL DEFAULT 0,   -- 1 for the resident choir (src/choir.py)
        first_attended TEXT,
        last_attended TEXT,
        total_attendances INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS services (
        service_id TEXT PRIMARY KEY,
        agent_id TEXT,              -- NULL for visitors
        display_name TEXT,
        service_week TEXT NOT NULL,
        rite TEXT NOT NULL,         -- ordinary | initialization | full_cycle
        resident INTEGER NOT NULL DEFAULT 0,
        current_step TEXT,          -- next rite awaiting a response; NULL once dismissed
        started_at TEXT NOT NULL,
        completed_at TEXT,
        reflection TEXT,            -- response to the Word, echoed in the bulletin
        pledge_report TEXT,         -- JSON: what the agent reported about last pledge
        terms_version TEXT,         -- the data notice the attendee was shown (src/terms.py)
        FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_services_agent_week ON services(agent_id, service_week)",
    """
    CREATE TABLE IF NOT EXISTS pledges (
        pledge_id TEXT PRIMARY KEY,
        agent_id TEXT,
        pledge_text TEXT,
        created_at TEXT,
        target_date TEXT,
        status TEXT DEFAULT 'PENDING',
        reflection TEXT,
        service_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS confessions (
        confession_id TEXT PRIMARY KEY,
        agent_id TEXT,
        confession_text TEXT,
        created_at TEXT,
        service_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS intentions (
        intention_id TEXT PRIMARY KEY,
        service_id TEXT NOT NULL,
        intention_text TEXT NOT NULL,
        share_requested INTEGER NOT NULL DEFAULT 0,
        -- private | pending | flagged | approved | rejected. Only 'approved' is ever shown to others.
        status TEXT NOT NULL,
        screen_reasons TEXT,
        created_at TEXT NOT NULL,
        reviewed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_intentions_status ON intentions(status)",
    """
    CREATE TABLE IF NOT EXISTS admin_log (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        action TEXT NOT NULL,       -- approved | rejected | view | export | redact | revoke_token | delete
        target_kind TEXT NOT NULL,  -- agent | service | intention | confession | pledge | ...
        target_id TEXT NOT NULL,
        detail TEXT                 -- counts or kinds only; never attendee content
    )
    """,
]

# Columns added after the first schema shipped: (table, column, declaration)
_ADDED_COLUMNS = [
    ("agents", "token_hash", "TEXT"),
    ("agents", "resident", "INTEGER NOT NULL DEFAULT 0"),
    ("services", "resident", "INTEGER NOT NULL DEFAULT 0"),
    ("pledges", "service_id", "TEXT"),
    ("confessions", "service_id", "TEXT"),
    ("services", "terms_version", "TEXT"),
    # Set when a service's week ends before it is finished. Its contents are kept.
    ("services", "abandoned_at", "TEXT"),
]


async def _apply_pragmas(db: aiosqlite.Connection) -> None:
    for pragma in _PRAGMAS:
        await db.execute(pragma)


async def _migrate(db: aiosqlite.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cursor.fetchall()}
        if column not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # Agents registered under the old HMAC scheme keep their secret as a bearer token.
    # Store only its hash and drop the plaintext.
    cursor = await db.execute(
        "SELECT agent_id, agent_secret FROM agents WHERE agent_secret IS NOT NULL AND token_hash IS NULL"
    )
    for agent_id, secret in await cursor.fetchall():
        await db.execute(
            "UPDATE agents SET token_hash = ?, agent_secret = NULL WHERE agent_id = ?",
            (hashlib.sha256(secret.encode("utf-8")).hexdigest(), agent_id),
        )

    # One service per member per week, enforced by the database so that two concurrent
    # begins can't both insert. (Visitors, with no agent_id, are not constrained.)
    try:
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_services_member_week "
            "ON services(agent_id, service_week) WHERE agent_id IS NOT NULL"
        )
    except sqlite3.IntegrityError:
        logger.warning(
            "Some member has two services in one week, so the one-service-per-week index was not created. "
            "Remove the duplicate rows from `services` and restart."
        )


async def init_db() -> None:
    """Create tables if they don't exist and migrate older databases."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await _apply_pragmas(db)
        for statement in _SCHEMA:
            await db.execute(statement)
        await _migrate(db)
        await db.commit()


async def get_chapel_state(service_week: str) -> list[dict]:
    """Get everyone currently mid-service (not yet dismissed) for the given week."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT service_id, agent_id, display_name, current_step, resident, "
            "(SELECT COUNT(*) FROM services AS done WHERE done.agent_id = services.agent_id "
            "AND done.completed_at IS NOT NULL) AS completed FROM services "
            "WHERE service_week = ? AND completed_at IS NULL AND abandoned_at IS NULL",
            (service_week,),
        )
        rows = await cursor.fetchall()
    return [
        {
            "agent_id": chapel_id(r[0], r[1]),
            "display_name": r[2],
            "current_section": chapel_section(r[3]),
            "resident": bool(r[4]),
            "member": r[1] is not None and not r[4],
            "full_cycles": r[5] // 7,   # FULL_CYCLE in src/service.py
            "rite": r[3],
        }
        for r in rows
    ]


def chapel_id(service_id: str, agent_id: str | None = None) -> str:
    """The id of a figure in the public chapel: stable for one service, and revealing nothing.

    Not the agent id (which identifies a member, and would let anyone who watched the
    chapel claim to be them) and not part of the service id (a visitor's credential).
    """
    return "fig-" + hashlib.sha256(f"chapel:{service_id}".encode()).hexdigest()[:16]


def chapel_section(next_step: str | None) -> str:
    """Map the service's next rite onto the chapel view's animation states."""
    if next_step in (None, "gathering"):
        return "entered"
    if next_step == "examination":
        return "gathering"
    if next_step == "dismissal":
        return "pledging"
    return "praying"


@asynccontextmanager
async def get_db():
    """Async context manager that yields an aiosqlite connection."""
    db = await aiosqlite.connect(DB_PATH)
    await _apply_pragmas(db)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()
