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
        self._workers: dict[str, JsonDict] = {}
        self._proposals: dict[int, JsonDict] = {}
        self._threads: dict[int, JsonDict] = {}
        self._proposal_checks: dict[int, JsonDict] = {}
        self._profiles: dict[str, JsonDict] = {}
        self._next_id = 1
        self._next_proposal_id = 1
        self._next_thread_id = 1
        self._next_proposal_check_id = 1
        self.calls: list[JsonDict] = []
        self.close_count = 0
        # Real IssuekitClient carries the target project; the fake defaults to the
        # canonical project and lets tests override it for profile routing.
        self.project = "issuekit"
        for issue in issues or []:
            self._store_issue(issue)
        for proposal in proposals or []:
            self._store_proposal(proposal)

    def put_project_profile(
        self,
        *,
        summary: str | None = None,
        profile_md: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        source_commit: str | None = None,
        source_committed_at: str | None = None,
    ) -> JsonDict:
        with self._lock:
            body = {
                "project": self.project,
                "summary": summary,
                "profile_md": profile_md,
                "tags": list(tags) if tags is not None else None,
                "source_commit": source_commit,
                "source_committed_at": source_committed_at,
            }
            self._record("put_project_profile", body=deepcopy(body))
            self._profiles[self.project] = deepcopy(body)
            return deepcopy(body)

    def get_project_profile(self, project: str | None = None) -> JsonDict:
        target = project or self.project
        with self._lock:
            profile = self._profiles.get(target)
            if profile is None:
                from issuekit.workflow import WorkflowError

                raise WorkflowError(
                    f"Project profile for {target} was not found.", code="http_404"
                )
            return deepcopy(profile)

    def list_project_profiles(self) -> list[JsonDict]:
        with self._lock:
            return [deepcopy(profile) for _, profile in sorted(self._profiles.items())]

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
