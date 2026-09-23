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
from __future__ import annotations

import httpx
import pytest

from devin_automation.config import Settings
from devin_automation.github_client import GitHubClient, pull_number_from_url
from devin_automation.models import IssueState, TrackedIssue
from devin_automation.store import Store


def test_issue_round_trip() -> None:
    store = Store(":memory:")
    store.upsert_issue(
        TrackedIssue(number=7, title="t", author="a", state=IssueState.IMPLEMENTING)
    )
    store.upsert_issue(TrackedIssue(number=7, title="t2", author="a", pr_number=9))

    issue = store.get_issue(7)
    assert issue is not None
    assert issue.title == "t2"
    assert issue.pr_number == 9
    assert issue.state is IssueState.NEW


def test_cursor_round_trip() -> None:
    store = Store(":memory:")
    assert store.get_cursor("k") is None
    store.set_cursor("k", "v1")
    store.set_cursor("k", "v2")
    assert store.get_cursor("k") == "v2"


def test_count_active_sessions() -> None:
    store = Store(":memory:")
    store.upsert_issue(TrackedIssue(number=1, state=IssueState.IMPLEMENTING))
    store.upsert_issue(TrackedIssue(number=2, state=IssueState.REVIEWING))
    store.upsert_issue(TrackedIssue(number=3, state=IssueState.MERGED))
    assert store.count_active_sessions() == 2


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/o/r/pull/12", 12),
        ("https://github.com/o/r/pull/12/", 12),
        ("https://github.com/o/r/issues/12", None),
        ("nonsense", None),
    ],
)
def test_pull_number_from_url(url: str, expected: int | None) -> None:
    assert pull_number_from_url(url) == expected


def test_merge_method_is_validated() -> None:
    with pytest.raises(ValueError, match="merge_method"):
        Settings(merge_method="yolo")


def test_missing_settings_are_reported() -> None:
    settings = Settings(github_token="", devin_api_key="", repo="o/r")
    assert not settings.configured
    assert settings.missing_settings == [
        "DEVIN_AUTOMATION_GITHUB_TOKEN",
        "DEVIN_AUTOMATION_DEVIN_API_KEY",
    ]


async def test_combined_status_without_ci_visibility_blocks_merge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Resource not accessible"})

    client = GitHubClient(
        token="t",  # noqa: S106
        repo="o/r",
        client=httpx.AsyncClient(
            base_url="https://api.github.com", transport=httpx.MockTransport(handler)
        ),
    )
    assert await client.combined_status("sha") == "pending"
    assert await client.whoami() == ""
