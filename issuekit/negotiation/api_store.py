"""API-backed negotiation store."""

from __future__ import annotations

from typing import Any

from issuekit.api import IssuekitClient
from issuekit.config import IssuekitConfig
from issuekit.core import optional_int
from issuekit.negotiation.model import (
    NegotiationEntry,
    NegotiationIssueRefs,
    NegotiationThreadSummary,
    ThreadStatus,
    Verdict,
    coerce_status,
    coerce_verdict,
    latest_agree_contract,
    validate_contract,
    validate_entry_input,
)
from issuekit.workflow import WorkflowError


class ApiNegotiationStore:
    """Negotiation store backed by mine-py's proposal thread endpoints."""

    def __init__(
        self,
        config: IssuekitConfig,
        client: IssuekitClient | None = None,
    ) -> None:
        self.config = config
        self.client = client or IssuekitClient(
            config.api_url,
            project=config.project,
            timeout=config.api_timeout,
        )

    def create_thread(
        self,
        *,
        side: str,
        verdict: Verdict,
        title: str,
        body: str,
        origin: str,
        contract: str | None = None,
    ) -> NegotiationEntry:
        validate_entry_input(side, verdict)
        validate_contract(contract)
        try:
            proposal = self.client.create_proposal(
                origin=origin,
                title=title,
                body=body,
                side=side,
                verdict=coerce_verdict(verdict).value,
                contract=contract,
            )
        except WorkflowError as exc:
            raise _with_negotiation_context(exc, origin, self.config.project) from exc
        entry = entry_from_api(proposal)
        _ensure_idempotent_entry(
            entry,
            origin=origin,
            side=side,
            verdict=coerce_verdict(verdict),
            title=title,
            body=body,
            contract=contract,
            target_project=self.config.project,
        )
        return entry

    def append_entry(
        self,
        thread_id: str,
        *,
        side: str,
        verdict: Verdict,
        title: str,
        body: str,
        origin: str,
        contract: str | None = None,
    ) -> NegotiationEntry:
        validate_entry_input(side, verdict)
        validate_contract(contract)
        entries = self.get_thread(thread_id)
        if not entries:
            raise WorkflowError(
                f"Negotiation thread {thread_id} has no entries.",
                code="invalid_response",
            )
        last_entry_id = entries[-1].id
        if last_entry_id is None:
            raise WorkflowError(
                f"Negotiation thread {thread_id} last entry has no id.",
                code="invalid_response",
            )
        try:
            proposal = self.client.reply_proposal(
                last_entry_id,
                origin=origin,
                title=title,
                body=body,
                side=side,
                verdict=coerce_verdict(verdict).value,
                contract=contract,
            )
        except WorkflowError as exc:
            raise _with_negotiation_context(exc, origin, self.config.project) from exc
        return entry_from_api(proposal)

    def get_thread(self, thread_id: str) -> list[NegotiationEntry]:
        payload = self.client.get_thread(_api_thread_id(thread_id))
        items = payload.get("items")
        if not isinstance(items, list):
            raise WorkflowError(
                "Proposal thread response items was not a JSON array.",
                code="invalid_response",
            )
        return sorted(
            [entry_from_api(item) for item in items],
            key=lambda entry: (
                entry.id is None,
                entry.id if entry.id is not None else 0,
                entry.created,
            ),
        )

    def list_threads(self, *, status: ThreadStatus | None = None) -> list[NegotiationThreadSummary]:
        requested_status = coerce_status(status).value if status is not None else None
        return [
            thread_summary_from_api(thread)
            for thread in self.client.list_threads(status=requested_status)
        ]

    def set_status(
        self,
        thread_id: str,
        status: ThreadStatus,
        *,
        agreed_contract: str | None = None,
    ) -> None:
        validate_contract(agreed_contract)
        next_status = coerce_status(status)
        if next_status is ThreadStatus.agreed and agreed_contract is None:
            agreed_contract = latest_agree_contract(self.get_thread(thread_id))
        self.client.patch_thread(
            _api_thread_id(thread_id),
            status=next_status.value,
            agreed_contract=agreed_contract,
        )

    def get_status(self, thread_id: str) -> ThreadStatus:
        payload = self.client.get_thread(_api_thread_id(thread_id))
        return _status_from_api(payload)

    def get_agreed_contract(self, thread_id: str) -> str | None:
        payload = self.client.get_thread(_api_thread_id(thread_id))
        contract = payload.get("agreed_contract")
        if contract is not None and not isinstance(contract, str):
            raise WorkflowError(
                "Proposal thread agreed_contract was not a string or null.",
                code="invalid_response",
            )
        return contract

    def get_issue_refs(self, thread_id: str) -> NegotiationIssueRefs | None:
        payload = self.client.get_thread(_api_thread_id(thread_id))
        return issue_refs_from_api(payload, require_supported=True)

    def set_issue_refs(self, thread_id: str, refs: NegotiationIssueRefs) -> None:
        payload = self.client.patch_thread(
            _api_thread_id(thread_id),
            backend_issue_ref=refs.backend_issue_ref,
            frontend_issue_ref=refs.frontend_issue_ref,
        )
        stored_refs = issue_refs_from_api(payload, require_supported=True)
        if stored_refs != refs:
            raise WorkflowError(
                "Proposal thread response did not confirm the requested issue refs.",
                code="server_schema_drift",
            )


