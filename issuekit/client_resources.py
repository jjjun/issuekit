"""Resource-specific method mixins for :class:`issuekit.client.IssuekitClient`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from issuekit.client_base import JsonDict, _ensure_dict, _profile_rows, _worker_rows
from issuekit.core import _drop_none
from issuekit.workflow import WorkflowError


class _IssueResourceMixin:
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
                params=_drop_none(
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
            page = _ensure_dict(payload, "Issue board response")
            items = page.get("items")
            if not isinstance(items, list):
                raise WorkflowError(
                    "Issue board response items was not a JSON array.",
                    code="invalid_response",
                )
            return [_ensure_dict(item, "Issue response") for item in items]

        params = _drop_none(
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
            params=_drop_none(
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
        page = _ensure_dict(payload, "Issue board response")
        total = page.get("total")
        if not isinstance(total, int):
            raise WorkflowError(
                "Issue board response total was not an integer.",
                code="invalid_response",
            )
        return total

    def get_issue(self, number: int) -> JsonDict:
        payload = self._request("GET", f"/{number}")
        return _ensure_dict(payload, "Issue response")

    def create_issue(self, issue: Mapping[str, Any]) -> JsonDict:
        payload = self._request("POST", "/", json=dict(issue))
        return _ensure_dict(payload, "Create response")

    def update_issue(self, number: int, issue: Mapping[str, Any]) -> JsonDict:
        update = _drop_none(
            {
                "title": issue.get("title"),
                "body": issue.get("body"),
                "priority": issue.get("priority"),
            }
        )
        if not update:
            raise ValueError("Issue update requires at least one editable field.")
        payload = self._request("PATCH", f"/{number}", json=update)
        return _ensure_dict(payload, "Update response")

    def claim(
        self,
        number: int,
        *,
        assignee: str,
        worker: str | None = None,
        allow_self_implement: bool = False,
    ) -> JsonDict:
        body = _drop_none({"assignee": assignee, "worker": worker})
        if allow_self_implement:
            body["allow_self_implement"] = True
        payload = self._request(
            "POST",
            f"/{number}/claim",
            json=body,
        )
        return _ensure_dict(payload, "Claim response")

    def claim_next(
        self,
        *,
        assignee: str,
        priority: str | None = None,
        worker: str | None = None,
        allow_self_implement: bool = False,
    ) -> JsonDict | None:
        body = _drop_none({"assignee": assignee, "priority": priority, "worker": worker})
        if allow_self_implement:
            body["allow_self_implement"] = True
        payload = self._request(
            "POST",
            "/claim-next",
            json=body,
        )
        if payload is None:
            return None
        return _ensure_dict(payload, "Claim-next response")

    def submit(
        self,
        number: int,
        *,
        summary: str,
        branch: str | None = None,
        commit: str | None = None,
        reviewer: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{number}/submit",
            json=_drop_none(
                {
                    "summary": summary,
                    "branch": branch,
                    "commit": commit,
                    "reviewer": reviewer,
                }
            ),
        )
        return _ensure_dict(payload, "Submit response")

    def request_changes(
        self,
        number: int,
        *,
        notes: str,
        reviewer: str | None = None,
        assignee: str | None = None,
        worker: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{number}/request-changes",
            json=_drop_none(
                {"notes": notes, "reviewer": reviewer, "assignee": assignee, "worker": worker}
            ),
        )
        return _ensure_dict(payload, "Request-changes response")

    def approve(
        self,
        number: int,
        *,
        summary: str,
        verification: str,
        reviewer: str,
        worker: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{number}/approve",
            json=_drop_none(
                {
                    "summary": summary,
                    "verification": verification,
                    "reviewer": reviewer,
                    "worker": worker,
                }
            ),
        )
        return _ensure_dict(payload, "Approve response")

    def complete(self, number: int, *, summary: str, verification: str, force: bool = False) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{number}/complete",
            json={
                "summary": summary,
                "verification": verification,
                "force": force,
            },
        )
        return _ensure_dict(payload, "Complete response")

    def import_issues(self, issues: list[Mapping[str, Any]] | Mapping[str, Any]) -> JsonDict | list[JsonDict]:
        items = [dict(issue) for issue in issues] if isinstance(issues, list) else [dict(issues)]
        payload = self._request("POST", "/import", json={"issues": items})
        if not isinstance(payload, (dict, list)):
            raise WorkflowError("Import response was not JSON data.", code="invalid_response")
        return payload


class _WorkerResourceMixin:
    def upsert_worker(
        self,
        *,
        machine_id: str,
        repo_id: str,
        worker_id: str,
        path: str | None,
        role: str | None = None,
        description: str | None = None,
    ) -> JsonDict:
        body = {
            "machine_id": machine_id,
            "repo_id": repo_id,
            "worker_id": worker_id,
            "path": path,
        }
        # role/description are optional, backward-compatible fields: only send
        # them when set so older backends keep accepting the payload.
        body.update(_drop_none({"role": role, "description": description}))
        payload = self._authorized_request("POST", "/api/workers", json=body)
        return _ensure_dict(payload, "Worker response")

    def list_workers(
        self,
        *,
        repo_id: str | None = None,
        project: str | None = None,
    ) -> list[JsonDict]:
        payload = self._authorized_request(
            "GET",
            "/api/workers",
            params=_drop_none({"repo_id": repo_id, "project": project}),
        )
        return _worker_rows(payload)


class _ProfileResourceMixin:
    project: str

    def put_project_profile(
        self,
        *,
        summary: str | None = None,
        profile_md: str | None = None,
        tags: Sequence[str] | None = None,
        source_commit: str | None = None,
        source_committed_at: str | None = None,
    ) -> JsonDict:
        body = _drop_none(
            {
                "summary": summary,
                "profile_md": profile_md,
                "tags": list(tags) if tags is not None else None,
                "source_commit": source_commit,
                "source_committed_at": source_committed_at,
            }
        )
        payload = self._authorized_request(
            "PUT",
            f"/api/projects/{self.project}/profile",
            json=body,
        )
        return _ensure_dict(payload, "Project profile response")

    def get_project_profile(self, project: str | None = None) -> JsonDict:
        target = project or self.project
        payload = self._authorized_request(
            "GET",
            f"/api/projects/{target}/profile",
        )
        return _ensure_dict(payload, "Project profile response")

    def list_project_profiles(self) -> list[JsonDict]:
        payload = self._authorized_request("GET", "/api/projects/profiles")
        return _profile_rows(payload)


class _ProposalCheckResourceMixin:
    project: str

    def create_proposal_check(
        self,
        proposal_id: int,
        *,
        target_worker: str,
        project: str | None = None,
    ) -> JsonDict:
        target_project = project or self.project
        payload = self._authorized_request(
            "POST",
            f"/api/issues/{target_project}/proposals/{proposal_id}/checks",
            json={"target_worker": target_worker},
        )
        return _ensure_dict(payload, "Proposal check response")

    def list_proposal_checks(
        self,
        *,
        target_worker: str,
        status: str | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        return list(
            self._paginate(
                "/api/issues/proposal-checks",
                collection=None,
                params={"target_worker": target_worker, "status": status},
                page_label="Proposal check list response",
                item_label="Proposal check response",
                page_size=page_size,
            )
        )

    def poll_proposal_checks(
        self,
        *,
        target_worker: str,
        status: str = "pending",
        limit: int = 50,
        offset: int = 0,
    ) -> list[JsonDict]:
        payload = self._authorized_request(
            "GET",
            "/api/issues/proposal-checks",
            params=_drop_none(
                {
                    "target_worker": target_worker,
                    "status": status,
                    "limit": limit,
                    "offset": offset,
                }
            ),
        )
        page = _ensure_dict(payload, "Proposal check list response")
        items = page.get("items")
        if not isinstance(items, list):
            raise WorkflowError(
                "Proposal check list response items was not a JSON array.",
                code="invalid_response",
            )
        return [_ensure_dict(item, "Proposal check response") for item in items]

    def post_proposal_check_result(
        self,
        check_id: int,
        *,
        project: str,
        verdict: str,
        comment: str | None = None,
        adopted_issue_ref: str | None = None,
    ) -> JsonDict:
        payload = self._authorized_request(
            "POST",
            f"/api/issues/{project}/proposal-checks/{check_id}/result",
            json=_drop_none(
                {
                    "verdict": verdict,
                    "comment": comment,
                    "adopted_issue_ref": adopted_issue_ref,
                }
            ),
        )
        return _ensure_dict(payload, "Proposal check response")


class _ProposalResourceMixin:
    def create_proposal(
        self,
        *,
        origin: str,
        title: str,
        body: str,
        reply_to: str | None = None,
        blocking: bool | None = None,
        priority: str | None = None,
        depends_on: Sequence[str] | str | None = None,
        thread_id: int | None = None,
        side: str | None = None,
        verdict: str | None = None,
        contract: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "POST",
            "/",
            collection="proposals",
            json=_drop_none(
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
                }
            ),
        )
        return _ensure_dict(payload, "Proposal response")

    def list_proposals(
        self,
        *,
        status: str | None = None,
        thread_id: int | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        return list(
            self._paginate(
                "/",
                collection="proposals",
                params={"status": status, "thread_id": thread_id},
                page_label="Proposal list response",
                item_label="Proposal response",
                page_size=page_size,
            )
        )

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
        payload = self._request(
            "POST",
            f"/{proposal_id}/reply",
            collection="proposals",
            json=_drop_none(
                {
                    "origin": origin,
                    "title": title,
                    "body": body,
                    "side": side,
                    "verdict": verdict,
                    "contract": contract,
                    "priority": priority,
                }
            ),
        )
        return _ensure_dict(payload, "Proposal response")

    def get_thread(self, thread_id: int) -> JsonDict:
        payload = self._request("GET", f"/thread/{thread_id}", collection="proposals")
        return _ensure_dict(payload, "Proposal thread response")

    def list_threads(
        self,
        *,
        status: str | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        return list(
            self._paginate(
                "/threads",
                collection="proposals",
                params={"status": status},
                page_label="Proposal thread list response",
                item_label="Proposal thread response",
                page_size=page_size,
            )
        )

    def patch_thread(
        self,
        thread_id: int,
        *,
        status: str | None = None,
        agreed_contract: str | None = None,
        backend_issue_ref: str | None = None,
        frontend_issue_ref: str | None = None,
    ) -> JsonDict:
        payload = self._request(
            "PATCH",
            f"/thread/{thread_id}",
            collection="proposals",
            json=_drop_none(
                {
                    "status": status,
                    "agreed_contract": agreed_contract,
                    "backend_issue_ref": backend_issue_ref,
                    "frontend_issue_ref": frontend_issue_ref,
                }
            ),
        )
        return _ensure_dict(payload, "Proposal thread response")

    def get_proposal(self, proposal_id: int) -> JsonDict:
        payload = self._request("GET", f"/{proposal_id}", collection="proposals")
        return _ensure_dict(payload, "Proposal response")

    def adopt_proposal(self, proposal_id: int, *, priority: str | None = None) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{proposal_id}/adopt",
            collection="proposals",
            json=_drop_none({"priority": priority}),
        )
        return _ensure_dict(payload, "Adopt proposal response")

    def discard_proposal(self, proposal_id: int) -> JsonDict:
        payload = self._request("POST", f"/{proposal_id}/discard", collection="proposals")
        return _ensure_dict(payload, "Discard proposal response")

    def import_proposals(self, proposals: list[Mapping[str, Any]] | Mapping[str, Any]) -> list[JsonDict]:
        items = [dict(proposal) for proposal in proposals] if isinstance(proposals, list) else [dict(proposals)]
        payload = self._request("POST", "/import", collection="proposals", json={"proposals": items})
        if not isinstance(payload, list):
            raise WorkflowError("Proposal import response was not a JSON array.", code="invalid_response")
        return [_ensure_dict(item, "Proposal import response item") for item in payload]
