"""SQLite schema, connection, and CRUD operations."""

import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

CREATE TABLE IF NOT EXISTS embeddings (
    message_id TEXT PRIMARY KEY REFERENCES messages(id),
    vector     BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id             TEXT PRIMARY KEY,
    entity         TEXT NOT NULL,
    category       TEXT NOT NULL,
    content        TEXT NOT NULL,
    source_msg_id  TEXT REFERENCES messages(id),
    created_at     TEXT NOT NULL,
    superseded_by  TEXT REFERENCES facts(id)
);
CREATE INDEX IF NOT EXISTS idx_facts_entity ON facts(entity);

CREATE TABLE IF NOT EXISTS people (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    relationship TEXT,
    notes        TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_usage (
    id           TEXT PRIMARY KEY,
    query        TEXT NOT NULL,
    result_count INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id         TEXT PRIMARY KEY,
    due_at     TEXT NOT NULL,
    message    TEXT NOT NULL,
    fired      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at, fired);

CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    start_at   TEXT NOT NULL,
    end_at     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_at);

CREATE TABLE IF NOT EXISTS todos (
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    priority   TEXT NOT NULL DEFAULT 'medium',
    done       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    done_at    TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id                TEXT PRIMARY KEY,
    endpoint          TEXT NOT NULL UNIQUE,
    subscription_json TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
"""

_CURRENT_VERSION = 5


def _connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> sqlite3.Connection:
    """Create tables if needed and return a connection."""
    conn = _connect()
    conn.executescript(_SCHEMA)
    # Set or update version
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    if row[0] is None:
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (_CURRENT_VERSION,)
        )
    elif row[0] < _CURRENT_VERSION:
        conn.execute(
            "UPDATE schema_version SET version = ? WHERE version = ?",
            (_CURRENT_VERSION, row[0]),
        )
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def estimate_tokens(text: str) -> int:
    """Rough char/3 heuristic."""
    return max(1, len(text) // 3)


# ── Messages ──────────────────────────────────────────────────

def save_message(
    conn: sqlite3.Connection,
    session_id: str,
    role: str,
    content: str,
) -> str:
    """Insert a message and return its id."""
    mid = _uid()
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at, token_count) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mid, session_id, role, content, _now(), estimate_tokens(content)),
    )
    conn.commit()
    return mid


def get_session_messages(
    conn: sqlite3.Connection,
    session_id: str,
    limit: int = 50,
) -> list[dict]:
    """Return recent messages for a session, oldest first."""
    rows = conn.execute(
        "SELECT id, role, content, token_count FROM messages "
        "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def count_messages(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
    return row[0]


def count_sessions(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(DISTINCT session_id) FROM messages").fetchone()
    return row[0]


# ── Embeddings ────────────────────────────────────────────────

def save_embedding(conn: sqlite3.Connection, message_id: str, vector: bytes):
    conn.execute(
        "INSERT OR REPLACE INTO embeddings (message_id, vector) VALUES (?, ?)",
        (message_id, vector),
    )
    conn.commit()


def load_all_embeddings(conn: sqlite3.Connection) -> list[tuple[str, bytes]]:
    """Return list of (message_id, vector_blob)."""
    rows = conn.execute("SELECT message_id, vector FROM embeddings").fetchall()
    return [(r["message_id"], r["vector"]) for r in rows]


def get_messages_by_ids(
    conn: sqlite3.Connection, ids: list[str]
) -> list[dict]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, role, content, created_at FROM messages "
        f"WHERE id IN ({placeholders}) ORDER BY created_at",
        ids,
    ).fetchall()
    return [dict(r) for r in rows]


# ── People ────────────────────────────────────────────────────

def upsert_person(
    conn: sqlite3.Connection,
    name: str,
    relationship: str | None = None,
) -> str:
    row = conn.execute(
        "SELECT id FROM people WHERE name = ?", (name,)
    ).fetchone()
    if row:
        if relationship:
            conn.execute(
                "UPDATE people SET relationship = ? WHERE id = ?",
                (relationship, row["id"]),
            )
            conn.commit()
        return row["id"]
    pid = _uid()
    conn.execute(
        "INSERT INTO people (id, name, relationship, notes, created_at) "
        "VALUES (?, ?, ?, '', ?)",
        (pid, name, relationship, _now()),
    )
    conn.commit()
    return pid


def get_all_people(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT name, relationship, notes FROM people ORDER BY name"
    ).fetchall()
    return [dict(r) for r in rows]


# ── Facts ─────────────────────────────────────────────────────

def save_fact(
    conn: sqlite3.Connection,
    entity: str,
    category: str,
    content: str,
    source_msg_id: str | None = None,
) -> str:
    fid = _uid()
    conn.execute(
        "INSERT INTO facts (id, entity, category, content, source_msg_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (fid, entity, category, content, source_msg_id, _now()),
    )
    conn.commit()
    return fid


def get_active_facts(
    conn: sqlite3.Connection, entity: str | None = None
) -> list[dict]:
    """Return facts not superseded. Optionally filter by entity."""
    if entity:
        rows = conn.execute(
            "SELECT entity, category, content FROM facts "
            "WHERE superseded_by IS NULL AND entity = ? ORDER BY created_at",
            (entity,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT entity, category, content FROM facts "
            "WHERE superseded_by IS NULL ORDER BY entity, created_at"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Search Usage ─────────────────────────────────────────────

def save_search(conn: sqlite3.Connection, query: str, result_count: int) -> str:
    """Record a search query and return its id."""
    sid = _uid()
    conn.execute(
        "INSERT INTO search_usage (id, query, result_count, created_at) "
        "VALUES (?, ?, ?, ?)",
        (sid, query, result_count, _now()),
    )
    conn.commit()
    return sid


def count_monthly_searches(conn: sqlite3.Connection) -> int:
    """Count searches performed in the current calendar month."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    row = conn.execute(
        "SELECT COUNT(*) FROM search_usage WHERE created_at >= ?",
        (month_start.isoformat(),),
    ).fetchone()
    return row[0]


