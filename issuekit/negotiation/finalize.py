"""Finalization of agreed negotiation threads."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.errors import WorkflowError
from issuekit.negotiation.model import (
    NegotiationEntry,
    NegotiationIssueRefs,
    NegotiationStore,
    ThreadStatus,
)
from issuekit.negotiation.prompts import consumer_issue_body, provider_issue_body
from issuekit.negotiation.thread import (
    CONSUMER_SIDE,
    PROVIDER_SIDE,
    _evaluate_convergence,
    _latest_contract,
    _other_side,
    _require_supported_thread,
    finalize_refusal_reason,
    origin_issue_ref_from_thread,
)
from issuekit.store import get_store


@dataclass(frozen=True)
class NegotiationFinalizationResult:
    thread_id: str
    backend_issue_ref: str
    frontend_issue_ref: str
    created: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "backend_issue_ref": self.backend_issue_ref,
            "frontend_issue_ref": self.frontend_issue_ref,
            "created": self.created,
        }


class IssueCreator(Protocol):
    def create_issue(
        self,
        *,
        project: str,
        title: str,
        body: str,
        priority: str,
        author: str,
        depends_on: tuple[str, ...] | None = None,
    ) -> Issue:
        """Create one implementation issue in a project."""

    def update_issue_body(self, *, project: str, issue_id: int, body: str) -> Issue:
        """Update one issue body after both cross-linked refs are known."""


class ApiIssueCreator:
    def __init__(self, config: IssuekitConfig) -> None:
        self.config = config

    def create_issue(
        self,
        *,
        project: str,
        title: str,
        body: str,
        priority: str,
        author: str,
        depends_on: tuple[str, ...] | None = None,
    ) -> Issue:
        project_config = replace(self.config, project=project)
        with get_store(project_config) as store:
            return store.create_issue(  # type: ignore[attr-defined]
                title=title,
                body=body,
                priority=priority,
                author=author,
                depends_on=depends_on,
            )

    def update_issue_body(self, *, project: str, issue_id: int, body: str) -> Issue:
        project_config = replace(self.config, project=project)
        with get_store(project_config) as store:
            return store.update_issue_body(issue_id, body=body)  # type: ignore[attr-defined]


class MockIssueCreator:
    def __init__(self) -> None:
        self._next_ids: dict[str, int] = {}
        self.issues: dict[str, Issue] = {}

    def create_issue(
        self,
        *,
        project: str,
        title: str,
        body: str,
        priority: str,
        author: str,
        depends_on: tuple[str, ...] | None = None,
    ) -> Issue:
        issue_id = self._next_ids.get(project, 1)
        self._next_ids[project] = issue_id + 1
        issue = Issue(
            id=issue_id,
            ref=f"{project}#{issue_id}",
            title=title,
            issue_status="active",
            created="",
            completed="",
            priority=priority,
            assignee="",
            stage="todo",
            implementer="",
            author=author,
            body=body,
            metadata={"title": title, "depends_on": list(depends_on or ())},
            depends_on=depends_on or (),
        )
        self.issues[issue.ref] = issue
        return issue

    def update_issue_body(self, *, project: str, issue_id: int, body: str) -> Issue:
        ref = f"{project}#{issue_id}"
        issue = self.issues.get(ref)
        if issue is None:
            raise WorkflowError(f"Issue {ref} was not found.", code="not_found")
        updated = replace(issue, body=body)
        self.issues[ref] = updated
        self.issues[ref] = updated
        return updated


def finalize_negotiation(
    *,
    thread_id: str,
    to_project: str,
    author_agent: str,
    priority: str,
    config: IssuekitConfig,
    store: NegotiationStore,
    issue_creator: IssueCreator,
) -> NegotiationFinalizationResult:
    """Create cross-linked implementation issues for an agreed negotiation."""

    thread = _require_finalizable(thread_id, store)
    existing_refs = store.get_issue_refs(thread_id)
    if existing_refs is not None:
        store.settle_thread_members(thread_id)
        return NegotiationFinalizationResult(
            thread_id=thread_id,
            backend_issue_ref=existing_refs.backend_issue_ref,
            frontend_issue_ref=existing_refs.frontend_issue_ref,
            created=False,
        )

    contract = store.get_agreed_contract(thread_id) or _latest_contract(thread)
    if not contract:
        raise WorkflowError(
            f"Negotiation thread {thread_id} has no agreed contract.",
            code="invalid_transition",
        )

    refs = _create_issue_pair(
        thread_id=thread_id,
        to_project=to_project,
        author_agent=author_agent,
        priority=priority,
        config=config,
        thread=thread,
        contract=contract,
        issue_creator=issue_creator,
    )
    _record_issue_refs(thread_id, refs, store)
    return NegotiationFinalizationResult(
        thread_id=thread_id,
        backend_issue_ref=refs.backend_issue_ref,
        frontend_issue_ref=refs.frontend_issue_ref,
        created=True,
    )


def _require_finalizable(thread_id: str, store: NegotiationStore) -> list[NegotiationEntry]:
    status = store.get_status(thread_id)
    thread = store.get_thread(thread_id)
    _require_supported_thread(thread_id, thread)
    if status is ThreadStatus.negotiating:
        outcome = _evaluate_convergence(thread)
        if outcome != "negotiating":
            if outcome == "agreed":
                store.set_status(
                    thread_id,
                    ThreadStatus.agreed,
                    agreed_contract=_latest_contract(thread),
                )
            elif outcome == "blocked":
                store.set_status(thread_id, ThreadStatus.blocked)
                store.settle_thread_members(thread_id)
            status = store.get_status(thread_id)

    if status is not ThreadStatus.agreed:
        raise WorkflowError(
            f"Negotiation thread {thread_id} is {status.value}, not agreed: "
            f"{finalize_refusal_reason(status, thread)}",
            code="invalid_transition",
        )
    return thread


def _create_issue_pair(
    *,
    thread_id: str,
    to_project: str,
    author_agent: str,
    priority: str,
    config: IssuekitConfig,
    thread: list[NegotiationEntry],
    contract: str,
    issue_creator: IssueCreator,
) -> NegotiationIssueRefs:
    origin_issue_ref = origin_issue_ref_from_thread(thread)
    initiator_side = thread[0].side
    counterpart_side = _other_side(initiator_side)
    projects = {initiator_side: config.project, counterpart_side: to_project}
    titles = {
        PROVIDER_SIDE: f"Implement agreed contract from negotiation {thread_id}",
        CONSUMER_SIDE: f"Integrate agreed contract from negotiation {thread_id}",
    }
    provider = issue_creator.create_issue(
        project=projects[PROVIDER_SIDE],
        title=titles[PROVIDER_SIDE],
        body=provider_issue_body(
            thread_id=thread_id,
            origin_issue_ref=origin_issue_ref,
            consumer_issue_ref="pending",
            contract=contract,
        ),
        priority=priority,
        author=author_agent,
    )
    consumer = issue_creator.create_issue(
        project=projects[CONSUMER_SIDE],
        title=titles[CONSUMER_SIDE],
        body=consumer_issue_body(
            thread_id=thread_id,
            origin_issue_ref=origin_issue_ref,
            provider_issue_ref=provider.ref,
            contract=contract,
        ),
        priority=priority,
        author=author_agent,
        depends_on=(f"{projects[PROVIDER_SIDE]}#issue:{_require_issue_id(provider)}",),
    )
    issue_creator.update_issue_body(
        project=projects[PROVIDER_SIDE],
        issue_id=_require_issue_id(provider),
        body=provider_issue_body(
            thread_id=thread_id,
            origin_issue_ref=origin_issue_ref,
            consumer_issue_ref=consumer.ref,
            contract=contract,
        ),
    )
    return NegotiationIssueRefs(
        backend_issue_ref=provider.ref,
        frontend_issue_ref=consumer.ref,
    )


def _record_issue_refs(
    thread_id: str,
    refs: NegotiationIssueRefs,
    store: NegotiationStore,
) -> None:
    store.set_issue_refs(thread_id, refs)
    store.settle_thread_members(thread_id)


def _require_issue_id(issue: Issue) -> int:
    if issue.id is None:
        raise WorkflowError(f"Created issue {issue.ref} has no id.", code="invalid_response")
    return issue.id
