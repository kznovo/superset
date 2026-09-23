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
"""State machine tests driven entirely through fake GitHub/Devin clients."""

from __future__ import annotations

from typing import Any, cast

import pytest

from devin_automation.config import Settings
from devin_automation.models import IssueState
from devin_automation.orchestrator import COMMENT_MARKER, Orchestrator
from devin_automation.store import Store
from devin_automation.tests.fakes import BOT_LOGIN, FakeDevin, FakeGitHub


@pytest.fixture
def setup() -> tuple[Orchestrator, FakeGitHub, FakeDevin, Store]:
    settings = Settings(
        github_token="t",  # noqa: S106
        devin_api_key="k",  # noqa: S106
        repo="o/r",
        state_db_path=":memory:",
        max_clarification_rounds=2,
        max_review_rounds=1,
    )
    store = Store(settings.state_db_path)
    github = FakeGitHub()
    devin = FakeDevin()
    orchestrator = Orchestrator(settings, store, cast(Any, github), cast(Any, devin))
    return orchestrator, github, devin, store


async def test_new_issue_starts_a_session(setup: Any) -> None:
    orchestrator, github, devin, store = setup
    github.add_issue(1, "Chart breaks", "alice", body="It breaks")

    await orchestrator.run_cycle()

    issue = store.get_issue(1)
    assert issue is not None
    assert issue.state is IssueState.IMPLEMENTING
    assert issue.session_id == devin.created[0]["session_id"]
    assert "Handle issue #1" in devin.created[0]["prompt"]


async def test_issues_from_the_token_owner_are_processed(setup: Any) -> None:
    # The token often belongs to a human who also files issues.
    orchestrator, github, devin, store = setup
    github.add_issue(1, "Chart breaks", BOT_LOGIN)

    await orchestrator.run_cycle()

    issue = store.get_issue(1)
    assert issue is not None
    assert issue.state is IssueState.IMPLEMENTING
    assert len(devin.created) == 1


async def test_configured_ignored_authors_are_skipped(setup: Any) -> None:
    orchestrator, github, devin, store = setup
    orchestrator.settings.ignored_authors = ("noisy-bot",)
    github.add_issue(1, "Chart breaks", "noisy-bot")

    await orchestrator.run_cycle()

    assert store.get_issue(1) is None
    assert devin.created == []


async def test_needs_clarification_comments_and_waits(setup: Any) -> None:
    orchestrator, github, devin, store = setup
    github.add_issue(1, "Vague", "alice")
    await orchestrator.run_cycle()
    session_id = devin.created[0]["session_id"]
    devin.set_session(
        session_id,
        structured_output={"outcome": "needs_clarification", "question": "Which DB?"},
    )

    await orchestrator.run_cycle()

    issue = store.get_issue(1)
    assert issue is not None
    assert issue.state is IssueState.AWAITING_REPORTER
    assert issue.clarification_rounds == 1
    number, body = github.posted[0]
    assert number == 1
    assert "@alice" in body
    assert "Which DB?" in body


async def test_reporter_reply_resumes_the_session(setup: Any) -> None:
    orchestrator, github, devin, store = setup
    github.add_issue(1, "Vague", "alice")
    await orchestrator.run_cycle()
    session_id = devin.created[0]["session_id"]
    devin.set_session(
        session_id,
        structured_output={"outcome": "needs_clarification", "question": "Which DB?"},
    )
    await orchestrator.run_cycle()
    github.add_comment(1, "alice", "Postgres 17")

    await orchestrator.run_cycle()

    issue = store.get_issue(1)
    assert issue is not None
    assert issue.state is IssueState.IMPLEMENTING
    assert "Postgres 17" in devin.messages[0][1]


async def test_clarification_budget_is_bounded(setup: Any) -> None:
    orchestrator, github, devin, store = setup
    github.add_issue(1, "Vague", "alice")
    await orchestrator.run_cycle()
    session_id = devin.created[0]["session_id"]

    for round_number in range(3):
        devin.set_session(
            session_id,
            structured_output={
                "outcome": "needs_clarification",
                "question": f"q{round_number}",
            },
        )
        await orchestrator.run_cycle()
        github.add_comment(1, "alice", "still vague")
        await orchestrator.run_cycle()

    issue = store.get_issue(1)
    assert issue is not None
    assert issue.state is IssueState.ABANDONED
    assert "rounds of questions" in github.posted[-1][1]


