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
"""Thin async GitHub REST client covering only what the automation needs."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

JSONDict = dict[str, Any]


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(
        self,
        token: str,
        repo: str,
        api_url: str = "https://api.github.com",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.repo = repo
        self._client = client or httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            timeout=httpx.Timeout(30.0),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise GitHubError(
                f"{method} {path} failed with {response.status_code}: {response.text}"
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def _paginate(self, path: str, params: JSONDict) -> list[JSONDict]:
        results: list[JSONDict] = []
        page = 1
        while True:
            batch = await self._request(
                "GET", path, params={**params, "per_page": 100, "page": page}
            )
            if not batch:
                break
            results.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return results

    # --- identity --------------------------------------------------------

    async def whoami(self) -> str:
        data = await self._request("GET", "/user")
        return str(data["login"])

    # --- issues ----------------------------------------------------------

    async def list_issues_updated_since(
        self, since: str | None, labels: str = ""
    ) -> list[JSONDict]:
        """Open issues touched since ``since``, excluding pull requests.

        GitHub models pull requests as issues on this endpoint, so they are
        filtered out by the presence of the ``pull_request`` key.
        """
        params: JSONDict = {"state": "open", "sort": "updated", "direction": "asc"}
        if since:
            params["since"] = since
        if labels:
            params["labels"] = labels
        issues = await self._paginate(f"/repos/{self.repo}/issues", params)
        return [issue for issue in issues if "pull_request" not in issue]

    async def get_issue(self, number: int) -> JSONDict:
        return dict(await self._request("GET", f"/repos/{self.repo}/issues/{number}"))

    async def list_issue_comments(self, number: int, since: str = "") -> list[JSONDict]:
        params: JSONDict = {}
        if since:
            params["since"] = since
        return await self._paginate(
            f"/repos/{self.repo}/issues/{number}/comments", params
        )

    async def create_issue_comment(self, number: int, body: str) -> JSONDict:
        return dict(
            await self._request(
                "POST",
                f"/repos/{self.repo}/issues/{number}/comments",
                json={"body": body},
            )
        )

    # --- pull requests ---------------------------------------------------

    async def get_pull(self, number: int) -> JSONDict:
        return dict(await self._request("GET", f"/repos/{self.repo}/pulls/{number}"))

    async def list_pull_reviews(self, number: int) -> list[JSONDict]:
        return await self._paginate(f"/repos/{self.repo}/pulls/{number}/reviews", {})

    async def combined_status(self, sha: str) -> str:
        """Roll check-runs and legacy statuses into one of success/pending/failure."""
        legacy = await self._request(
            "GET", f"/repos/{self.repo}/commits/{sha}/status", params={"per_page": 100}
        )
        checks = await self._request(
            "GET",
            f"/repos/{self.repo}/commits/{sha}/check-runs",
            params={"per_page": 100},
        )
        states = {str(legacy.get("state", "pending"))}
        if states == {"pending"} and not legacy.get("statuses"):
            states = set()
        for run in checks.get("check_runs", []):
            if run.get("status") != "completed":
                states.add("pending")
            elif run.get("conclusion") in ("success", "neutral", "skipped"):
                states.add("success")
            else:
                states.add("failure")
        if not states:
            return "success"
        if "failure" in states or "error" in states:
            return "failure"
        if "pending" in states:
            return "pending"
        return "success"

    async def merge_pull(
        self, number: int, merge_method: str = "squash", commit_title: str = ""
    ) -> JSONDict:
        payload: JSONDict = {"merge_method": merge_method}
        if commit_title:
            payload["commit_title"] = commit_title
        return dict(
            await self._request(
                "PUT", f"/repos/{self.repo}/pulls/{number}/merge", json=payload
            )
        )


def pull_number_from_url(url: str) -> int | None:
    """Extract the PR number from an ``https://github.com/o/r/pull/123`` URL."""
    parts = [part for part in url.rstrip("/").split("/") if part]
    if len(parts) < 2 or parts[-2] != "pull":
        return None
    try:
        return int(parts[-1])
    except ValueError:
        return None
