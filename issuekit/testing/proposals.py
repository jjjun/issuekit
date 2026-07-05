"""Proposal and negotiation-thread fake client surface."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from issuekit.core import _drop_none
from issuekit.negotiation.model import _validate_contract as validate_negotiation_contract
from issuekit.workflow import WorkflowError


JsonDict = dict[str, Any]


class FakeProposalSurface:
    def create_proposal(
        self,
        *,
        origin: str,
        title: str,
        body: str,
        reply_to: str | None = None,
        blocking: bool | None = None,
        priority: str | None = None,
        depends_on: list[str] | tuple[str, ...] | str | None = None,
        thread_id: int | None = None,
        side: str | None = None,
        verdict: str | None = None,
        contract: str | None = None,
        target_worker: str | None = None,
    ) -> JsonDict:
        self._validate_contract(contract)
        request = _drop_none(
            {
                "origin": origin,
                "title": title,
                "body": body,
                "reply_to": reply_to,
                "blocking": blocking,
                "priority": priority,
                "depends_on": depends_on,
                "thread_id": thread_id,
                "side": side,
                "verdict": verdict,
                "contract": contract,
                "target_worker": target_worker,
            }
        )
        with self._lock:
            self._record("create_proposal", body=deepcopy(request))
            has_thread_fields = any(
                value is not None for value in (thread_id, side, verdict, contract)
            )
            if thread_id is None:
                # mine-py (082f0220) returns the existing pending proposal for a
                # duplicate origin on both plain and negotiation creates.
                for proposal in sorted(self._proposals.values(), key=lambda item: int(item["id"])):
                    if proposal.get("origin") == origin and proposal.get("status") == "pending":
                        return deepcopy(proposal)
            if has_thread_fields and thread_id is None:
                request["thread_id"] = self._allocate_thread()["id"]
            elif thread_id is not None:
                self._ensure_thread_is_negotiating(thread_id)
                self._ensure_unique_thread_origin(thread_id, origin)
            return deepcopy(self._store_proposal(request, allocate=True))

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
                    return deepcopy(check)
            return deepcopy(
                self._store_proposal_check(
                    {
                        "target_project": target_project,
                        "proposal_id": int(proposal_id),
                        "target_worker": target_worker,
                    },
                    allocate=True,
                )
            )

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
                body=_drop_none(
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

    def list_proposals(
        self,
        *,
        status: str | None = None,
        thread_id: int | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        proposals: list[JsonDict] = []
        offset = 0
        while True:
            page = self.list_proposals_page(
                status=status,
                thread_id=thread_id,
                limit=min(page_size, 500),
                offset=offset,
            )
            proposals.extend(page["items"])
            if page["offset"] + len(page["items"]) >= page["total"] or len(page["items"]) < page["limit"]:
                return deepcopy(proposals)
            offset = page["offset"] + page["limit"]

    def list_proposals_page(
        self,
        *,
        status: str | None = None,
        thread_id: int | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> JsonDict:
        with self._lock:
            filtered = [
                proposal
                for proposal in sorted(self._proposals.values(), key=lambda item: int(item["id"]))
                if (status or "pending") == proposal.get("status")
                and (thread_id is None or proposal.get("thread_id") == thread_id)
            ]
            items = deepcopy(filtered[offset : offset + limit])
            return {
                "items": items,
                "total": len(filtered),
                "limit": limit,
                "offset": offset,
            }

    def reply_proposal(
        self,
        proposal_id: int,
        *,
        origin: str,
        title: str,
        body: str,
        side: str,
        verdict: str,
        contract: str | None = None,
        priority: str | None = None,
    ) -> JsonDict:
        self._validate_contract(contract)
        request = _drop_none(
            {
                "origin": origin,
                "title": title,
                "body": body,
                "side": side,
                "verdict": verdict,
                "contract": contract,
                "priority": priority,
            }
        )
        with self._lock:
            self._record("reply_proposal", number=proposal_id, body=deepcopy(request))
            parent = self._find_proposal(proposal_id)
            thread_id = parent.get("thread_id")
            if thread_id is None:
                thread_id = self._allocate_thread()["id"]
                parent["thread_id"] = thread_id
                self._update_thread_timestamp(thread_id)
            thread_id = int(thread_id)
            self._ensure_thread_is_negotiating(thread_id)
            self._ensure_unique_thread_origin(thread_id, origin)
            request["thread_id"] = thread_id
            request["reply_to"] = str(proposal_id)
            return deepcopy(self._store_proposal(request, allocate=True))

    def get_thread(self, thread_id: int) -> JsonDict:
        with self._lock:
            thread = deepcopy(self._find_thread(thread_id))
            items = [
                proposal
                for proposal in sorted(self._proposals.values(), key=lambda item: int(item["id"]))
                if proposal.get("thread_id") == thread_id
            ]
            thread["items"] = deepcopy(items)
            thread["total"] = len(items)
            thread["limit"] = len(items)
            thread["offset"] = 0
            return thread

    def list_threads(
        self,
        *,
        status: str | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        threads: list[JsonDict] = []
        offset = 0
        while True:
            page = self.list_threads_page(status=status, limit=min(page_size, 500), offset=offset)
            threads.extend(page["items"])
            if page["offset"] + len(page["items"]) >= page["total"] or len(page["items"]) < page["limit"]:
                return deepcopy(threads)
            offset = page["offset"] + page["limit"]

    def list_threads_page(
        self,
        *,
        status: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> JsonDict:
        with self._lock:
            filtered = [
                thread
                for thread in sorted(self._threads.values(), key=lambda item: int(item["id"]))
                if status is None or thread.get("status") == status
            ]
            items = deepcopy(filtered[offset : offset + limit])
            return {
                "items": items,
                "total": len(filtered),
                "limit": limit,
                "offset": offset,
            }

    def patch_thread(
        self,
        thread_id: int,
        *,
        status: str | None = None,
        agreed_contract: str | None = None,
        backend_issue_ref: str | None = None,
        frontend_issue_ref: str | None = None,
    ) -> JsonDict:
        self._validate_contract(agreed_contract)
        with self._lock:
            self._record(
                "patch_thread",
                number=thread_id,
                body=_drop_none(
                    {
                        "status": status,
                        "agreed_contract": agreed_contract,
                        "backend_issue_ref": backend_issue_ref,
                        "frontend_issue_ref": frontend_issue_ref,
                    }
                ),
            )
            thread = self._find_thread(thread_id)
            if status is not None:
                if thread.get("status") != "negotiating":
                    raise WorkflowError(
                        f"Negotiation thread {thread_id} is already {thread.get('status')}.",
                        code="invalid_transition",
                    )
                if status not in {"agreed", "blocked"}:
                    raise WorkflowError(
                        f"Invalid thread status transition: {status}.",
                        code="invalid_transition",
                    )
                if agreed_contract is not None and status != "agreed":
                    raise WorkflowError(
                        "agreed_contract can only be set when status is agreed.",
                        code="invalid_transition",
                    )
                thread["status"] = status
                if status == "agreed":
                    thread["agreed_contract"] = agreed_contract or self._latest_agree_contract(thread_id)
            elif agreed_contract is not None:
                raise WorkflowError(
                    "agreed_contract can only be set with a status transition.",
                    code="invalid_transition",
                )
            if backend_issue_ref is not None:
                thread["backend_issue_ref"] = backend_issue_ref
            if frontend_issue_ref is not None:
                thread["frontend_issue_ref"] = frontend_issue_ref
            thread["updated_at"] = date.today().isoformat()
            return deepcopy(thread)

    def get_proposal(self, proposal_id: int) -> JsonDict:
        with self._lock:
            return deepcopy(self._find_proposal(proposal_id))

    def adopt_proposal(self, proposal_id: int, *, priority: str | None = None) -> JsonDict:
        with self._lock:
            self._record(
                "adopt_proposal",
                number=proposal_id,
                body=_drop_none({"priority": priority}),
            )
            proposal = self._find_proposal(proposal_id)
            if proposal.get("status") != "pending":
                raise WorkflowError(
                    f"Proposal #{proposal_id} has already been {proposal.get('status')}.",
                    code="invalid_transition",
                )
            issue = self._store_issue(
                {
                    "title": proposal["title"],
                    "body": proposal["body"],
                    "priority": priority or proposal.get("priority") or "medium",
                    "author": "proposal",
                    "origin": proposal["origin"],
                    "origin_proposal_id": str(proposal_id),
                },
                allocate=True,
            )
            proposal["status"] = "adopted"
            proposal["adopted_issue_number"] = issue["id"]
            proposal["updated"] = date.today().isoformat()
            return deepcopy(issue)

    def discard_proposal(self, proposal_id: int) -> JsonDict:
        with self._lock:
            self._record("discard_proposal", number=proposal_id)
            proposal = self._find_proposal(proposal_id)
            if proposal.get("status") != "pending":
                raise WorkflowError(
                    f"Proposal #{proposal_id} has already been {proposal.get('status')}.",
                    code="invalid_transition",
                )
            proposal["status"] = "discarded"
            proposal["updated"] = date.today().isoformat()
            return deepcopy(proposal)

    def import_proposals(self, proposals: list[JsonDict] | JsonDict) -> list[JsonDict]:
        raw_proposals = proposals.get("proposals", []) if isinstance(proposals, dict) else proposals
        if not isinstance(raw_proposals, list):
            raise WorkflowError("Proposal import payload must be a list of proposals.", code="invalid_value")
        with self._lock:
            self._record("import_proposals", body={"proposals": deepcopy(raw_proposals)})
            imported: list[JsonDict] = []
            for proposal in raw_proposals:
                existing = self._find_imported_proposal(proposal)
                if existing is None:
                    existing = self._store_proposal(proposal, allocate=True)
                else:
                    existing.update(deepcopy(proposal))
                imported.append(existing)
            return deepcopy(imported)

    def _find_proposal(self, proposal_id: int) -> JsonDict:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise WorkflowError(f"Proposal #{proposal_id} was not found.", code="not_found")
        return proposal

    def _find_proposal_check(self, check_id: int) -> JsonDict:
        check = self._proposal_checks.get(check_id)
        if check is None:
            raise WorkflowError(f"Proposal check {check_id} was not found.", code="not_found")
        return check

    def _find_thread(self, thread_id: int) -> JsonDict:
        thread = self._threads.get(thread_id)
        if thread is None:
            raise WorkflowError(f"Proposal thread {thread_id} was not found.", code="not_found")
        return thread

    def _find_imported_proposal(self, proposal: JsonDict) -> JsonDict | None:
        origin = proposal.get("origin")
        status = proposal.get("status")
        for stored in sorted(self._proposals.values(), key=lambda item: int(item["id"])):
            if stored.get("origin") == origin and stored.get("status") == status:
                return stored
        return None

    def _store_proposal(self, proposal: JsonDict, *, allocate: bool = False) -> JsonDict:
        stored = deepcopy(proposal)
        raw_id = stored.get("id")
        if allocate or raw_id is None:
            proposal_id = self._next_proposal_id
            self._next_proposal_id += 1
        else:
            proposal_id = int(raw_id)
            self._next_proposal_id = max(self._next_proposal_id, proposal_id + 1)
        stored["id"] = proposal_id
        stored.setdefault("target_project", "issuekit")
        stored.setdefault("origin", f"source#0@{proposal_id}")
        stored.setdefault("reply_to", None)
        stored.setdefault("title", f"Proposal #{proposal_id}")
        stored.setdefault("body", "")
        stored.setdefault("priority", None)
        stored.setdefault("blocking", False)
        stored.setdefault("depends_on", None)
        stored.setdefault("target_worker", "")
        stored.setdefault("status", "pending")
        stored.setdefault("created", date.today().isoformat())
        stored.setdefault("created_at", stored["created"])
        stored.setdefault("adopted_issue_number", None)
        stored.setdefault("updated", stored["created"])
        stored.setdefault("updated_at", stored["updated"])
        stored.setdefault("thread_id", None)
        stored.setdefault("side", None)
        stored.setdefault("verdict", None)
        stored.setdefault("contract", None)
        self._proposals[proposal_id] = stored
        if stored.get("thread_id") is not None:
            thread_id = int(stored["thread_id"])
            if thread_id not in self._threads:
                self._store_thread({"id": thread_id}, allocate=False)
            self._update_thread_timestamp(thread_id)
        return stored

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

    def _store_thread(self, thread: JsonDict, *, allocate: bool = False) -> JsonDict:
        stored = deepcopy(thread)
        raw_id = stored.get("id")
        if allocate or raw_id is None:
            thread_id = self._next_thread_id
            self._next_thread_id += 1
        else:
            thread_id = int(raw_id)
            self._next_thread_id = max(self._next_thread_id, thread_id + 1)
        stored["id"] = thread_id
        stored.setdefault("target_project", "issuekit")
        stored.setdefault("status", "negotiating")
        stored.setdefault("agreed_contract", None)
        stored.setdefault("backend_issue_ref", None)
        stored.setdefault("frontend_issue_ref", None)
        stored.setdefault("created_at", date.today().isoformat())
        stored.setdefault("updated_at", stored["created_at"])
        self._threads[thread_id] = stored
        return stored

    def _allocate_thread(self) -> JsonDict:
        return self._store_thread({}, allocate=True)

    def _update_thread_timestamp(self, thread_id: int) -> None:
        self._threads[thread_id]["updated_at"] = date.today().isoformat()

    def _ensure_thread_is_negotiating(self, thread_id: int) -> None:
        thread = self._find_thread(thread_id)
        if thread.get("status") != "negotiating":
            raise WorkflowError(
                f"Negotiation thread {thread_id} is {thread.get('status')} and cannot be modified.",
                code="invalid_transition",
            )

    def _ensure_unique_thread_origin(self, thread_id: int, origin: str) -> None:
        if any(
            proposal.get("thread_id") == thread_id and proposal.get("origin") == origin
            for proposal in self._proposals.values()
        ):
            raise WorkflowError(
                f"Negotiation thread {thread_id} already has origin {origin}.",
                code="duplicate_origin",
            )

    def _latest_agree_contract(self, thread_id: int) -> str | None:
        for proposal in sorted(self._proposals.values(), key=lambda item: int(item["id"]), reverse=True):
            if (
                proposal.get("thread_id") == thread_id
                and proposal.get("verdict") == "agree"
                and proposal.get("contract") is not None
            ):
                return str(proposal.get("contract"))
        return None

    def _validate_contract(self, contract: str | None) -> None:
        validate_negotiation_contract(contract)
