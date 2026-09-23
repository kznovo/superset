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

import pytest
from fastapi.testclient import TestClient

from devin_automation.config import Settings
from devin_automation.main import app, Runtime


def _client(**overrides: object) -> Iterator[TestClient]:
    settings = Settings(
        github_token="t",  # noqa: S106
        devin_api_key="k",  # noqa: S106
        repo="o/r",
        state_db_path=":memory:",
        **overrides,
    )
    runtime = Runtime(settings)
    app.state.runtime = runtime
    try:
        yield TestClient(app)
    finally:
        runtime.store.close()
        app.state.runtime = None


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield from _client()


def test_status_reports_defaults(client: TestClient) -> None:
    payload = client.get("/status").json()

    assert payload["repo"] == "o/r"
    assert payload["dry_run"] is False
    assert payload["auto_merge"] is True


def test_status_reports_write_mode_flags() -> None:
    for client in _client(dry_run=True, auto_merge=False):
        payload = client.get("/status").json()

        assert payload["dry_run"] is True
        assert payload["auto_merge"] is False
