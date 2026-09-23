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
"""Domain models shared by the poller, the orchestrator and the API."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class IssueState(str, Enum):
    """Lifecycle of a single tracked GitHub issue."""

    NEW = "new"
    #: A Devin session is implementing (or re-implementing) a fix.
    IMPLEMENTING = "implementing"
    #: Devin asked the reporter a question and we are waiting for a reply.
    AWAITING_REPORTER = "awaiting_reporter"
    #: A pull request exists and is being reviewed / iterated on.
    REVIEWING = "reviewing"
    #: The pull request was merged. The issue itself is intentionally left open.
    MERGED = "merged"
    #: Gave up: a bounded retry/clarification budget was exhausted.
    ABANDONED = "abandoned"
    FAILED = "failed"


class PullRequestState(str, Enum):
    OPEN = "open"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    MERGED = "merged"
    CLOSED = "closed"


#: Devin returns this structured payload when it finishes an implementation run.
IMPLEMENTATION_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["outcome"],
    "properties": {
        "outcome": {
            "type": "string",
            "enum": ["pr_opened", "needs_clarification", "not_actionable"],
        },
        "pr_url": {"type": "string"},
        "question": {
            "type": "string",
            "description": "Markdown question to post back on the issue.",
        },
        "summary": {"type": "string"},
    },
}

#: Devin returns this structured payload when it finishes a review run.
REVIEW_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict"],
    "properties": {
        "verdict": {"type": "string", "enum": ["ready_to_merge", "changes_needed"]},
        "findings": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
}


@dataclass
class TrackedIssue:
    number: int
    title: str = ""
    author: str = ""
    state: IssueState = IssueState.NEW
    session_id: str | None = None
    pr_number: int | None = None
    clarification_rounds: int = 0
    review_rounds: int = 0
    last_seen_comment_id: int = 0
    last_error: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "title": self.title,
            "author": self.author,
            "state": self.state.value,
            "session_id": self.session_id,
            "pr_number": self.pr_number,
            "clarification_rounds": self.clarification_rounds,
            "review_rounds": self.review_rounds,
            "last_seen_comment_id": self.last_seen_comment_id,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }


@dataclass
class PollReport:
    """Summary of a single polling cycle, surfaced through ``GET /status``."""

    started_at: str = ""
    finished_at: str = ""
    issues_scanned: int = 0
    sessions_started: int = 0
    comments_posted: int = 0
    prs_merged: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "issues_scanned": self.issues_scanned,
            "sessions_started": self.sessions_started,
            "comments_posted": self.comments_posted,
            "prs_merged": self.prs_merged,
            "errors": self.errors,
        }
