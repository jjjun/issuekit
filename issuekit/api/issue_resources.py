"""Issue lifecycle API resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from issuekit.core import drop_none
from issuekit.errors import WorkflowError
from issuekit.session import validate_session_token

from .base import JsonDict, ensure_dict


class IssueResourceMixin:
    project: str

    def list_issues(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        assignee: str | None = None,
        include_completed: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[JsonDict]:
        if include_completed:
            payload = self._authorized_request(
                "GET",
                "/api/issues/board",
                params=drop_none(
                    {
                        "projects": self.project,
                        "status": status,
                        "include_completed": True,
                        "stage": stage,
                        "assignee": assignee,
                        "limit": limit,
                        "offset": offset,
                    }
                ),
            )
            page = ensure_dict(payload, "Issue board response")
            items = page.get("items")
            if not isinstance(items, list):
                raise WorkflowError(
                    "Issue board response items was not a JSON array.",
                    code="invalid_response",
                )
            return [ensure_dict(item, "Issue response") for item in items]

        params = drop_none(
            {
                "status": status,
                "stage": stage,
                "assignee": assignee,
                "limit": limit,
                "offset": offset,
            }
        )
        payload = self._request("GET", "/", params=params)
        if not isinstance(payload, list):
            raise WorkflowError("List response was not a JSON array.", code="invalid_response")
        return payload

    def list_all_issues(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        assignee: str | None = None,
        include_completed: bool = False,
        page_size: int = 500,
    ) -> list[JsonDict]:
        if include_completed:
            return list(
                self._paginate(
                    "/api/issues/board",
                    collection=None,
                    params={
                        "projects": self.project,
                        "status": status,
                        "include_completed": True,
                        "stage": stage,
                        "assignee": assignee,
                    },
                    page_label="Issue board response",
                    item_label="Issue response",
                    page_size=page_size,
                )
            )
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
                include_completed=include_completed,
                limit=page_size,
                offset=offset,
            )
            issues.extend(batch)
            if len(batch) < page_size:
                return issues
            offset += page_size

    def count_issues(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        assignee: str | None = None,
        include_completed: bool = False,
    ) -> int:
        payload = self._authorized_request(
            "GET",
            "/api/issues/board",
            params=drop_none(
                {
                    "projects": self.project,
                    "status": status,
                    "include_completed": include_completed,
                    "stage": stage,
                    "assignee": assignee,
                    "limit": 1,
                    "offset": 0,
                }
            ),
        )
        page = ensure_dict(payload, "Issue board response")
        total = page.get("total")
        if not isinstance(total, int):
            raise WorkflowError(
                "Issue board response total was not an integer.",
                code="invalid_response",
            )
        return total

    def get_issue(self, number: int) -> JsonDict:
        payload = self._request("GET", f"/{int(number)}")
        return ensure_dict(payload, "Issue response")

    def get_issue_edit(self, number: int) -> JsonDict:
        payload = self._request("GET", f"/{int(number)}/edit")
        return ensure_dict(payload, "Issue edit response")

    def create_issue(self, issue: Mapping[str, Any], *, session: str | None = None) -> JsonDict:
        body = dict(issue)
        if session is not None:
            body["session"] = validate_session_token(session)
        payload = self._request("POST", "/", json=body)
        return ensure_dict(payload, "Create response")

    def update_issue(self, number: int, issue: Mapping[str, Any]) -> JsonDict:
        update = drop_none(
            {
                "title": issue.get("title"),
                "body": issue.get("body"),
                "priority": issue.get("priority"),
                "depends_on": issue.get("depends_on"),
            }
        )
        if not update:
            raise ValueError("Issue update requires at least one editable field.")
        payload = self._request("PATCH", f"/{int(number)}", json=update)
        return ensure_dict(payload, "Update response")

    def claim(
        self,
        number: int,
        *,
        assignee: str,
        worker: str | None = None,
        allow_self_implement: bool = False,
        session: str | None = None,
    ) -> JsonDict:
        body = drop_none(
            {
                "assignee": assignee,
                "worker": worker,
                "session": _validated_session(session),
            }
        )
        if allow_self_implement:
            body["allow_self_implement"] = True
        payload = self._request(
            "POST",
            f"/{int(number)}/claim",
            json=body,
        )
        return ensure_dict(payload, "Claim response")

    def claim_next(
        self,
        *,
        assignee: str,
        priority: str | None = None,
        worker: str | None = None,
        allow_self_implement: bool = False,
        session: str | None = None,
    ) -> JsonDict | None:
        body = drop_none(
            {
                "assignee": assignee,
                "priority": priority,
                "worker": worker,
                "session": _validated_session(session),
            }
        )
        if allow_self_implement:
            body["allow_self_implement"] = True
        payload = self._request(
            "POST",
            "/claim-next",
            json=body,
        )
        if payload is None:
            return None
        return ensure_dict(payload, "Claim-next response")

    def reclaim(
        self,
        number: int,
        *,
        expected_worker: str | None = None,
        actor: str | None = None,
        reason: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{int(number)}/reclaim",
            json=drop_none(
                {
                    "expected_worker": expected_worker,
                    "actor": actor,
                    "reason": reason,
                }
            ),
        )
        return ensure_dict(payload, "Reclaim response")

    def readdress(
        self,
        number: int,
        *,
        expected_target_worker: str | None = None,
        actor: str | None = None,
        reason: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{int(number)}/readdress",
            json=drop_none(
                {
                    "expected_target_worker": expected_target_worker,
                    "actor": actor,
                    "reason": reason,
                }
            ),
        )
        return ensure_dict(payload, "Readdress response")

    def dispatch(
        self,
        number: int,
        *,
        target_worker: str,
        assignee: str | None = None,
        stage: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{int(number)}/dispatch",
            json=drop_none(
                {
                    "target_worker": target_worker,
                    "assignee": assignee,
                    "stage": stage,
                }
            ),
        )
        return ensure_dict(payload, "Dispatch response")

    def plan(
        self,
        number: int,
        *,
        stage: str = "planned",
        actor: str | None = None,
        note: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{int(number)}/plan",
            json=drop_none(
                {
                    "stage": stage,
                    "actor": actor,
                    "note": note,
                }
            ),
        )
        return ensure_dict(payload, "Plan response")

    def submit(
        self,
        number: int,
        *,
        summary: str,
        branch: str | None = None,
        commit: str | None = None,
        reviewer: str | None = None,
        session: str | None = None,
        agent_model: str | None = None,
        agent_reasoning_effort: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{int(number)}/submit",
            json=drop_none(
                {
                    "summary": summary,
                    "branch": branch,
                    "commit": commit,
                    "reviewer": reviewer,
                    "session": _validated_session(session),
                    "agent_model": agent_model,
                    "agent_reasoning_effort": agent_reasoning_effort,
                }
            ),
        )
        return ensure_dict(payload, "Submit response")

    def request_changes(
        self,
        number: int,
        *,
        notes: str,
        reviewer: str | None = None,
        assignee: str | None = None,
        worker: str | None = None,
        session: str | None = None,
        agent_model: str | None = None,
        agent_reasoning_effort: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{int(number)}/request-changes",
            json=drop_none(
                {
                    "notes": notes,
                    "reviewer": reviewer,
                    "assignee": assignee,
                    "worker": worker,
                    "session": _validated_session(session),
                    "agent_model": agent_model,
                    "agent_reasoning_effort": agent_reasoning_effort,
                }
            ),
        )
        return ensure_dict(payload, "Request-changes response")

    def approve(
        self,
        number: int,
        *,
        summary: str,
        verification: str,
        reviewer: str,
        worker: str | None = None,
        session: str | None = None,
        agent_model: str | None = None,
        agent_reasoning_effort: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{int(number)}/approve",
            json=drop_none(
                {
                    "summary": summary,
                    "verification": verification,
                    "reviewer": reviewer,
                    "worker": worker,
                    "session": _validated_session(session),
                    "agent_model": agent_model,
                    "agent_reasoning_effort": agent_reasoning_effort,
                }
            ),
        )
        return ensure_dict(payload, "Approve response")

    def complete(
        self,
        number: int,
        *,
        summary: str,
        verification: str,
        force: bool = False,
        agent_model: str | None = None,
        agent_reasoning_effort: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{int(number)}/complete",
            json=drop_none(
                {
                    "summary": summary,
                    "verification": verification,
                    "force": force,
                    "agent_model": agent_model,
                    "agent_reasoning_effort": agent_reasoning_effort,
                }
            ),
        )
        return ensure_dict(payload, "Complete response")


def _validated_session(session: str | None) -> str | None:
    return None if session is None else validate_session_token(session)
