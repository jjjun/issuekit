"""Proposal-check fake client surface."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from issuekit.core import drop_none
from issuekit.errors import WorkflowError

JsonDict = dict[str, Any]


class FakeProposalCheckSurface:
    def create_proposal_check(
        self,
        proposal_id: int,
        *,
        target_worker: str,
        project: str | None = None,
    ) -> JsonDict:
        with self._lock:
            body = {"target_worker": target_worker, "project": project}
            self._record("create_proposal_check", number=proposal_id, body=body)
            proposal = self._find_proposal(proposal_id)
            if proposal.get("status") != "pending":
                raise WorkflowError(
                    f"Proposal #{proposal_id} is {proposal.get('status')}.",
                    code="invalid_state",
                )
            target_project = project or self.project
            for check in sorted(self._proposal_checks.values(), key=lambda item: int(item["id"])):
                if (
                    int(check.get("proposal_id", 0)) == int(proposal_id)
                    and check.get("target_project") == target_project
                    and check.get("target_worker") == target_worker
                    and check.get("status") == "pending"
                ):
                    existing = deepcopy(check)
                    existing["was_created"] = False
                    return existing
            created = deepcopy(
                self._store_proposal_check(
                    {
                        "target_project": target_project,
                        "proposal_id": int(proposal_id),
                        "target_worker": target_worker,
                    },
                    allocate=True,
                )
            )
            created["was_created"] = True
            return created

    def list_proposal_checks(
        self,
        *,
        target_worker: str,
        status: str | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        with self._lock:
            self._record(
                "list_proposal_checks",
                body={"target_worker": target_worker, "status": status, "page_size": page_size},
            )
            checks = [
                check
                for check in sorted(self._proposal_checks.values(), key=lambda item: int(item["id"]))
                if check.get("target_worker") == target_worker
                and (status is None or check.get("status") == status)
            ]
            return deepcopy(checks)

    def list_proposal_checks_for_proposal(
        self,
        proposal_id: int,
        *,
        project: str | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        with self._lock:
            target_project = project
            self._record(
                "list_proposal_checks_for_proposal",
                number=proposal_id,
                body={"project": project or self.project, "page_size": page_size},
            )
            checks = [
                check
                for check in sorted(
                    self._proposal_checks.values(), key=lambda item: int(item["id"])
                )
                if int(check.get("proposal_id", 0)) == int(proposal_id)
                and (target_project is None or check.get("target_project") == target_project)
            ]
            return deepcopy(checks)

    def poll_proposal_checks(
        self,
        *,
        target_worker: str,
        status: str = "pending",
        limit: int = 50,
        offset: int = 0,
    ) -> list[JsonDict]:
        with self._lock:
            self._record(
                "poll_proposal_checks",
                body={
                    "target_worker": target_worker,
                    "status": status,
                    "limit": limit,
                    "offset": offset,
                },
            )
            checks = [
                check
                for check in sorted(self._proposal_checks.values(), key=lambda item: int(item["id"]))
                if check.get("target_worker") == target_worker
                and (status is None or check.get("status") == status)
            ]
            return deepcopy(checks[offset : offset + limit])

    def post_proposal_check_result(
        self,
        check_id: int,
        *,
        project: str,
        verdict: str,
        comment: str | None = None,
        adopted_issue_ref: str | None = None,
    ) -> JsonDict:
        with self._lock:
            self._record(
                "post_proposal_check_result",
                number=check_id,
                body=drop_none(
                    {
                        "project": project,
                        "verdict": verdict,
                        "comment": comment,
                        "adopted_issue_ref": adopted_issue_ref,
                    }
                ),
            )
            check = self._find_proposal_check(check_id)
            if check.get("target_project") != project:
                raise WorkflowError(
                    f"Proposal check {check_id} does not belong to {project}.",
                    code="not_found",
                )
            if check.get("status") == "answered":
                raise WorkflowError(
                    f"Proposal check {check_id} is already answered.",
                    code="already_decided",
                )
            check["status"] = "answered"
            check["verdict"] = verdict
            check["comment"] = comment
            check["adopted_issue_ref"] = adopted_issue_ref
            check["answered_at"] = date.today().isoformat()
            check["updated_at"] = check["answered_at"]
            return deepcopy(check)

    def _find_proposal_check(self, check_id: int) -> JsonDict:
        check = self._proposal_checks.get(check_id)
        if check is None:
            raise WorkflowError(f"Proposal check {check_id} was not found.", code="not_found")
        return check

    def _store_proposal_check(self, check: JsonDict, *, allocate: bool = False) -> JsonDict:
        stored = deepcopy(check)
        raw_id = stored.get("id")
        if allocate or raw_id is None:
            check_id = self._next_proposal_check_id
            self._next_proposal_check_id += 1
        else:
            check_id = int(raw_id)
            self._next_proposal_check_id = max(self._next_proposal_check_id, check_id + 1)
        stored["id"] = check_id
        stored.setdefault("target_project", self.project)
        stored.setdefault("proposal_id", 1)
        stored.setdefault("target_worker", "")
        stored.setdefault("status", "pending")
        stored.setdefault("verdict", None)
        stored.setdefault("comment", None)
        stored.setdefault("adopted_issue_ref", None)
        stored.setdefault("answered_at", None)
        stored.setdefault("created_at", date.today().isoformat())
        stored.setdefault("updated_at", stored["created_at"])
        self._proposal_checks[check_id] = stored
        return stored