# ── Reminders ────────────────────────────────────────────────

def save_reminder(conn: sqlite3.Connection, due_at: str, message: str) -> str:
    """Insert a reminder and return its id. due_at is a UTC ISO8601 string."""
    rid = _uid()
    conn.execute(
        "INSERT INTO reminders (id, due_at, message, fired, created_at) "
        "VALUES (?, ?, ?, 0, ?)",
        (rid, due_at, message, _now()),
    )
    conn.commit()
    return rid


def get_due_reminders(conn: sqlite3.Connection) -> list[dict]:
    """Return all unfired reminders whose due_at is in the past."""
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT id, due_at, message FROM reminders "
        "WHERE fired = 0 AND due_at <= ? ORDER BY due_at",
        (now,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_reminder_fired(conn: sqlite3.Connection, reminder_id: str) -> None:
    conn.execute(
        "UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,)
    )
    conn.commit()


def get_reminder(conn: sqlite3.Connection, reminder_id: str) -> dict | None:
    """Return a single reminder by id, or None if not found."""
    row = conn.execute(
        "SELECT id, due_at, message, fired FROM reminders WHERE id = ?",
        (reminder_id,),
    ).fetchone()
    return dict(row) if row else None


def get_pending_reminders(conn: sqlite3.Connection) -> list[dict]:
    """Return all unfired reminders, ordered by due time."""
    rows = conn.execute(
        "SELECT id, due_at, message FROM reminders "
        "WHERE fired = 0 ORDER BY due_at"
    ).fetchall()
    return [dict(r) for r in rows]


# ── Events ────────────────────────────────────────────────────

def save_event(
    conn: sqlite3.Connection,
    title: str,
    start_at: str,
    end_at: str | None = None,
) -> str:
    """Insert an event and return its id. Times are UTC ISO8601."""
    eid = _uid()
    conn.execute(
        "INSERT INTO events (id, title, start_at, end_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (eid, title, start_at, end_at, _now()),
    )
    conn.commit()
    return eid


def get_todays_events(conn: sqlite3.Connection) -> list[dict]:
    """Return events starting today (local time), ordered by start_at."""
    local_tz = datetime.now().astimezone().tzinfo
    today = date.today()
    tomorrow = today + timedelta(days=1)
    start_utc = datetime(
        today.year, today.month, today.day, tzinfo=local_tz
    ).astimezone(timezone.utc).isoformat()
    end_utc = datetime(
        tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=local_tz
    ).astimezone(timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT id, title, start_at, end_at FROM events "
        "WHERE start_at >= ? AND start_at < ? ORDER BY start_at",
        (start_utc, end_utc),
    ).fetchall()
    return [dict(r) for r in rows]


def get_upcoming_events(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    """Return events starting within the next N days from now."""
    now_utc = datetime.now(timezone.utc)
    end_utc = (now_utc + timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT id, title, start_at, end_at FROM events "
        "WHERE start_at >= ? AND start_at <= ? ORDER BY start_at",
        (now_utc.isoformat(), end_utc),
    ).fetchall()
    return [dict(r) for r in rows]


def get_event(conn: sqlite3.Connection, event_id: str) -> dict | None:
    """Return a single event by id, or None if not found."""
    row = conn.execute(
        "SELECT id, title, start_at, end_at FROM events WHERE id = ?",
        (event_id,),
    ).fetchone()
    return dict(row) if row else None


# ── Todos ─────────────────────────────────────────────────────

def save_todo(
    conn: sqlite3.Connection,
    content: str,
    priority: str = "medium",
) -> str:
    """Insert a todo and return its id."""
    tid = _uid()
    conn.execute(
        "INSERT INTO todos (id, content, priority, done, created_at) "
        "VALUES (?, ?, ?, 0, ?)",
        (tid, content, priority, _now()),
    )
    conn.commit()
    return tid


def get_pending_todos(conn: sqlite3.Connection) -> list[dict]:
    """Return undone todos ordered by priority (high→medium→low), then created_at."""
    rows = conn.execute(
        "SELECT id, content, priority, created_at FROM todos "
        "WHERE done = 0 "
        "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
        "created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def complete_todo(conn: sqlite3.Connection, todo_id: str) -> None:
    """Mark a todo as done."""
    conn.execute(
        "UPDATE todos SET done = 1, done_at = ? WHERE id = ?",
        (_now(), todo_id),
    )
    conn.commit()


def find_todo(conn: sqlite3.Connection, partial: str) -> dict | None:
    """Fuzzy match on content for TODO_DONE. Returns first undone match."""
    row = conn.execute(
        "SELECT id, content, priority FROM todos "
        "WHERE done = 0 AND lower(content) LIKE lower(?) LIMIT 1",
        (f"%{partial}%",),
    ).fetchone()
    return dict(row) if row else None


# ── Notes ─────────────────────────────────────────────────────

def save_note(conn: sqlite3.Connection, content: str) -> str:
    """Insert a note and return its id."""
    nid = _uid()
    conn.execute(
        "INSERT INTO notes (id, content, archived, created_at) VALUES (?, ?, 0, ?)",
        (nid, content, _now()),
    )
    conn.commit()
    return nid


def get_recent_notes(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    """Return the most recent non-archived notes."""
    rows = conn.execute(
        "SELECT id, content, created_at FROM notes "
        "WHERE archived = 0 ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def archive_note(conn: sqlite3.Connection, note_id: str) -> None:
    """Mark a note as archived."""
    conn.execute("UPDATE notes SET archived = 1 WHERE id = ?", (note_id,))
    conn.commit()


# ── Push Subscriptions ────────────────────────────────────────

def save_push_subscription(
    conn: sqlite3.Connection, endpoint: str, subscription_json: str
) -> str:
    """Upsert a Web Push subscription by endpoint. Returns its id."""
    row = conn.execute(
        "SELECT id FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE push_subscriptions SET subscription_json = ? WHERE id = ?",
            (subscription_json, row["id"]),
        )
        conn.commit()
        return row["id"]
    sid = _uid()
    conn.execute(
        "INSERT INTO push_subscriptions (id, endpoint, subscription_json, created_at) "
        "VALUES (?, ?, ?, ?)",
        (sid, endpoint, subscription_json, _now()),
    )
    conn.commit()
    return sid


def get_push_subscriptions(conn: sqlite3.Connection) -> list[dict]:
    """Return all stored push subscriptions."""
    rows = conn.execute(
        "SELECT id, endpoint, subscription_json FROM push_subscriptions"
    ).fetchall()
    return [dict(r) for r in rows]


def delete_push_subscription(conn: sqlite3.Connection, endpoint: str) -> None:
    """Remove a push subscription (called on 410 Gone response)."""
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    conn.commit()
