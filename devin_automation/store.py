#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
"""SQLite-backed state for the automation service.

State has to survive container restarts, otherwise a restart would re-trigger
Devin on every open issue. The database is tiny and single-writer, so plain
``sqlite3`` with a lock is enough.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from devin_automation.models import IssueState, TrackedIssue

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issues (
    number INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    session_id TEXT,
    pr_number INTEGER,
    clarification_rounds INTEGER NOT NULL DEFAULT 0,
    review_rounds INTEGER NOT NULL DEFAULT 0,
    last_seen_comment_id INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS review_sessions (
    pr_number INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- key/value -------------------------------------------------------

    def get_cursor(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM kv WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_cursor(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    # --- issues ----------------------------------------------------------

    @staticmethod
    def _row_to_issue(row: sqlite3.Row) -> TrackedIssue:
        return TrackedIssue(
            number=int(row["number"]),
            title=str(row["title"]),
            author=str(row["author"]),
            state=IssueState(row["state"]),
            session_id=row["session_id"],
            pr_number=row["pr_number"],
            clarification_rounds=int(row["clarification_rounds"]),
            review_rounds=int(row["review_rounds"]),
            last_seen_comment_id=int(row["last_seen_comment_id"]),
            last_error=str(row["last_error"]),
            updated_at=str(row["updated_at"]),
        )

    def get_issue(self, number: int) -> TrackedIssue | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM issues WHERE number = ?", (number,)
            ).fetchone()
        return self._row_to_issue(row) if row else None

    def list_issues(self) -> list[TrackedIssue]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM issues ORDER BY number DESC"
            ).fetchall()
        return [self._row_to_issue(row) for row in rows]

    def list_issues_in_state(self, *states: IssueState) -> list[TrackedIssue]:
        wanted = {state.value for state in states}
        return [issue for issue in self.list_issues() if issue.state.value in wanted]

    def upsert_issue(self, issue: TrackedIssue) -> TrackedIssue:
        issue.updated_at = _utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO issues (
                    number, title, author, state, session_id, pr_number,
                    clarification_rounds, review_rounds, last_seen_comment_id,
                    last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(number) DO UPDATE SET
                    title = excluded.title,
                    author = excluded.author,
                    state = excluded.state,
                    session_id = excluded.session_id,
                    pr_number = excluded.pr_number,
                    clarification_rounds = excluded.clarification_rounds,
                    review_rounds = excluded.review_rounds,
                    last_seen_comment_id = excluded.last_seen_comment_id,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    issue.number,
                    issue.title,
                    issue.author,
                    issue.state.value,
                    issue.session_id,
                    issue.pr_number,
                    issue.clarification_rounds,
                    issue.review_rounds,
                    issue.last_seen_comment_id,
                    issue.last_error,
                    issue.updated_at,
                ),
            )
            self._conn.commit()
        return issue

    def count_active_sessions(self) -> int:
        active = (IssueState.IMPLEMENTING, IssueState.REVIEWING)
        return len(self.list_issues_in_state(*active))

    # --- review sessions -------------------------------------------------

    def get_review_session(self, pr_number: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM review_sessions WHERE pr_number = ?",
                (pr_number,),
            ).fetchone()
        return str(row["session_id"]) if row else None

    def set_review_session(self, pr_number: int, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO review_sessions (pr_number, session_id, created_at) "
                "VALUES (?, ?, ?) ON CONFLICT(pr_number) DO UPDATE SET "
                "session_id = excluded.session_id, created_at = excluded.created_at",
                (pr_number, session_id, _utcnow()),
            )
            self._conn.commit()

    def clear_review_session(self, pr_number: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM review_sessions WHERE pr_number = ?", (pr_number,)
            )
            self._conn.commit()
