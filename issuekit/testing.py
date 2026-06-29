"""Reusable test doubles for issuekit integrations."""

from __future__ import annotations

from copy import deepcopy
from threading import Lock
from typing import Any

from issuekit.workflow import WorkflowError


JsonDict = dict[str, Any]
READY_STAGES = {"", "todo", "changes_requested"}
CLAIMABLE_STATUSES = {"active", "in_progress"}
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


class FakeIssuekitClient:
    """In-memory implementation of the IssuekitClient method surface."""

    def __init__(self, issues: list[JsonDict] | None = None) -> None:
        self._lock = Lock()
        self._issues: dict[int, JsonDict] = {}
        self._next_id = 1
        for issue in issues or []:
            self._store_issue(issue)

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
                if (status is None or issue.get("status") == status)
                and (stage is None or issue.get("stage") == stage)
                and (assignee is None or issue.get("assignee") == assignee)
            ]
            if offset is not None:
                issues = issues[offset:]
            if limit is not None:
                issues = issues[:limit]
            return deepcopy(issues)

    def get_issue(self, number: int) -> JsonDict:
        with self._lock:
            return deepcopy(self._find(number))

    def create_issue(self, issue: JsonDict) -> JsonDict:
        with self._lock:
            return deepcopy(self._store_issue(issue, allocate=True))

    def claim(self, number: int, *, assignee: str) -> JsonDict:
        with self._lock:
            issue = self._find(number)
            self._claim_issue(issue, assignee)
            return deepcopy(issue)

    def claim_next(
        self,
        *,
        assignee: str,
        priority: str | None = None,
    ) -> JsonDict | None:
        with self._lock:
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
            self._claim_issue(issue, assignee)
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
        del summary, branch, commit
        with self._lock:
            issue = self._find(number)
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
        del notes, reviewer
        with self._lock:
            issue = self._find(number)
            issue["stage"] = "changes_requested"
            issue["assignee"] = assignee or issue.get("implementer") or "codex"
            return deepcopy(issue)

    def approve(self, number: int, *, summary: str, verification: str, reviewer: str) -> JsonDict:
        del summary, verification, reviewer
        with self._lock:
            issue = self._find(number)
            issue["status"] = "completed"
            issue["stage"] = "done"
            issue["assignee"] = ""
            return deepcopy(issue)

    def complete(self, number: int, *, summary: str, verification: str, force: bool = False) -> JsonDict:
        del summary, verification, force
        with self._lock:
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
            imported = [self._store_issue(issue) for issue in raw_issues]
            return deepcopy(imported)

    def _find(self, number: int) -> JsonDict:
        issue = self._issues.get(number)
        if issue is None:
            raise WorkflowError(f"Issue #{number} was not found.", code="not_found")
        return issue

    def _store_issue(self, issue: JsonDict, *, allocate: bool = False) -> JsonDict:
        stored = deepcopy(issue)
        raw_id = stored.get("id")
        if allocate or raw_id is None:
            issue_id = self._next_id
            self._next_id += 1
        else:
            issue_id = int(raw_id)
            self._next_id = max(self._next_id, issue_id + 1)
        stored["id"] = issue_id
        stored.setdefault("status", "active")
        stored.setdefault("priority", "medium")
        stored.setdefault("assignee", "")
        stored.setdefault("stage", "todo")
        stored.setdefault("implementer", "")
        stored.setdefault("author", "")
        stored.setdefault("body", "")
        self._issues[issue_id] = stored
        return stored

    def _claim_issue(self, issue: JsonDict, assignee: str) -> None:
        issue_id = issue["id"]
        if issue.get("status") not in CLAIMABLE_STATUSES:
            raise WorkflowError(
                f"Issue #{issue_id} has status {issue.get('status')}; only active or in_progress issues can be implemented.",
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
        issue["status"] = "in_progress"
        issue["assignee"] = assignee
        issue["stage"] = "implementing"
        issue["implementer"] = assignee
