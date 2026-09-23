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
"""Async client for the Devin public API (https://docs.devin.ai)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

JSONDict = dict[str, Any]

#: ``status_enum`` values that mean the session will not progress on its own.
TERMINAL_STATUSES = frozenset({"finished", "expired"})
#: ``status_enum`` value meaning the session is waiting for a human reply.
BLOCKED_STATUS = "blocked"


class DevinError(RuntimeError):
    pass


@dataclass
class SessionSnapshot:
    session_id: str
    status: str
    status_enum: str | None
    structured_output: JSONDict | None
    pull_request_url: str | None
    url: str
    messages: list[JSONDict]

    @property
    def is_terminal(self) -> bool:
        return (self.status_enum or "") in TERMINAL_STATUSES

    @property
    def is_blocked(self) -> bool:
        return (self.status_enum or "") == BLOCKED_STATUS


class DevinClient:
    def __init__(
        self,
        api_key: str,
        api_url: str = "https://api.devin.ai",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            timeout=httpx.Timeout(60.0),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise DevinError(
                f"{method} {path} failed with {response.status_code}: {response.text}"
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def create_session(
        self,
        prompt: str,
        *,
        title: str | None = None,
        tags: list[str] | None = None,
        structured_output_schema: dict[str, object] | None = None,
        idempotent: bool = True,
    ) -> JSONDict:
        payload: JSONDict = {"prompt": prompt, "idempotent": idempotent}
        if title:
            payload["title"] = title
        if tags:
            payload["tags"] = tags
        if structured_output_schema:
            payload["structured_output_schema"] = structured_output_schema
        return dict(await self._request("POST", "/v1/sessions", json=payload))

    async def send_message(self, session_id: str, message: str) -> None:
        await self._request(
            "POST", f"/v1/sessions/{session_id}/message", json={"message": message}
        )

    async def get_session(self, session_id: str) -> SessionSnapshot:
        data = await self._request("GET", f"/v1/sessions/{session_id}")
        pull_request = data.get("pull_request") or {}
        structured = data.get("structured_output")
        return SessionSnapshot(
            session_id=str(data["session_id"]),
            status=str(data.get("status", "")),
            status_enum=data.get("status_enum"),
            structured_output=structured if isinstance(structured, dict) else None,
            pull_request_url=pull_request.get("url"),
            url=str(data.get("url", "")),
            messages=list(data.get("messages") or []),
        )