async def test_pr_opened_review_then_merge_keeps_issue_open(setup: Any) -> None:
    orchestrator, github, devin, store = setup
    github.add_issue(1, "Bug", "alice")
    await orchestrator.run_cycle()
    impl_session = devin.created[0]["session_id"]

    github.add_pull(42)
    devin.set_session(
        impl_session,
        structured_output={
            "outcome": "pr_opened",
            "pr_url": "https://github.com/o/r/pull/42",
        },
    )
    await orchestrator.run_cycle()
    issue = store.get_issue(1)
    assert issue is not None
    assert issue.state is IssueState.REVIEWING
    assert issue.pr_number == 42
    assert "pull/42" in github.posted[0][1]

    # Next cycle starts the review session.
    await orchestrator.run_cycle()
    review_session = store.get_review_session(42)
    assert review_session is not None

    devin.set_session(review_session, structured_output={"verdict": "ready_to_merge"})
    await orchestrator.run_cycle()

    assert github.merged == [42]
    issue = store.get_issue(1)
    assert issue is not None
    assert issue.state is IssueState.MERGED
    # The issue itself is never closed by the automation, only commented on.
    assert not hasattr(github, "close_issue")
    assert "Leaving this issue open" in github.posted[-1][1]


async def test_failing_ci_sends_the_session_back_to_work(setup: Any) -> None:
    orchestrator, github, devin, store = setup
    github.add_issue(1, "Bug", "alice")
    await orchestrator.run_cycle()
    impl_session = devin.created[0]["session_id"]
    github.add_pull(42, sha="cafe")
    github.statuses["cafe"] = "failure"
    devin.set_session(
        impl_session,
        structured_output={
            "outcome": "pr_opened",
            "pr_url": "https://github.com/o/r/pull/42",
        },
    )
    await orchestrator.run_cycle()
    await orchestrator.run_cycle()
    review_session = store.get_review_session(42)
    assert review_session is not None
    devin.set_session(review_session, structured_output={"verdict": "ready_to_merge"})

    await orchestrator.run_cycle()

    assert github.merged == []
    issue = store.get_issue(1)
    assert issue is not None
    assert issue.state is IssueState.IMPLEMENTING
    assert "CI is failing" in devin.messages[-1][1]


async def test_review_budget_is_bounded(setup: Any) -> None:
    orchestrator, github, devin, store = setup
    github.add_issue(1, "Bug", "alice")
    await orchestrator.run_cycle()
    impl_session = devin.created[0]["session_id"]
    github.add_pull(42)
    devin.set_session(
        impl_session,
        structured_output={
            "outcome": "pr_opened",
            "pr_url": "https://github.com/o/r/pull/42",
        },
    )
    await orchestrator.run_cycle()

    for _ in range(2):
        await orchestrator.run_cycle()
        review_session = store.get_review_session(42)
        if review_session is None:
            break
        devin.set_session(
            review_session,
            structured_output={"verdict": "changes_needed", "findings": ["add tests"]},
        )
        await orchestrator.run_cycle()
        devin.set_session(
            impl_session,
            structured_output={
                "outcome": "pr_opened",
                "pr_url": "https://github.com/o/r/pull/42",
            },
        )
        await orchestrator.run_cycle()

    issue = store.get_issue(1)
    assert issue is not None
    assert issue.state is IssueState.ABANDONED
    assert github.merged == []


async def test_dry_run_never_writes(setup: Any) -> None:
    orchestrator, github, devin, store = setup
    orchestrator.settings.dry_run = True
    github.add_issue(1, "Bug", "alice")

    await orchestrator.run_cycle()

    assert devin.created == []
    assert github.posted == []
    issue = store.get_issue(1)
    assert issue is not None
    assert issue.state is IssueState.NEW


async def test_concurrency_budget_defers_extra_issues(setup: Any) -> None:
    orchestrator, github, devin, store = setup
    orchestrator.settings.max_concurrent_sessions = 1
    github.add_issue(1, "Bug one", "alice")
    github.add_issue(2, "Bug two", "bob")

    await orchestrator.run_cycle()

    assert len(devin.created) == 1
    states = {issue.number: issue.state for issue in store.list_issues()}
    assert IssueState.NEW in states.values()


async def test_own_clarification_does_not_read_as_a_reply(setup: Any) -> None:
    # Posted under the same login as the reporter: only the marker separates them.
    orchestrator, github, devin, _store = setup
    github.add_issue(1, "Vague", BOT_LOGIN)
    await orchestrator.run_cycle()
    devin.set_session(
        devin.created[0]["session_id"],
        structured_output={"outcome": "needs_clarification", "question": "Which DB?"},
    )
    await orchestrator.run_cycle()
    assert COMMENT_MARKER in github.posted[-1][1]

    await orchestrator.run_cycle()

    assert devin.messages == []
