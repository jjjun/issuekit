"""Storage seam for issue read paths."""

from __future__ import annotations

from typing import Any, Protocol

from issuekit.client import IssuekitClient
from issuekit.config import IssuekitConfig
from issuekit.core import (
    Issue,
    _drop_none,
    get_issue_heading,
)
from issuekit.workflow import WorkflowError


REQUIRED_API_FIELDS = {
    "id",
    "body",
    "status",
    "priority",
    "created",
    "completed",
    "assignee",
    "stage",
    "implementer",
    "author",
}


class IssueStore(Protocol):
    def __enter__(self) -> "IssueStore":
        """Enter a store lifecycle context."""

    def __exit__(self, *exc_info: object) -> None:
        """Exit a store lifecycle context."""

    def close(self) -> None:
        """Release any store-owned resources."""

    def read_all_issues(self) -> tuple[list[Issue], list[Issue], list[Issue]]:
        """Read active, completed, and combined issues."""

    def get_issue(self, issue_id: int) -> Issue | None:
        """Read one issue by id."""

    def update_issue(
        self,
        issue_id: int,
        *,
        title: str | None = None,
        body: str | None = None,
        priority: str | None = None,
    ) -> Issue:
        """Update editable issue fields."""

    def find_for(self, assignee: str | None = None, stage: str | None = None) -> list[Issue]:
        """Find active issues matching workflow fields."""

    def find_implementing_for_worker(self, worker: str) -> list[Issue]:
        """Find in-progress implementation issues held by a worker."""


