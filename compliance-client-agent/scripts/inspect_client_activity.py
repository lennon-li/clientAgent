#!/usr/bin/env python3
"""Read-only inspection of compliance client requests, jobs, and events."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "state" / "gateway" / "gateway.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--requests", action="store_true",
        help="include the client request text (may contain sensitive content)",
    )
    parser.add_argument("--events", metavar="JOB_ID", help="show one job's event log")
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if args.events:
        rows = conn.execute(
            "SELECT datetime(ts,'unixepoch','localtime') AS time, kind, payload "
            "FROM events WHERE job_id = ? ORDER BY id",
            (args.events,),
        ).fetchall()
    else:
        request_column = ", request" if args.requests else ""
        rows = conn.execute(
            "SELECT datetime(created_at,'unixepoch','localtime') AS created, "
            "job_id, user_id, chat_id, status, branch, worktree, commit_before, "
            "commit_after, files_changed, commands_run, preview_requested, "
            f"preview_url, preview_error, handoff_path{request_column} "
            "FROM jobs ORDER BY created_at DESC LIMIT ?",
            (max(1, args.limit),),
        ).fetchall()

    print(json.dumps([dict(row) for row in rows], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
