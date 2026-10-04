"""Proposal check API resources."""

from __future__ import annotations

from issuekit.core import drop_none
from issuekit.errors import WorkflowError

from .base import JsonDict, _validate_project_token, ensure_dict


class ProposalCheckResourceMixin:
    project: str

    def create_proposal_check(
        self,
        proposal_id: int,
        *,
        target_worker: str,
        project: str | None = None,
    ) -> JsonDict:
        """Create a proposal check through the public server endpoint.

        Issuekit normally polls checks created by the dashboard, but keeps this
        endpoint available for API clients that need to create checks directly.
        """
        target_project = self.project if project is None else project
        _validate_project_token(target_project)
        response = self._authorized_response(
            "POST",
            f"/api/issues/{target_project}/proposals/{int(proposal_id)}/checks",
            json={"target_worker": target_worker},
        )
        payload = ensure_dict(
            self._parse_response(response),
            "Proposal check response",
        )
        payload["was_created"] = response.status_code == 201
        return payload

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

    def list_proposal_checks_for_proposal(
        self,
        proposal_id: int,
        *,
        project: str | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        target_project = self.project if project is None else project
        _validate_project_token(target_project)
        return list(
            self._paginate(
                f"/api/issues/{target_project}/proposals/{int(proposal_id)}/checks",
                collection=None,
                params={},
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
            params=drop_none(
                {
                    "target_worker": target_worker,
                    "status": status,
                    "limit": limit,
                    "offset": offset,
                }
            ),
        )
        page = ensure_dict(payload, "Proposal check list response")
        items = page.get("items")
        if not isinstance(items, list):
            raise WorkflowError(
                "Proposal check list response items was not a JSON array.",
                code="invalid_response",
            )
        return [ensure_dict(item, "Proposal check response") for item in items]

    def post_proposal_check_result(
        self,
        check_id: int,
        *,
        project: str,
        verdict: str,
        comment: str | None = None,
        adopted_issue_ref: str | None = None,
    ) -> JsonDict:
        _validate_project_token(project)
        payload = self._authorized_request(
            "POST",
            f"/api/issues/{project}/proposal-checks/{int(check_id)}/result",
            json=drop_none(
                {
                    "verdict": verdict,
                    "comment": comment,
                    "adopted_issue_ref": adopted_issue_ref,
                }
            ),
        )
        return ensure_dict(payload, "Proposal check response")
