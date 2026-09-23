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
"""The issue -> session -> pull request -> merge state machine.

One cycle of :meth:`Orchestrator.run_cycle` is idempotent: it reconciles the
persisted state of every tracked issue against GitHub and Devin, so a crash
between two steps only costs a repeated read, never a duplicated comment,
session or merge.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from devin_automation import prompts
from devin_automation.config import Settings
from devin_automation.devin_client import DevinClient, SessionSnapshot
from devin_automation.github_client import GitHubClient, pull_number_from_url
from devin_automation.models import (
    IMPLEMENTATION_OUTPUT_SCHEMA,
    IssueState,
    PollReport,
    REVIEW_OUTPUT_SCHEMA,
    TrackedIssue,
)
from devin_automation.store import Store

logger = logging.getLogger(__name__)

JSONDict = dict[str, Any]

ISSUE_CURSOR_KEY = "issues_updated_since"
#: Footer stamped on every comment the automation posts, so its own comments are
#: recognisable even when the token belongs to a human who also comments.
COMMENT_MARKER = "<!-- devin-automation -->"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        github: GitHubClient,
        devin: DevinClient,
    ) -> None:
        self.settings = settings
        self.store = store
        self.github = github
        self.devin = devin
        self.last_report: PollReport | None = None

    # --- helpers ---------------------------------------------------------

    def _is_reply(self, comment: JSONDict) -> bool:
        """Whether a comment is human input the automation should react to."""
        if COMMENT_MARKER in str(comment.get("body") or ""):
            return False
        login = str((comment.get("user") or {}).get("login", ""))
        return login not in self.settings.ignored_authors

    async def _comment(self, issue_number: int, body: str, report: PollReport) -> None:
        body = f"{body}\n\n{COMMENT_MARKER}"
        if self.settings.dry_run:
            logger.info("[dry-run] would comment on #%s:\n%s", issue_number, body)
            return
        await self.github.create_issue_comment(issue_number, body)
        report.comments_posted += 1

    def _save(self, issue: TrackedIssue) -> TrackedIssue:
        return self.store.upsert_issue(issue)

    # --- cycle -----------------------------------------------------------

    async def run_cycle(self) -> PollReport:
        report = PollReport(started_at=_utcnow_iso())
        try:
            await self._discover(report)
            await self._advance_all(report)
        except Exception as exc:  # noqa: BLE001 - a bad cycle must not kill the loop
            logger.exception("polling cycle failed")
            report.errors.append(str(exc))
        report.finished_at = _utcnow_iso()
        self.last_report = report
        return report

    async def _discover(self, report: PollReport) -> None:
        """Pick up newly opened issues and new comments on tracked issues."""
        since = self.store.get_cursor(ISSUE_CURSOR_KEY)
        cycle_started = _utcnow_iso()
        issues = await self.github.list_issues_updated_since(
            since, labels=self.settings.issue_label_filter
        )
        report.issues_scanned = len(issues)

        for payload in issues:
            number = int(payload["number"])
            author = str((payload.get("user") or {}).get("login", ""))
            if author in self.settings.ignored_authors:
                continue
            tracked = self.store.get_issue(number)
            if tracked is None:
                self._save(
                    TrackedIssue(
                        number=number,
                        title=str(payload.get("title", "")),
                        author=author,
                        state=IssueState.NEW,
                    )
                )
                logger.info("tracking new issue #%s", number)
            else:
                tracked.title = str(payload.get("title", tracked.title))
                self._save(tracked)

        self.store.set_cursor(ISSUE_CURSOR_KEY, cycle_started)

    async def _advance_all(self, report: PollReport) -> None:
        handlers = {
            IssueState.NEW: self._handle_new,
            IssueState.IMPLEMENTING: self._handle_implementing,
            IssueState.AWAITING_REPORTER: self._handle_awaiting_reporter,
            IssueState.REVIEWING: self._handle_reviewing,
        }
        for issue in self.store.list_issues_in_state(*handlers):
            try:
                await handlers[issue.state](issue, report)
            except Exception as exc:  # noqa: BLE001 - isolate per-issue failures
                logger.exception("failed to advance issue #%s", issue.number)
                issue.last_error = str(exc)
                self._save(issue)
                report.errors.append(f"#{issue.number}: {exc}")

    # --- state handlers --------------------------------------------------

    async def _handle_new(self, issue: TrackedIssue, report: PollReport) -> None:
        if self.store.count_active_sessions() >= self.settings.max_concurrent_sessions:
            logger.info("session budget reached, deferring issue #%s", issue.number)
            return

        payload = await self.github.get_issue(issue.number)
        comments = await self.github.list_issue_comments(issue.number)
        human_comments = [c for c in comments if self._is_reply(c)]
        comments_section = ""
        if human_comments:
            comments_section = (
                "\n--- EXISTING COMMENTS ---\n"
                + prompts.render_comments(human_comments)
                + "\n--- END EXISTING COMMENTS ---\n"
            )

        prompt = prompts.IMPLEMENTATION_PROMPT.format(
            repo=self.settings.repo,
            base_branch=self.settings.base_branch,
            number=issue.number,
            title=payload.get("title", ""),
            issue_url=payload.get("html_url", ""),
            author=issue.author,
            body=payload.get("body") or "(no description provided)",
            comments_section=comments_section,
        )

        if comments:
            issue.last_seen_comment_id = max(int(c["id"]) for c in comments)

        if self.settings.dry_run:
            logger.info("[dry-run] would start session for #%s", issue.number)
            return

        session = await self.devin.create_session(
            prompt,
            title=f"[auto] {self.settings.repo}#{issue.number}: {issue.title}"[:120],
            tags=["devin-automation", f"issue-{issue.number}"],
            structured_output_schema=IMPLEMENTATION_OUTPUT_SCHEMA,
        )
        issue.session_id = str(session["session_id"])
        issue.state = IssueState.IMPLEMENTING
        self._save(issue)
        report.sessions_started += 1
        logger.info("started session %s for issue #%s", issue.session_id, issue.number)

    async def _handle_implementing(
        self, issue: TrackedIssue, report: PollReport
    ) -> None:
        if not issue.session_id:
            issue.state = IssueState.NEW
            self._save(issue)
            return

        snapshot = await self.devin.get_session(issue.session_id)
        if not snapshot.is_settled:
            return

        output = snapshot.structured_output or {}
        outcome = str(output.get("outcome", ""))

        if outcome == "needs_clarification":
            await self._ask_reporter(
                issue, str(output.get("question", "")), snapshot.url, report
            )
        elif outcome == "pr_opened":
            await self._register_pull_request(issue, output, snapshot, report)
        elif outcome == "not_actionable":
            await self._abandon(
                issue, str(output.get("summary", "the issue is not actionable")), report
            )
        elif snapshot.pull_request_url:
            await self._register_pull_request(
                issue, {"pr_url": snapshot.pull_request_url}, snapshot, report
            )
        elif snapshot.is_blocked:
            # Stuck without an answer of its own: whatever it last said is a
            # question for the reporter.
            await self._ask_reporter(
                issue, self._last_devin_message(snapshot), snapshot.url, report
            )
        else:
            await self._abandon(
                issue, "the session finished without a pull request", report
            )

    async def _handle_awaiting_reporter(
        self, issue: TrackedIssue, report: PollReport
    ) -> None:
        comments = await self.github.list_issue_comments(issue.number)
        new_comments = [
            comment
            for comment in comments
            if int(comment["id"]) > issue.last_seen_comment_id
            and self._is_reply(comment)
        ]
        if not new_comments:
            return

        issue.last_seen_comment_id = max(int(c["id"]) for c in new_comments)
        message = prompts.CLARIFICATION_REPLY_PROMPT.format(
            number=issue.number,
            base_branch=self.settings.base_branch,
            comments=prompts.render_comments(new_comments),
        )
        if self.settings.dry_run:
            logger.info("[dry-run] would reply to session for #%s", issue.number)
            return
        await self._send_or_restart(issue, message, report)
        issue.state = IssueState.IMPLEMENTING
        self._save(issue)

    async def _handle_reviewing(self, issue: TrackedIssue, report: PollReport) -> None:
        if issue.pr_number is None:
            issue.state = IssueState.IMPLEMENTING
            self._save(issue)
            return

        pull = await self.github.get_pull(issue.pr_number)
        if pull.get("merged"):
            await self._on_merged(issue, str(pull.get("html_url", "")), report)
            return
        if pull.get("state") == "closed":
            await self._abandon(issue, "the pull request was closed", report)
            return

        review_session_id = self.store.get_review_session(issue.pr_number)
        if review_session_id is None:
            await self._start_review(issue, pull, report)
            return

        snapshot = await self.devin.get_session(review_session_id)
        if not snapshot.is_settled:
            return

        output = snapshot.structured_output or {}
        verdict = str(output.get("verdict", ""))
        self.store.clear_review_session(issue.pr_number)

        if verdict == "ready_to_merge":
            await self._maybe_merge(issue, pull, report)
            return

        findings = output.get("findings") or [
            output.get("summary", "See the review session for details.")
        ]
        issue.review_rounds += 1
        if issue.review_rounds > self.settings.max_review_rounds:
            await self._abandon(
                issue,
                f"the review did not converge after "
                f"{self.settings.max_review_rounds} rounds",
                report,
            )
            return

        message = prompts.FIX_PROMPT.format(
            pr_url=pull.get("html_url", ""),
            round_number=issue.review_rounds,
            findings="\n".join(f"- {finding}" for finding in findings),
        )
        await self._send_or_restart(issue, message, report)
        issue.state = IssueState.IMPLEMENTING
        self._save(issue)

    # --- transitions -----------------------------------------------------

    async def _start_review(
        self, issue: TrackedIssue, pull: JSONDict, report: PollReport
    ) -> None:
        if self.settings.dry_run:
            logger.info("[dry-run] would start review for PR #%s", issue.pr_number)
            return
        prompt = prompts.REVIEW_PROMPT.format(
            pr_url=pull.get("html_url", ""),
            repo=self.settings.repo,
            issue_number=issue.number,
            issue_title=issue.title,
            issue_url=f"https://github.com/{self.settings.repo}/issues/{issue.number}",
        )
        session = await self.devin.create_session(
            prompt,
            title=f"[auto-review] {self.settings.repo}#{issue.pr_number}"[:120],
            tags=["devin-automation", "review", f"issue-{issue.number}"],
            structured_output_schema=REVIEW_OUTPUT_SCHEMA,
            # Every round reviews the same pull request URL, so an idempotent
            # create would hand back the previous round's session — verdict
            # included — and the loop would spend its rounds re-reading it.
            idempotent=False,
        )
        assert issue.pr_number is not None
        self.store.set_review_session(issue.pr_number, str(session["session_id"]))
        report.sessions_started += 1

    async def _maybe_merge(
        self, issue: TrackedIssue, pull: JSONDict, report: PollReport
    ) -> None:
        if not self.settings.auto_merge:
            logger.info("auto-merge disabled; PR #%s is ready", issue.pr_number)
            return
        if pull.get("mergeable_state") == "dirty" or pull.get("mergeable") is False:
            await self._send_or_restart(
                issue,
                f"Pull request {pull.get('html_url')} has merge conflicts with "
                f"`{self.settings.base_branch}`. Rebase it and resolve them.",
                report,
            )
            issue.state = IssueState.IMPLEMENTING
            self._save(issue)
            return

        if self.settings.require_green_ci:
            sha = str((pull.get("head") or {}).get("sha", ""))
            status = await self.github.combined_status(sha)
            if status == "pending":
                logger.info("CI still pending for PR #%s", issue.pr_number)
                return
            if status == "failure":
                await self._send_or_restart(
                    issue,
                    f"CI is failing on {pull.get('html_url')}. Investigate the "
                    "failing jobs and push fixes.",
                    report,
                )
                issue.state = IssueState.IMPLEMENTING
                self._save(issue)
                return

        assert issue.pr_number is not None
        if self.settings.dry_run:
            logger.info("[dry-run] would merge PR #%s", issue.pr_number)
            return
        await self.github.merge_pull(
            issue.pr_number,
            merge_method=self.settings.merge_method,
            commit_title=f"{pull.get('title', '')} (#{issue.pr_number})",
        )
        report.prs_merged += 1
        await self._on_merged(issue, str(pull.get("html_url", "")), report)

    async def _on_merged(
        self, issue: TrackedIssue, pr_url: str, report: PollReport
    ) -> None:
        # The issue is deliberately left open; only a comment is posted.
        await self._comment(
            issue.number,
            prompts.MERGED_COMMENT.format(author=issue.author, pr_url=pr_url),
            report,
        )
        issue.state = IssueState.MERGED
        self._save(issue)

    async def _register_pull_request(
        self,
        issue: TrackedIssue,
        output: JSONDict,
        snapshot: SessionSnapshot,
        report: PollReport,
    ) -> None:
        pr_url = str(output.get("pr_url") or snapshot.pull_request_url or "")
        number = pull_number_from_url(pr_url)
        if number is None:
            await self._abandon(
                issue,
                f"could not determine a pull request number from {pr_url!r}",
                report,
            )
            return
        first_time = issue.pr_number != number
        issue.pr_number = number
        issue.state = IssueState.REVIEWING
        self._save(issue)
        if first_time:
            await self._comment(
                issue.number,
                prompts.PR_OPENED_COMMENT.format(
                    author=issue.author, pr_url=pr_url, session_url=snapshot.url
                ),
                report,
            )

    async def _ask_reporter(
        self, issue: TrackedIssue, question: str, session_url: str, report: PollReport
    ) -> None:
        issue.clarification_rounds += 1
        if issue.clarification_rounds > self.settings.max_clarification_rounds:
            await self._abandon(
                issue,
                f"the issue was still unclear after "
                f"{self.settings.max_clarification_rounds} rounds of questions",
                report,
            )
            return
        await self._comment(
            issue.number,
            prompts.CLARIFICATION_COMMENT.format(
                author=issue.author,
                question=question or "Could you share more details?",
                session_url=session_url,
            ),
            report,
        )
        comments = await self.github.list_issue_comments(issue.number)
        if comments:
            issue.last_seen_comment_id = max(int(c["id"]) for c in comments)
        issue.state = IssueState.AWAITING_REPORTER
        self._save(issue)

    async def _abandon(
        self, issue: TrackedIssue, reason: str, report: PollReport
    ) -> None:
        await self._comment(
            issue.number,
            prompts.ABANDONED_COMMENT.format(author=issue.author, reason=reason),
            report,
        )
        issue.state = IssueState.ABANDONED
        issue.last_error = reason
        self._save(issue)

    async def _send_or_restart(
        self, issue: TrackedIssue, message: str, report: PollReport
    ) -> None:
        """Message the implementation session, starting a new one if it is gone."""
        if self.settings.dry_run:
            logger.info("[dry-run] would message session for #%s", issue.number)
            return
        if issue.session_id:
            try:
                await self.devin.send_message(issue.session_id, message)
                return
            except Exception:  # noqa: BLE001 - expired sessions cannot be resumed
                logger.warning(
                    "session %s unreachable, starting a new one", issue.session_id
                )
        session = await self.devin.create_session(
            f"You are continuing work on `{self.settings.repo}` issue "
            f"#{issue.number}.\n\n{message}",
            title=f"[auto] {self.settings.repo}#{issue.number} (continued)"[:120],
            tags=["devin-automation", f"issue-{issue.number}"],
            structured_output_schema=IMPLEMENTATION_OUTPUT_SCHEMA,
        )
        issue.session_id = str(session["session_id"])
        self._save(issue)
        report.sessions_started += 1

    @staticmethod
    def _last_devin_message(snapshot: SessionSnapshot) -> str:
        for message in reversed(snapshot.messages):
            if message.get("type") in ("devin_message", "devin_message_sent"):
                return str(message.get("message", ""))
        return ""
