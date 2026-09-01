"""SQLite persistence for chats, jobs, and events.

The queue lives in this database too: a job row with status 'queued' *is* the
queue entry. That is deliberate -- if the gateway process dies mid-job, the
queue survives a restart and nothing is lost to an in-memory structure.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS chats (
    chat_id          TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    codex_thread_id  TEXT,
    worktree_path    TEXT,
    git_branch       TEXT,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id                     TEXT PRIMARY KEY,
    chat_id                    TEXT NOT NULL,
    user_id                    TEXT NOT NULL,
    codex_thread_id            TEXT,
    request                    TEXT NOT NULL,
    started_at                 REAL,
    ended_at                   REAL,
    worktree                   TEXT,
    branch                     TEXT,
    commit_before              TEXT,
    commit_after               TEXT,
    files_changed              TEXT,
    commands_run               TEXT,
    result                     TEXT,
    status                     TEXT NOT NULL,
    error                      TEXT,
    maintainer_action_required INTEGER NOT NULL DEFAULT 0,
    preview_requested        INTEGER NOT NULL DEFAULT 0,
    preview_url              TEXT,
    preview_error            TEXT,
    handoff_path             TEXT,
    created_at                 REAL NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  TEXT NOT NULL,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    payload TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);

CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_chat ON jobs(chat_id, created_at);
"""

# Job status values.
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
NEEDS_ATTENTION = "needs_attention"

# Chat status values.
CHAT_ACTIVE = "active"
CHAT_NEEDS_ATTENTION = "needs_attention"


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Additive migrations for databases created before preview deployment and
    # handoff artifacts existed. No data is rewritten or deleted.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for name, definition in (
        ("preview_requested", "INTEGER NOT NULL DEFAULT 0"),
        ("preview_url", "TEXT"),
        ("preview_error", "TEXT"),
        ("handoff_path", "TEXT"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
    return conn


def now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def short_chat_id(chat_id: str) -> str:
    """Filesystem/branch-safe short form of an arbitrary client chat id.

    Client chat ids come from the chat UI and are untrusted, so this never
    passes the raw value through to a path or a ref name.
    """
    safe = "".join(ch for ch in chat_id if ch.isalnum() or ch in "-_")[:12]
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"gambling-chat:{chat_id}").hex[:8]
    return f"{safe}-{digest}" if safe else digest


# --------------------------------------------------------------------------
# chats
# --------------------------------------------------------------------------

def get_chat(conn: sqlite3.Connection, chat_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM chats WHERE chat_id = ?", (chat_id,)
    ).fetchone()


def create_chat(conn: sqlite3.Connection, chat_id: str, user_id: str) -> sqlite3.Row:
    ts = now()
    conn.execute(
        "INSERT INTO chats (chat_id, user_id, created_at, updated_at, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (chat_id, user_id, ts, ts, CHAT_ACTIVE),
    )
    row = get_chat(conn, chat_id)
    assert row is not None
    return row


def update_chat(conn: sqlite3.Connection, chat_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE chats SET {sets} WHERE chat_id = ?",
        (*fields.values(), chat_id),
    )


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------

def create_job(
    conn: sqlite3.Connection,
    *,
    chat_id: str,
    user_id: str,
    request: str,
    preview_requested: bool = False,
) -> str:
    job_id = new_id("job")
    conn.execute(
        "INSERT INTO jobs (job_id, chat_id, user_id, request, status, "
        "preview_requested, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, chat_id, user_id, request, QUEUED, int(preview_requested), now()),
    )
    return job_id


def update_job(conn: sqlite3.Connection, job_id: str, **fields: Any) -> None:
    if not fields:
        return
    for key in ("files_changed", "commands_run"):
        if key in fields and not isinstance(fields[key], (str, type(None))):
            fields[key] = json.dumps(fields[key])
    if "maintainer_action_required" in fields:
        fields["maintainer_action_required"] = int(bool(fields["maintainer_action_required"]))
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE jobs SET {sets} WHERE job_id = ?",
        (*fields.values(), job_id),
    )


def get_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()


def next_queued_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM jobs WHERE status = ? ORDER BY created_at, rowid LIMIT 1",
        (QUEUED,),
    ).fetchone()


def queue_position(conn: sqlite3.Connection, job_id: str) -> int:
    """1-based position in the queue; 0 if the job is not queued."""
    rows = conn.execute(
        "SELECT job_id FROM jobs WHERE status = ? ORDER BY created_at, rowid",
        (QUEUED,),
    ).fetchall()
    for i, row in enumerate(rows, start=1):
        if row["job_id"] == job_id:
            return i
    return 0


def running_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM jobs WHERE status = ? ORDER BY started_at", (RUNNING,)
    ).fetchall()


def job_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key in ("files_changed", "commands_run"):
        raw = out.get(key)
        out[key] = json.loads(raw) if raw else []
    out["maintainer_action_required"] = bool(out.get("maintainer_action_required"))
    out["preview_requested"] = bool(out.get("preview_requested"))
    return out


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------

def add_event(
    conn: sqlite3.Connection, job_id: str, kind: str, payload: Any = None
) -> int:
    cur = conn.execute(
        "INSERT INTO events (job_id, ts, kind, payload) VALUES (?, ?, ?, ?)",
        (job_id, now(), kind, json.dumps(payload) if payload is not None else None),
    )
    return int(cur.lastrowid)


def get_events(
    conn: sqlite3.Connection, job_id: str, after_id: int = 0
) -> list[dict[str, Any]]:
    rows: Iterable[sqlite3.Row] = conn.execute(
        "SELECT * FROM events WHERE job_id = ? AND id > ? ORDER BY id",
        (job_id, after_id),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"]) if item["payload"] else None
        out.append(item)
    return out
