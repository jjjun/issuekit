"""Proposal and negotiation API resources."""

from __future__ import annotations

from collections.abc import Sequence

from issuekit.core import drop_none

from .base import JsonDict, ensure_dict


class ProposalResourceMixin:
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
        target_worker: str | None = None,
    ) -> JsonDict:
        response = self._authorized_response(
            "POST",
            self._collection_path("proposals", "/"),
            json=drop_none(
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
            ),
        )
        payload = ensure_dict(self._parse_response(response), "Proposal response")
        payload["was_created"] = response.status_code == 201
        return payload

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

    def list_proposals_board(
        self,
        *,
        projects: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        origin_project: str | None = None,
        page_size: int = 500,
    ) -> list[JsonDict]:
        return list(
            self._paginate(
                "/api/issues/proposals/board",
                collection=None,
                params={
                    "projects": projects,
                    "status": statuses,
                    "origin_project": origin_project,
                },
                page_label="Proposal board response",
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
            f"/{int(proposal_id)}/reply",
            collection="proposals",
            json=drop_none(
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
        return ensure_dict(payload, "Proposal response")

    def get_thread(self, thread_id: int) -> JsonDict:
        payload = self._request("GET", f"/thread/{int(thread_id)}", collection="proposals")
        return ensure_dict(payload, "Proposal thread response")

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
            f"/thread/{int(thread_id)}",
            collection="proposals",
            json=drop_none(
                {
                    "status": status,
                    "agreed_contract": agreed_contract,
                    "backend_issue_ref": backend_issue_ref,
                    "frontend_issue_ref": frontend_issue_ref,
                }
            ),
        )
        return ensure_dict(payload, "Proposal thread response")

    def get_proposal(self, proposal_id: int) -> JsonDict:
        payload = self._request("GET", f"/{int(proposal_id)}", collection="proposals")
        return ensure_dict(payload, "Proposal response")

    def adopt_proposal(self, proposal_id: int, *, priority: str | None = None) -> JsonDict:
        payload = self._request(
            "POST",
            f"/{int(proposal_id)}/adopt",
            collection="proposals",
            json=drop_none({"priority": priority}),
        )
        return ensure_dict(payload, "Adopt proposal response")

    def discard_proposal(self, proposal_id: int) -> JsonDict:
        payload = self._request("POST", f"/{int(proposal_id)}/discard", collection="proposals")
        return ensure_dict(payload, "Discard proposal response")
