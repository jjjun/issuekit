"""Reusable test doubles for issuekit integrations."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from threading import Lock
from typing import Any

from issuekit.workflow import WorkflowError


JsonDict = dict[str, Any]
READY_STAGES = {"", "todo", "changes_requested"}
CLAIMABLE_STATUSES = {"active", "in_progress"}
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


class FakeIssuekitClient:
    """In-memory implementation of the IssuekitClient method surface."""

    def __init__(
        self,
        issues: list[JsonDict] | None = None,
        proposals: list[JsonDict] | None = None,
    ) -> None:
        self._lock = Lock()
        self._issues: dict[int, JsonDict] = {}
        self._proposals: dict[int, JsonDict] = {}
        self._next_id = 1
        self._next_proposal_id = 1
        self.calls: list[JsonDict] = []
        for issue in issues or []:
            self._store_issue(issue)
        for proposal in proposals or []:
            self._store_proposal(proposal)

    def __enter__(self) -> "FakeIssuekitClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def list_issues(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        assignee: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[JsonDict]:
        with self._lock:
            issues = [
                issue
                for issue in sorted(self._issues.values(), key=lambda item: int(item["id"]))
                if (
                    (status is None and issue.get("status") != "completed")
                    or (status is not None and issue.get("status") == status)
                )
                and (stage is None or issue.get("stage") == stage)
                and (assignee is None or issue.get("assignee") == assignee)
            ]
            start = offset or 0
            stop = start + (limit if limit is not None else 100)
            issues = issues[start:stop]
            return deepcopy(issues)

    def list_all_issues(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        assignee: str | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        page_size = min(page_size, 500)
        offset = 0
        issues: list[JsonDict] = []
        while True:
            batch = self.list_issues(
                status=status,
                stage=stage,
                assignee=assignee,
                limit=page_size,
                offset=offset,
            )
            issues.extend(batch)
            if len(batch) < page_size:
                return issues
            offset += page_size

    def get_issue(self, number: int) -> JsonDict:
        with self._lock:
            return deepcopy(self._find(number))

    def create_issue(self, issue: JsonDict) -> JsonDict:
        with self._lock:
            self._record("create_issue", body=deepcopy(issue))
            return deepcopy(self._store_issue(issue, allocate=True))

    def claim(self, number: int, *, assignee: str, worker: str | None = None) -> JsonDict:
        with self._lock:
            self._record(
                "claim",
                number=number,
                body={
                    key: value
                    for key, value in {"assignee": assignee, "worker": worker}.items()
                    if value is not None
                },
            )
            issue = self._find(number)
            self._claim_issue(issue, assignee, worker=worker)
            return deepcopy(issue)

    def claim_next(
        self,
        *,
        assignee: str,
        priority: str | None = None,
        worker: str | None = None,
    ) -> JsonDict | None:
        with self._lock:
            self._record(
                "claim_next",
                body={
                    key: value
                    for key, value in {
                        "assignee": assignee,
                        "priority": priority,
                        "worker": worker,
                    }.items()
                    if value is not None
                },
            )
            candidates = [
                issue
                for issue in self._issues.values()
                if issue.get("status") in CLAIMABLE_STATUSES
                and issue.get("stage", "") in READY_STAGES
                and issue.get("assignee", "") in {"", assignee}
                and (priority is None or issue.get("priority") == priority)
            ]
            if not candidates:
                return None
            issue = sorted(
                candidates,
                key=lambda item: (PRIORITY_RANK.get(str(item.get("priority", "")), 99), int(item["id"])),
            )[0]
            self._claim_issue(issue, assignee, worker=worker)
            return deepcopy(issue)

    def submit(
        self,
        number: int,
        *,
        summary: str,
        branch: str | None = None,
        commit: str | None = None,
        reviewer: str | None = None,
    ) -> JsonDict:
        with self._lock:
            self._record(
                "submit",
                number=number,
                body={
                    key: value
                    for key, value in {
                        "summary": summary,
                        "branch": branch,
                        "commit": commit,
                        "reviewer": reviewer,
                    }.items()
                    if value is not None
                },
            )
            issue = self._find(number)
            if reviewer and issue.get("implementer") == reviewer:
                raise WorkflowError(
                    f"Issue #{number} was implemented by {reviewer}; self-review is not allowed.",
                    code="invalid_transition",
                )
            issue["stage"] = "review"
            issue["assignee"] = reviewer or ""
            return deepcopy(issue)

    def request_changes(
        self,
        number: int,
        *,
        notes: str,
        reviewer: str | None = None,
        assignee: str | None = None,
    ) -> JsonDict:
        with self._lock:
            self._record(
                "request_changes",
                number=number,
                body={
                    key: value
                    for key, value in {
                        "notes": notes,
                        "reviewer": reviewer,
                        "assignee": assignee,
                    }.items()
                    if value is not None
                },
            )
            issue = self._find(number)
            issue["stage"] = "changes_requested"
            issue["assignee"] = assignee or issue.get("implementer") or "codex"
            return deepcopy(issue)

    def approve(
        self,
        number: int,
        *,
        summary: str,
        verification: str,
        reviewer: str,
    ) -> JsonDict:
        with self._lock:
            self._record(
                "approve",
                number=number,
                body={
                    "summary": summary,
                    "verification": verification,
                    "reviewer": reviewer,
                },
            )
            issue = self._find(number)
            if issue.get("implementer") == reviewer:
                raise WorkflowError(
                    f"Issue #{number} was implemented by {reviewer}; self-review is not allowed.",
                    code="invalid_transition",
                )
            issue["status"] = "completed"
            issue["stage"] = "done"
            issue["assignee"] = ""
            return deepcopy(issue)

    def complete(self, number: int, *, summary: str, verification: str, force: bool = False) -> JsonDict:
        with self._lock:
            self._record(
                "complete",
                number=number,
                body={"summary": summary, "verification": verification, "force": force},
            )
            issue = self._find(number)
            issue["status"] = "completed"
            issue["stage"] = "done"
            issue["assignee"] = ""
            return deepcopy(issue)

    def import_issues(self, issues: list[JsonDict] | JsonDict) -> JsonDict | list[JsonDict]:
        raw_issues = issues.get("issues", []) if isinstance(issues, dict) else issues
        if not isinstance(raw_issues, list):
            raise WorkflowError("Import payload must be a list of issues.", code="invalid_value")
        with self._lock:
            self._record("import_issues", body={"issues": deepcopy(raw_issues)})
            imported = [self._store_issue(issue) for issue in raw_issues]
            return deepcopy(imported)

    def upsert_worker(
        self,
        *,
        machine_id: str,
        repo_id: str,
        worker_id: str,
        path: str | None,
    ) -> JsonDict:
        body = {
            "machine_id": machine_id,
            "repo_id": repo_id,
            "worker_id": worker_id,
            "path": path,
        }
        with self._lock:
            self._record("upsert_worker", body=body)
            return {
                "id": f"{machine_id}/{repo_id}/{worker_id}",
                **body,
                "status": "idle",
                "current_issue": None,
                "last_seen": "2026-01-01T00:00:00Z",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }

    def create_proposal(
        self,
        *,
        origin: str,
        title: str,
        body: str,
        reply_to: str | None = None,
        priority: str | None = None,
    ) -> JsonDict:
        request = {
            key: value
            for key, value in {
                "origin": origin,
                "title": title,
                "body": body,
                "reply_to": reply_to,
                "priority": priority,
            }.items()
            if value is not None
        }
        with self._lock:
            self._record("create_proposal", body=deepcopy(request))
            for proposal in sorted(self._proposals.values(), key=lambda item: int(item["id"])):
                if proposal.get("origin") == origin and proposal.get("status") == "pending":
                    return deepcopy(proposal)
            return deepcopy(self._store_proposal(request, allocate=True))

    def list_proposals(
        self,
        *,
        status: str | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        proposals: list[JsonDict] = []
        offset = 0
        while True:
            page = self.list_proposals_page(status=status, limit=min(page_size, 500), offset=offset)
            proposals.extend(page["items"])
            if page["offset"] + len(page["items"]) >= page["total"] or len(page["items"]) < page["limit"]:
                return deepcopy(proposals)
            offset = page["offset"] + page["limit"]

    def list_proposals_page(
        self,
        *,
        status: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> JsonDict:
        with self._lock:
            filtered = [
                proposal
                for proposal in sorted(self._proposals.values(), key=lambda item: int(item["id"]))
                if (status or "pending") == proposal.get("status")
            ]
            items = deepcopy(filtered[offset : offset + limit])
            return {
                "items": items,
                "total": len(filtered),
                "limit": limit,
                "offset": offset,
            }

    def get_proposal(self, proposal_id: int) -> JsonDict:
        with self._lock:
            return deepcopy(self._find_proposal(proposal_id))

    def adopt_proposal(self, proposal_id: int, *, priority: str | None = None) -> JsonDict:
        with self._lock:
            self._record(
                "adopt_proposal",
                number=proposal_id,
                body={key: value for key, value in {"priority": priority}.items() if value is not None},
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

    def _find(self, number: int) -> JsonDict:
        issue = self._issues.get(number)
        if issue is None:
            raise WorkflowError(f"Issue #{number} was not found.", code="not_found")
        return issue

    def _find_proposal(self, proposal_id: int) -> JsonDict:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise WorkflowError(f"Proposal #{proposal_id} was not found.", code="not_found")
        return proposal

    def _find_imported_proposal(self, proposal: JsonDict) -> JsonDict | None:
        origin = proposal.get("origin")
        status = proposal.get("status")
        for stored in sorted(self._proposals.values(), key=lambda item: int(item["id"])):
            if stored.get("origin") == origin and stored.get("status") == status:
                return stored
        return None

    def _store_issue(self, issue: JsonDict, *, allocate: bool = False) -> JsonDict:
        stored = deepcopy(issue)
        raw_id = stored.get("id", stored.get("number"))
        if allocate or raw_id is None:
            issue_id = self._next_id
            self._next_id += 1
        else:
            issue_id = int(raw_id)
            self._next_id = max(self._next_id, issue_id + 1)
        stored["id"] = issue_id
        stored.pop("number", None)
        stored.setdefault("title", f"Issue #{issue_id}")
        stored.setdefault("status", "active")
        stored.setdefault("priority", "medium")
        stored.setdefault("created", "2026-01-01")
        stored.setdefault("completed", "")
        stored.setdefault("assignee", "")
        stored.setdefault("stage", "todo")
        stored.setdefault("implementer", "")
        stored.setdefault("author", "")
        stored.setdefault("worker", "")
        stored.setdefault("body", "")
        self._issues[issue_id] = stored
        return stored

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
        stored.setdefault("status", "pending")
        stored.setdefault("created", date.today().isoformat())
        stored.setdefault("adopted_issue_number", None)
        stored.setdefault("updated", stored["created"])
        self._proposals[proposal_id] = stored
        return stored

    def _claim_issue(self, issue: JsonDict, assignee: str, *, worker: str | None = None) -> None:
        issue_id = issue["id"]
        if issue.get("status") not in CLAIMABLE_STATUSES:
            raise WorkflowError(
                f"Issue #{issue_id} has status {issue.get('status')}; "
                "only active or in_progress issues can be implemented.",
                code="invalid_transition",
            )
        if issue.get("stage", "") not in READY_STAGES | {"implementing"}:
            raise WorkflowError(
                f"Issue #{issue_id} is at stage {issue.get('stage') or 'todo'}, not ready for implementation.",
                code="invalid_transition",
            )
        if issue.get("assignee", "") not in {"", assignee}:
            raise WorkflowError(
                f"Issue #{issue_id} is assigned to {issue.get('assignee')}, not {assignee}.",
                code="invalid_transition",
            )
        if issue.get("author") == assignee:
            raise WorkflowError(
                f"Issue #{issue_id} was authored by {assignee}; self-implementation is not allowed.",
                code="invalid_transition",
            )
        issue["status"] = "in_progress"
        issue["assignee"] = assignee
        issue["stage"] = "implementing"
        issue["implementer"] = assignee
        if worker is not None:
            issue["worker"] = worker

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
