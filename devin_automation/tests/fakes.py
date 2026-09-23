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
"""In-memory doubles for the GitHub and Devin clients."""

from __future__ import annotations

from itertools import count
from typing import Any

from devin_automation.devin_client import SessionSnapshot

JSONDict = dict[str, Any]

BOT_LOGIN = "devin-bot"


class FakeGitHub:
    def __init__(self) -> None:
        self.issues: dict[int, JSONDict] = {}
        self.comments: dict[int, list[JSONDict]] = {}
        self.pulls: dict[int, JSONDict] = {}
        self.statuses: dict[str, str] = {}
        self.merged: list[int] = []
        self.posted: list[tuple[int, str]] = []
        self.whoami_login = BOT_LOGIN
        self._comment_ids = count(1000)

    # --- test helpers ----------------------------------------------------

    def add_issue(self, number: int, title: str, author: str, body: str = "") -> None:
        self.issues[number] = {
            "number": number,
            "title": title,
            "body": body,
            "user": {"login": author},
            "html_url": f"https://github.com/o/r/issues/{number}",
        }
        self.comments.setdefault(number, [])

    def add_comment(self, number: int, author: str, body: str) -> int:
        comment_id = next(self._comment_ids)
        self.comments.setdefault(number, []).append(
            {"id": comment_id, "user": {"login": author}, "body": body}
        )
        return comment_id

    def add_pull(
        self,
        number: int,
        *,
        state: str = "open",
        merged: bool = False,
        mergeable: bool = True,
        sha: str = "deadbeef",
    ) -> None:
        self.pulls[number] = {
            "number": number,
            "state": state,
            "merged": merged,
            "mergeable": mergeable,
            "mergeable_state": "clean" if mergeable else "dirty",
            "title": f"fix: pr {number}",
            "html_url": f"https://github.com/o/r/pull/{number}",
            "head": {"sha": sha},
        }
        self.statuses.setdefault(sha, "success")

    # --- client surface --------------------------------------------------

    async def whoami(self) -> str:
        return self.whoami_login

    async def list_issues_updated_since(
        self, since: str | None, labels: str = ""
    ) -> list[JSONDict]:
        return list(self.issues.values())

    async def get_issue(self, number: int) -> JSONDict:
        return self.issues[number]

    async def list_issue_comments(self, number: int, since: str = "") -> list[JSONDict]:
        return list(self.comments.get(number, []))

    async def create_issue_comment(self, number: int, body: str) -> JSONDict:
        self.posted.append((number, body))
        comment_id = next(self._comment_ids)
        self.comments.setdefault(number, []).append(
            {"id": comment_id, "user": {"login": BOT_LOGIN}, "body": body}
        )
        return {"id": comment_id, "user": {"login": BOT_LOGIN}}

    async def get_pull(self, number: int) -> JSONDict:
        return self.pulls[number]

    async def combined_status(self, sha: str) -> str:
        return self.statuses.get(sha, "success")

    async def merge_pull(
        self, number: int, merge_method: str = "squash", commit_title: str = ""
    ) -> JSONDict:
        self.merged.append(number)
        self.pulls[number]["merged"] = True
        self.pulls[number]["state"] = "closed"
        return {"merged": True}


class FakeDevin:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionSnapshot] = {}
        self.messages: list[tuple[str, str]] = []
        self.created: list[JSONDict] = []
        self._ids = count(1)

    def set_session(
        self,
        session_id: str,
        *,
        status_enum: str = "finished",
        structured_output: JSONDict | None = None,
        pull_request_url: str | None = None,
        messages: list[JSONDict] | None = None,
    ) -> None:
        self.sessions[session_id] = SessionSnapshot(
            session_id=session_id,
            status="finished",
            status_enum=status_enum,
            structured_output=structured_output,
            pull_request_url=pull_request_url,
            url=f"https://app.devin.ai/sessions/{session_id}",
            messages=messages or [],
        )

    async def create_session(
        self,
        prompt: str,
        *,
        title: str | None = None,
        tags: list[str] | None = None,
        structured_output_schema: dict[str, object] | None = None,
        idempotent: bool = True,
    ) -> JSONDict:
        session_id = f"devin-{next(self._ids)}"
        self.created.append({"session_id": session_id, "prompt": prompt, "tags": tags})
        self.set_session(session_id, status_enum="working")
        return {"session_id": session_id, "url": f"https://app.devin.ai/{session_id}"}

    async def send_message(self, session_id: str, message: str) -> None:
        self.messages.append((session_id, message))

    async def get_session(self, session_id: str) -> SessionSnapshot:
        return self.sessions[session_id]
