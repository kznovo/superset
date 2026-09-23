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
"""Prompt templates handed to Devin sessions."""

from __future__ import annotations

IMPLEMENTATION_PROMPT = """\
You are working on the GitHub repository `{repo}` (base branch `{base_branch}`).

Handle issue #{number}: {title}
Issue URL: {issue_url}
Reported by: @{author}

--- ISSUE BODY ---
{body}
--- END ISSUE BODY ---
{comments_section}
Instructions:
1. Read the repository's AGENTS.md / CONTRIBUTING.md and follow its conventions,
   including running pre-commit on the files you change.
2. Decide whether the issue is actionable as written. It is NOT actionable if the
   reproduction steps are missing or contradictory, the expected behaviour is
   ambiguous, or the request conflicts with how the codebase works. In that case
   do NOT open a pull request: return structured output with
   `outcome = "needs_clarification"` and a `question` containing the specific
   questions to ask the reporter, written as GitHub-flavoured markdown. Ask only
   questions you cannot answer yourself by reading the code.
3. If the issue is actionable, implement the smallest correct fix, add or update
   tests, and open a pull request against `{base_branch}`.
   Do NOT write "Fixes #{number}" or any other closing keyword in the PR body -
   the issue must stay open after the PR merges. Reference it as
   "Related to #{number}" instead.
4. Return structured output with `outcome = "pr_opened"` and `pr_url` set to the
   pull request URL, plus a one-paragraph `summary`.
5. If the issue turns out to be invalid or already fixed, return
   `outcome = "not_actionable"` with an explanation in `summary`.
"""

CLARIFICATION_REPLY_PROMPT = """\
The reporter replied on issue #{number}. New comments since your question:

{comments}

Re-evaluate with this new context. If you now have enough information, implement
the fix and open a pull request against `{base_branch}` (no closing keywords for
the issue), then return structured output with `outcome = "pr_opened"`.
If it is still ambiguous, return `outcome = "needs_clarification"` with the next
question.
"""

REVIEW_PROMPT = """\
Review pull request {pr_url} in `{repo}`.

It was opened to address issue #{issue_number}: {issue_title}
Issue URL: {issue_url}

Review it as a demanding maintainer of this repository:
- Does it actually solve the problem described in the issue?
- Is it correct, minimally scoped, and consistent with the surrounding code and
  the conventions in AGENTS.md?
- Are there missing tests, unhandled edge cases, security issues, or breaking
  changes that need a note in UPDATING.md?
- Is CI green?

Do NOT modify the pull request. Only read it and report.
Return structured output with `verdict = "ready_to_merge"` when you would approve
it as-is, or `verdict = "changes_needed"` with a `findings` array listing each
required change as a concrete, actionable instruction.
"""

FIX_PROMPT = """\
Review feedback on your pull request {pr_url} (round {round_number}):

{findings}

Address every point, push to the same branch, and make sure CI passes. Then
return structured output with `outcome = "pr_opened"` and the same `pr_url`.
"""

CLARIFICATION_COMMENT = """\
@{author} I'm looking into this automatically, but I need a bit more information
before I can propose a fix:

{question}

---
<sub>Posted by the Devin issue automation · [session]({session_url})</sub>
"""

PR_OPENED_COMMENT = """\
@{author} I opened {pr_url} for this issue. It will be reviewed automatically and
merged once it is ready; this issue stays open so you can confirm the fix.

---
<sub>Posted by the Devin issue automation · [session]({session_url})</sub>
"""

MERGED_COMMENT = """\
@{author} {pr_url} has been merged. Leaving this issue open so you can verify the
fix and close it yourself if it resolves the problem.

---
<sub>Posted by the Devin issue automation</sub>
"""

ABANDONED_COMMENT = """\
@{author} I wasn't able to resolve this automatically ({reason}). Handing it over
to a human maintainer.

---
<sub>Posted by the Devin issue automation</sub>
"""


def render_comments(comments: list[dict[str, object]]) -> str:
    """Render GitHub comments into a plain-text block for a prompt."""
    rendered = []
    for comment in comments:
        user = comment.get("user") or {}
        login = user.get("login", "unknown") if isinstance(user, dict) else "unknown"
        rendered.append(f"@{login} wrote:\n{comment.get('body', '')}")
    return "\n\n".join(rendered)
