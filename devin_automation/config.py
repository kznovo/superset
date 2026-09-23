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
"""Runtime configuration for the Devin automation service."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings.

    The service is designed to boot even when credentials are absent so that
    ``docker compose up`` never fails because of a missing token; in that case
    it reports ``configured=False`` and the poller stays idle.
    """

    model_config = SettingsConfigDict(
        env_prefix="DEVIN_AUTOMATION_",
        env_file=".env",
        extra="ignore",
    )

    # --- credentials -----------------------------------------------------
    github_token: str = ""
    devin_api_key: str = ""

    # --- targets ---------------------------------------------------------
    repo: str = "kznovo/superset"
    base_branch: str = "master"
    github_api_url: str = "https://api.github.com"
    devin_api_url: str = "https://api.devin.ai"

    # --- behaviour -------------------------------------------------------
    enabled: bool = True
    dry_run: bool = False
    poll_interval_seconds: int = Field(default=60, ge=5)
    session_poll_interval_seconds: int = Field(default=60, ge=5)
    merge_method: str = "squash"
    auto_merge: bool = True
    require_green_ci: bool = True

    # Safety valves: every loop in this service is bounded.
    max_clarification_rounds: int = Field(default=3, ge=0)
    max_review_rounds: int = Field(default=3, ge=0)
    max_concurrent_sessions: int = Field(default=3, ge=1)
    issue_label_filter: str = ""
    ignored_authors: tuple[str, ...] = ()

    # --- storage ---------------------------------------------------------
    state_db_path: str = "/data/devin_automation.sqlite"

    @field_validator("merge_method")
    @classmethod
    def _valid_merge_method(cls, value: str) -> str:
        if value not in (allowed := {"merge", "squash", "rebase"}):
            raise ValueError(f"merge_method must be one of {sorted(allowed)}")
        return value

    @property
    def configured(self) -> bool:
        """Whether the service has everything it needs to actually run."""
        return bool(self.github_token and self.devin_api_key and self.repo)

    @property
    def missing_settings(self) -> list[str]:
        missing = []
        if not self.github_token:
            missing.append("DEVIN_AUTOMATION_GITHUB_TOKEN")
        if not self.devin_api_key:
            missing.append("DEVIN_AUTOMATION_DEVIN_API_KEY")
        if not self.repo:
            missing.append("DEVIN_AUTOMATION_REPO")
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