def _with_negotiation_context(
    exc: WorkflowError,
    origin: str,
    target_project: str,
) -> WorkflowError:
    return WorkflowError(
        f"{exc} (negotiation origin {origin}, target project {target_project})",
        code=exc.code,
    )


def _ensure_idempotent_entry(
    entry: NegotiationEntry,
    *,
    origin: str,
    side: str,
    verdict: Verdict,
    title: str,
    body: str,
    contract: str | None,
    target_project: str,
) -> None:
    """Accept same-origin responses only as exact idempotent retries."""
    if entry.origin != origin:
        return
    mismatched = [
        name
        for name, requested, returned in (
            ("side", side, entry.side),
            ("verdict", verdict, entry.verdict),
            ("title", title, entry.title),
            ("body", body, entry.body),
            ("contract", contract, entry.contract),
        )
        if requested != returned
    ]
    if mismatched:
        raise WorkflowError(
            f"Target project {target_project} returned existing proposal "
            f"#{entry.id} for origin {origin} with different "
            f"{', '.join(mismatched)}; a pending entry already uses this origin. "
            "Adopt or discard it before reopening a negotiation with this origin.",
            code="duplicate_origin",
        )


def _api_thread_id(thread_id: str) -> int:
    try:
        return int(thread_id)
    except (TypeError, ValueError) as exc:
        raise WorkflowError(
            f"Negotiation thread id {thread_id!r} is not an API thread id.",
            code="invalid_value",
        ) from exc


def entry_from_api(raw: Any) -> NegotiationEntry:
    if not isinstance(raw, dict):
        raise WorkflowError("Proposal response was not a JSON object.", code="invalid_response")
    try:
        thread_id = raw["thread_id"]
        side = raw["side"]
        verdict = raw["verdict"]
        title = raw["title"]
        body = raw["body"]
        origin = raw["origin"]
    except KeyError as exc:
        raise WorkflowError(
            f"Proposal response missing {exc.args[0]}.",
            code="invalid_response",
        ) from exc
    contract = raw.get("contract")
    if not isinstance(thread_id, int):
        raise WorkflowError("Proposal response thread_id was not an integer.", code="invalid_response")
    if not isinstance(side, str) or not isinstance(verdict, str):
        raise WorkflowError("Proposal response negotiation fields were invalid.", code="invalid_response")
    if contract is not None and not isinstance(contract, str):
        raise WorkflowError("Proposal response contract was not a string or null.", code="invalid_response")
    if not isinstance(title, str) or not isinstance(body, str) or not isinstance(origin, str):
        raise WorkflowError("Proposal response text fields were invalid.", code="invalid_response")
    created = raw.get("created_at") or raw.get("created") or ""
    if not isinstance(created, str):
        raise WorkflowError("Proposal response created timestamp was invalid.", code="invalid_response")
    try:
        return NegotiationEntry(
            thread_id=str(thread_id),
            side=side,
            verdict=verdict,
            contract=contract,
            title=title,
            body=body,
            origin=origin,
            created=created,
            id=optional_int(raw.get("id")),
        )
    except (TypeError, ValueError) as exc:
        raise WorkflowError(str(exc), code="invalid_response") from exc


