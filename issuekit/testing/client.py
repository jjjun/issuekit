"""Reusable test doubles for issuekit integrations."""

from __future__ import annotations

from copy import deepcopy
from threading import Lock
from typing import Any

from issuekit.errors import WorkflowError
from issuekit.testing.issues import FakeIssueSurface
from issuekit.testing.profiles import FakeProfileSurface
from issuekit.testing.proposal_checks import FakeProposalCheckSurface
from issuekit.testing.proposals import FakeProposalSurface
from issuekit.testing.workers import FakeWorkerSurface

JsonDict = dict[str, Any]


class FakeIssuekitClient(
    FakeIssueSurface,
    FakeWorkerSurface,
    FakeProposalSurface,
    FakeProposalCheckSurface,
    FakeProfileSurface,
):
    """In-memory implementation of the IssuekitClient method surface."""

    def __init__(
        self,
        issues: list[JsonDict] | None = None,
        proposals: list[JsonDict] | None = None,
        *,
        stored_target_worker_override: str | None = None,
        adopt_not_found_attempts: int = 0,
        drop_adopted_issue_body_patch: bool = False,
        rendered_issue_suffixes: dict[int, str] | None = None,
    ) -> None:
        if adopt_not_found_attempts < 0:
            raise ValueError("adopt_not_found_attempts must not be negative")
        self._lock = Lock()
        self._issues: dict[int, JsonDict] = {}
        self._repos: dict[str, JsonDict] = {}
        self._workers: dict[str, JsonDict] = {}
        self._proposals: dict[int, JsonDict] = {}
        self._threads: dict[int, JsonDict] = {}
        self._proposal_checks: dict[int, JsonDict] = {}
        self._profiles: dict[str, JsonDict] = {}
        self._next_id = 1
        self._next_proposal_id = 1
        self._next_thread_id = 1
        self._next_proposal_check_id = 1
        self._adopt_not_found_attempts = adopt_not_found_attempts
        self._adopted_issue_not_found_attempts: dict[int, int] = {}
        self._drop_adopted_issue_body_patch = drop_adopted_issue_body_patch
        self._rendered_issue_suffixes = dict(rendered_issue_suffixes or {})
        self.calls: list[JsonDict] = []
        self.stored_target_worker_override = stored_target_worker_override
        # Real IssuekitClient carries the target project; the fake defaults to the
        # canonical project and lets tests override it for profile routing.
        self.project = "issuekit"
        for issue in issues or []:
            self._store_issue(issue)
        for proposal in proposals or []:
            self._store_proposal(proposal)

    def __enter__(self) -> FakeIssuekitClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
        return

    def close(self) -> None:
        pass

    def get_issue(self, number: int) -> JsonDict:
        with self._lock:
            remaining_attempts = self._adopted_issue_not_found_attempts.get(number, 0)
            if remaining_attempts:
                self._adopted_issue_not_found_attempts[number] = remaining_attempts - 1
                raise WorkflowError(f"Issue #{number} was not found.", code="not_found")
            issue = deepcopy(self._find(number))
            if number in self._rendered_issue_suffixes:
                issue["body"] = (
                    f"{issue.get('body', '')}{self._rendered_issue_suffixes[number]}"
                )
            return issue

    def get_issue_edit(self, number: int) -> JsonDict:
        with self._lock:
            self._record("get_issue_edit", number=number)
            return {"body": deepcopy(self._find(number).get("body", ""))}

    def update_issue(self, number: int, issue: JsonDict) -> JsonDict:
        with self._lock:
            self._record("update_issue", number=number, body=deepcopy(issue))
            update = deepcopy(issue)
            if self._drop_adopted_issue_body_patch and number in self._adopted_issue_not_found_attempts:
                update.pop("body", None)
            stored = self._find(number)
            stored.update(update)
            return deepcopy(stored)

    def adopt_proposal(self, proposal_id: int, *, priority: str | None = None) -> JsonDict:
        issue = super().adopt_proposal(proposal_id, priority=priority)
        with self._lock:
            self._adopted_issue_not_found_attempts[issue["id"]] = self._adopt_not_found_attempts
        return issue

    def health(self) -> JsonDict:
        return {"status": "ok", "migration_revision": "test"}

    def _record(
        self,
        method: str,
        *,
        number: int | None = None,
        body: JsonDict | None = None,
    ) -> None:
        call: JsonDict = {"method": method, "body": deepcopy(body or {})}
        if number is not None:
            call["number"] = number
        self.calls.append(call)


__all__ = ["FakeIssuekitClient", "JsonDict"]
