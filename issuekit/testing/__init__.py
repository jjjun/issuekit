"""Reusable test doubles for issuekit integrations."""

from __future__ import annotations

from copy import deepcopy
from threading import Lock
from typing import Any

from issuekit.testing.issues import FakeIssueSurface
from issuekit.testing.proposals import FakeProposalSurface


JsonDict = dict[str, Any]


class FakeIssuekitClient(FakeIssueSurface, FakeProposalSurface):
    """In-memory implementation of the IssuekitClient method surface."""

    def __init__(
        self,
        issues: list[JsonDict] | None = None,
        proposals: list[JsonDict] | None = None,
    ) -> None:
        self._lock = Lock()
        self._issues: dict[int, JsonDict] = {}
        self._proposals: dict[int, JsonDict] = {}
        self._threads: dict[int, JsonDict] = {}
        self._next_id = 1
        self._next_proposal_id = 1
        self._next_thread_id = 1
        self.calls: list[JsonDict] = []
        self.close_count = 0
        for issue in issues or []:
            self._store_issue(issue)
        for proposal in proposals or []:
            self._store_proposal(proposal)

    def __enter__(self) -> "FakeIssuekitClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
        return None

    def close(self) -> None:
        self.close_count += 1

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