def issue_refs_from_api(
    raw: Any,
    *,
    require_supported: bool = False,
) -> NegotiationIssueRefs | None:
    if not isinstance(raw, dict):
        raise WorkflowError("Proposal thread response was not a JSON object.", code="invalid_response")
    if isinstance(raw.get("issue_refs"), dict):
        nested = raw["issue_refs"]
    elif isinstance(raw.get("adopted_issue_refs"), dict):
        nested = raw["adopted_issue_refs"]
    else:
        nested = None
    if isinstance(nested, dict):
        backend_ref = nested.get("backend_issue_ref") or nested.get("backend")
        frontend_ref = nested.get("frontend_issue_ref") or nested.get("frontend")
        supported = True
    else:
        backend_ref = raw.get("backend_issue_ref")
        frontend_ref = raw.get("frontend_issue_ref")
        supported = "backend_issue_ref" in raw and "frontend_issue_ref" in raw
    if require_supported and not supported:
        raise WorkflowError(
            "Proposal thread response did not include issue-ref fields; upgrade the "
            "mine-py API server before finalizing negotiation threads.",
            code="server_schema_drift",
        )
    if backend_ref is None and frontend_ref is None:
        return None
    if not isinstance(backend_ref, str) or not isinstance(frontend_ref, str):
        raise WorkflowError(
            "Proposal thread issue refs were incomplete or invalid.",
            code="invalid_response",
        )
    try:
        return NegotiationIssueRefs(
            backend_issue_ref=backend_ref,
            frontend_issue_ref=frontend_ref,
        )
    except ValueError as exc:
        raise WorkflowError(str(exc), code="invalid_response") from exc


def thread_summary_from_api(raw: Any) -> NegotiationThreadSummary:
    if not isinstance(raw, dict):
        raise WorkflowError("Proposal thread response was not a JSON object.", code="invalid_response")
    try:
        thread_id = raw["id"]
    except KeyError as exc:
        raise WorkflowError(
            f"Proposal thread response missing {exc.args[0]}.",
            code="invalid_response",
        ) from exc
    try:
        status = _status_from_api(raw)
    except WorkflowError:
        raise
    contract = raw.get("agreed_contract")
    if contract is not None and not isinstance(contract, str):
        raise WorkflowError(
            "Proposal thread agreed_contract was not a string or null.",
            code="invalid_response",
        )
    updated = raw.get("updated_at") or raw.get("updated") or ""
    if not isinstance(updated, str):
        raise WorkflowError("Proposal thread updated timestamp was invalid.", code="invalid_response")
    return NegotiationThreadSummary(
        thread_id=str(thread_id),
        status=status,
        agreed_contract=contract,
        issue_refs=issue_refs_from_api(raw),
        updated=updated,
    )


def _status_from_api(raw: Any) -> ThreadStatus:
    if not isinstance(raw, dict):
        raise WorkflowError("Proposal thread response was not a JSON object.", code="invalid_response")
    try:
        return coerce_status(raw.get("status"))
    except ValueError as exc:
        raise WorkflowError(str(exc), code="invalid_response") from exc
