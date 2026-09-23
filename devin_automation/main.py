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
"""FastAPI application exposing and controlling the polling loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from devin_automation.config import get_settings, Settings
from devin_automation.devin_client import DevinClient
from devin_automation.github_client import GitHubClient
from devin_automation.orchestrator import Orchestrator
from devin_automation.store import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("devin_automation")


class Runtime:
    """Holds the long-lived objects and the background polling task."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = Store(settings.state_db_path)
        self.orchestrator: Orchestrator | None = None
        self.task: asyncio.Task[None] | None = None
        self.paused = not settings.enabled
        self._github: GitHubClient | None = None
        self._devin: DevinClient | None = None
        self._cycle_lock = asyncio.Lock()

    def build(self) -> None:
        if not self.settings.configured:
            logger.warning(
                "not configured (missing %s); the poller stays idle",
                ", ".join(self.settings.missing_settings),
            )
            return
        self._github = GitHubClient(
            self.settings.github_token, self.settings.repo, self.settings.github_api_url
        )
        self._devin = DevinClient(
            self.settings.devin_api_key, self.settings.devin_api_url
        )
        self.orchestrator = Orchestrator(
            self.settings, self.store, self._github, self._devin
        )

    async def run_once(self) -> dict[str, Any]:
        if self.orchestrator is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "service is not configured; missing "
                    f"{', '.join(self.settings.missing_settings)}"
                ),
            )
        async with self._cycle_lock:
            report = await self.orchestrator.run_cycle()
        return report.as_dict()

    async def loop(self) -> None:
        while True:
            try:
                if not self.paused and self.orchestrator is not None:
                    await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must outlive any failure
                logger.exception("unexpected error in polling loop")
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def shutdown(self) -> None:
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self._github is not None:
            await self._github.aclose()
        if self._devin is not None:
            await self._devin.aclose()
        self.store.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    runtime = Runtime(get_settings())
    runtime.build()
    runtime.task = asyncio.create_task(runtime.loop())
    app.state.runtime = runtime
    try:
        yield
    finally:
        await runtime.shutdown()


app = FastAPI(
    title="Devin issue automation",
    description=(
        "Polls a GitHub repository for issue activity and drives Devin sessions "
        "to implement, review and merge the resulting pull requests."
    ),
    lifespan=lifespan,
)


def _runtime() -> Runtime:
    runtime: Runtime | None = getattr(app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="service is still starting")
    return runtime


@app.get("/health")
def health() -> dict[str, Any]:
    runtime = _runtime()
    return {
        "status": "ok",
        "configured": runtime.settings.configured,
        "missing_settings": runtime.settings.missing_settings,
        "paused": runtime.paused,
        "dry_run": runtime.settings.dry_run,
    }


@app.get("/status")
def status() -> dict[str, Any]:
    runtime = _runtime()
    last = runtime.orchestrator.last_report if runtime.orchestrator else None
    return {
        "repo": runtime.settings.repo,
        "paused": runtime.paused,
        "poll_interval_seconds": runtime.settings.poll_interval_seconds,
        "last_cycle": last.as_dict() if last else None,
        "tracked_issues": [issue.as_dict() for issue in runtime.store.list_issues()],
    }


@app.get("/issues/{number}")
def issue_detail(number: int) -> dict[str, Any]:
    issue = _runtime().store.get_issue(number)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"issue #{number} is not tracked")
    return issue.as_dict()


@app.post("/poll")
async def poll_now() -> dict[str, Any]:
    """Run a cycle immediately instead of waiting for the next tick."""
    return await _runtime().run_once()


@app.post("/pause")
def pause() -> dict[str, Any]:
    runtime = _runtime()
    runtime.paused = True
    return {"paused": True}


@app.post("/resume")
def resume() -> dict[str, Any]:
    runtime = _runtime()
    runtime.paused = False
    return {"paused": False}
