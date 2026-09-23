<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# Devin issue automation

A small FastAPI service that watches this repository's GitHub issues and drives
[Devin](https://docs.devin.ai) sessions to triage, fix, review and merge them.

It runs as its own container in `docker-compose.yml` and starts with
`docker compose up`. Without credentials it boots, reports
`configured: false` on `/health` and stays idle, so it never breaks a normal
Superset development environment.

## Flow

```
GitHub issue opened / commented
        │  (poll every 60s, cursor persisted in SQLite)
        ▼
  ┌───────────┐  not enough context   ┌────────────────────┐
  │IMPLEMENTING├──────────────────────►│ AWAITING_REPORTER  │
  │  session  │                        │ (comment @author)  │
  └─────┬─────┘◄───────────────────────┴────────────────────┘
        │ PR opened                      reporter replies
        ▼
  ┌───────────┐  changes needed
  │ REVIEWING │──────────► back to IMPLEMENTING (bounded rounds)
  │  session  │
  └─────┬─────┘
        │ ready_to_merge + CI green + mergeable
        ▼
     merged  ──►  comment on the issue; the issue stays OPEN
```

Two kinds of Devin session are used per issue: one long-lived *implementation*
session (reused for clarification replies and review fixes, so it keeps its
context) and a fresh, throwaway *review* session per review round, so the
reviewer is not biased by having written the code.

Devin returns its decision through
[structured output](https://docs.devin.ai), not free-form text — see
`IMPLEMENTATION_OUTPUT_SCHEMA` and `REVIEW_OUTPUT_SCHEMA` in `models.py`.

### Why the issue stays open

The implementation prompt forbids closing keywords (`Fixes #123`) in the PR
body, so GitHub does not auto-close the issue on merge. The service posts a
comment pointing at the merged PR and leaves the issue for the reporter to
close.

## Configuration

Copy `.env.example` to `.env` (git-ignored) and fill it in. Every setting is an
environment variable prefixed with `DEVIN_AUTOMATION_`; see `config.py`.

The GitHub token needs `repo` scope (issues: read/write, pull requests:
read/write, contents: read/write for merging). Use a dedicated bot account or a
GitHub App installation token — the service ignores comments authored by its
own token identity, which is what keeps it from replying to itself.

**Start with `DEVIN_AUTOMATION_DRY_RUN=true`.** In dry-run mode the poller does
every read and logs every decision but creates no sessions, comments or merges.

## Endpoints

| Method | Path             | Purpose                                       |
| ------ | ---------------- | --------------------------------------------- |
| GET    | `/health`        | Liveness plus whether credentials are present  |
| GET    | `/status`        | Last cycle report and every tracked issue      |
| GET    | `/issues/{n}`    | State of a single tracked issue                |
| POST   | `/poll`          | Run a cycle immediately                        |
| POST   | `/pause`         | Stop acting (reads and state are preserved)    |
| POST   | `/resume`        | Resume                                         |

```bash
docker compose up -d devin-automation
curl -s localhost:8200/health | jq
curl -s -XPOST localhost:8200/poll | jq
```

## Running locally without Docker

```bash
pip install -r devin_automation/requirements.txt
DEVIN_AUTOMATION_STATE_DB_PATH=/tmp/devin_automation.sqlite \
  uvicorn devin_automation.main:app --port 8200 --reload
```

## Safety properties

- **Bounded loops.** `max_clarification_rounds` and `max_review_rounds` cap how
  many times an issue can bounce; exhausting either posts a hand-off comment and
  moves the issue to `abandoned`.
- **Bounded concurrency.** `max_concurrent_sessions` limits how many issues can
  have a live session at once.
- **Restart-safe.** All cursors and per-issue state live in SQLite on a named
  volume, so a restart does not re-trigger Devin on every open issue.
- **No self-replies.** Comments from the bot identity are filtered out of both
  discovery and the prompts.
- **Merges are gated** on the review verdict, `mergeable`, and (by default) a
  green combined check status.

## Known limitations

- Polling, not webhooks. One `GET /issues?since=` per minute is well inside the
  5000 req/h REST budget, but latency is up to a minute and comments deleted
  between polls are missed. A webhook endpoint can be added to this same app
  later; the state machine does not change.
- Single writer. The SQLite store assumes exactly one replica of this container.
- The automation can modify its own source, since it lives in the repository it
  edits. Consider an `issue_label_filter` and `CODEOWNERS` on
  `devin_automation/` to keep it from merging changes to itself unreviewed.