class ApiStore:
    def __init__(self, config: IssuekitConfig, client: IssuekitClient | None = None) -> None:
        self.config = config
        self._owns_client = client is None
        self.client = client or IssuekitClient(
            config.api_url,
            project=config.project,
            timeout=config.api_timeout,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "ApiStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def read_all_issues(self) -> tuple[list[Issue], list[Issue], list[Issue]]:
        all_issues = self._list_issues(include_completed=True)
        active_issues, completed_issues = _partition_issues(all_issues)
        return active_issues, completed_issues, all_issues

    def get_issue(self, issue_id: int) -> Issue | None:
        try:
            return self._issue_from_response(self.client.get_issue(issue_id))
        except WorkflowError as exc:
            if exc.code == "not_found":
                return None
            raise

    def find_for(self, assignee: str | None = None, stage: str | None = None) -> list[Issue]:
        return self._list_issues(assignee=assignee, stage=stage)

    def count_issues(
        self,
        *,
        status: str | None = None,
        include_completed: bool = False,
    ) -> int:
        count = getattr(self.client, "count_issues", None)
        if count is not None:
            return int(count(status=status, include_completed=include_completed))
        return len(self.client.list_all_issues(status=status, include_completed=include_completed))

    def latest_issue_id(
        self,
        *,
        status: str | None = None,
        include_completed: bool = False,
        total: int | None = None,
    ) -> int:
        count = total if total is not None else self.count_issues(
            status=status,
            include_completed=include_completed,
        )
        if count <= 0:
            return 0
        items = self.client.list_issues(
            status=status,
            include_completed=include_completed,
            limit=1,
            offset=count - 1,
        )
        return max((int(item.get("id", 0)) for item in items), default=0)

    def find_implementing_for_worker(self, worker: str) -> list[Issue]:
        issues = self._list_issues(stage="implementing")
        return [issue for issue in issues if issue.worker == worker]

    def create_issue(
        self,
        *,
        title: str,
        body: str,
        priority: str,
        author: str,
        assignee: str | None = None,
        session: str | None = None,
    ) -> Issue:
        return self._issue_from_response(
            self.client.create_issue(
                _drop_none(
                    {
                        "title": title,
                        "body": body,
                        "priority": priority,
                        "author": author,
                        "assignee": assignee,
                    }
                ),
                session=session,
            )
        )

    def update_issue_body(self, issue_id: int, *, body: str) -> Issue:
        return self.update_issue(issue_id, body=body)

    def update_issue(
        self,
        issue_id: int,
        *,
        title: str | None = None,
        body: str | None = None,
        priority: str | None = None,
    ) -> Issue:
        return self._issue_from_response(
            self.client.update_issue(
                issue_id,
                _drop_none({"title": title, "body": body, "priority": priority}),
            )
        )

    def claim_issue(
        self,
        issue_id: int,
        *,
        assignee: str,
        worker: str | None = None,
        allow_self_implement: bool = False,
        session: str | None = None,
    ) -> Issue:
        return self._issue_from_response(
            self.client.claim(
                issue_id,
                assignee=assignee,
                worker=worker,
                allow_self_implement=allow_self_implement,
                session=session,
            )
        )

    def claim_next(
        self,
        *,
        assignee: str,
        priority: str | None = None,
        worker: str | None = None,
        allow_self_implement: bool = False,
        session: str | None = None,
    ) -> Issue | None:
        raw = self.client.claim_next(
            assignee=assignee,
            priority=priority,
            worker=worker,
            allow_self_implement=allow_self_implement,
            session=session,
        )
        return None if raw is None else self._issue_from_response(raw)

    def submit_for_review(
        self,
        issue_id: int,
        *,
        summary: str,
        branch: str | None = None,
        commit: str | None = None,
        reviewer: str | None = None,
        session: str | None = None,
    ) -> Issue:
        return self._issue_from_response(
            self.client.submit(
                issue_id,
                summary=summary,
                branch=branch,
                commit=commit,
                reviewer=reviewer,
                session=session,
            )
        )

    def request_changes(
        self,
        issue_id: int,
        *,
        notes: str,
        reviewer: str | None = None,
        assignee: str | None = None,
        worker: str | None = None,
        session: str | None = None,
    ) -> Issue:
        return self._issue_from_response(
            self.client.request_changes(
                issue_id,
                notes=notes,
                reviewer=reviewer,
                assignee=assignee,
                worker=worker,
                session=session,
            )
        )

    def approve_issue(
        self,
        issue_id: int,
        *,
        summary: str,
        verification: str,
        reviewer: str,
        worker: str | None = None,
        session: str | None = None,
    ) -> Issue:
        return self._issue_from_response(
            self.client.approve(
                issue_id,
                summary=summary,
                verification=verification,
                reviewer=reviewer,
                worker=worker,
                session=session,
            )
        )

    def complete_issue(
        self,
        issue_id: int,
        *,
        summary: str,
        verification: str,
        force: bool = False,
    ) -> Issue:
        return self._issue_from_response(
            self.client.complete(
                issue_id,
                summary=summary,
                verification=verification,
                force=force,
            )
        )

    def reclaim_issue(
        self,
        issue_id: int,
        *,
        expected_worker: str | None = None,
        actor: str | None = None,
        reason: str | None = None,
    ) -> Issue:
        return self._issue_from_response(
            self.client.reclaim(
                issue_id,
                expected_worker=expected_worker,
                actor=actor,
                reason=reason,
            )
        )

    def _list_issues(
        self,
        *,
        status: str | None = None,
        assignee: str | None = None,
        stage: str | None = None,
        include_completed: bool = False,
    ) -> list[Issue]:
        issues = [
            self._issue_from_response(raw)
            for raw in self.client.list_all_issues(
                status=status,
                assignee=assignee,
                stage=stage,
                include_completed=include_completed,
            )
        ]
        return sorted(issues, key=lambda issue: (issue.id or 0, issue.ref))

    def _issue_from_response(self, raw: dict[str, Any]) -> Issue:
        missing = sorted(field for field in REQUIRED_API_FIELDS if field not in raw)
        if missing:
            ref = raw.get("id", "<unknown>")
            raise ValueError(
                f"API issue response {ref} is missing required field(s): {', '.join(missing)}"
            )

        try:
            issue_id = int(raw["id"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"API issue response has invalid id: {raw.get('id')}") from exc

        body = _body(raw.get("body"))
        metadata = {
            "id": str(issue_id),
            "status": _string(raw.get("status")),
            "priority": _string(raw.get("priority")),
            "created": _string(raw.get("created")),
            "completed": _string(raw.get("completed")),
            "assignee": _string(raw.get("assignee")),
            "stage": _string(raw.get("stage")),
            "implementer": _string(raw.get("implementer")),
            "author": _string(raw.get("author")),
            "worker": _string(raw.get("worker")),
            "author_session": _string(raw.get("author_session")),
            "implementer_session": _string(raw.get("implementer_session")),
            "reviewer_session": _string(raw.get("reviewer_session")),
            "origin": _string(raw.get("origin")),
            "title": _title(raw, body, issue_id),
        }
        synthetic_ref = f"{self.config.project}#{issue_id}"
        return Issue(
            id=issue_id,
            ref=synthetic_ref,
            title=metadata["title"],
            issue_status=metadata["status"],
            created=metadata["created"],
            completed=metadata["completed"],
            priority=metadata["priority"],
            assignee=metadata["assignee"],
            stage=metadata["stage"],
            implementer=metadata["implementer"],
            author=metadata["author"],
            body=body,
            metadata=metadata,
            worker=metadata["worker"],
        )


def get_store(config: IssuekitConfig) -> IssueStore:
    if not config.api_url:
        raise WorkflowError(
            "API store requires api_url. Set api_url in issuekit.toml/[tool.issuekit] "
            "or ISSUEKIT_API_URL.",
            code="missing_api_url",
        )
    return ApiStore(config)


def _string(value: object) -> str:
    return "" if value is None else str(value).strip()


def _body(value: object) -> str:
    return "" if value is None else str(value)


def _partition_issues(issues: list[Issue]) -> tuple[list[Issue], list[Issue]]:
    active_issues: list[Issue] = []
    completed_issues: list[Issue] = []
    for issue in issues:
        if issue.issue_status == "completed":
            completed_issues.append(issue)
        else:
            active_issues.append(issue)
    return active_issues, completed_issues


def _title(raw: dict[str, Any], body: str, issue_id: int) -> str:
    raw_title = _string(raw.get("title"))
    if raw_title:
        return raw_title
    heading = get_issue_heading(body)
    if heading:
        return heading.group(1).strip()
    return f"Issue #{issue_id}"
