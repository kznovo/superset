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
"""HTTP surface tests driven through a runtime wired to an in-memory store."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from devin_automation.config import Settings
from devin_automation.main import app, Runtime


@contextmanager
def client_for(dry_run: bool, auto_merge: bool) -> Iterator[TestClient]:
    """Serve requests from a runtime that never touches the environment."""
    settings = Settings(
        _env_file=None,
        github_token="t",  # noqa: S106
        devin_api_key="k",  # noqa: S106
        repo="o/r",
        state_db_path=":memory:",
        dry_run=dry_run,
        auto_merge=auto_merge,
    )
    runtime = Runtime(settings)
    previous = getattr(app.state, "runtime", None)
    app.state.runtime = runtime
    try:
        yield TestClient(app)
    finally:
        runtime.store.close()
        app.state.runtime = previous


def test_status_reports_write_mode_flags_when_writing() -> None:
    with client_for(dry_run=False, auto_merge=True) as client:
        payload = client.get("/status").json()

    assert payload["repo"] == "o/r"
    assert payload["dry_run"] is False
    assert payload["auto_merge"] is True


def test_status_reports_write_mode_flags_when_read_only() -> None:
    with client_for(dry_run=True, auto_merge=False) as client:
        payload = client.get("/status").json()

    assert payload["dry_run"] is True
    assert payload["auto_merge"] is False
