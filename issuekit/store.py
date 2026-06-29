"""Storage seam for issue read paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from issuekit.client import IssuekitClient
from issuekit.config import IssuekitConfig
from issuekit.core import (
    Frontmatter,
    Issue,
    find_issue_by_id,
    get_issue_heading,
    read_active_issues,
    read_all_issues,
    read_completed_issues,
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
    def read_active_issues(self) -> list[Issue]:
        """Read active issues."""

    def read_completed_issues(self) -> list[Issue]:
        """Read completed issues."""

    def read_all_issues(self) -> tuple[list[Issue], list[Issue], list[Issue]]:
        """Read active, completed, and combined issues."""

    def get_issue(self, issue_id: int) -> Issue | None:
        """Read one issue by id."""

    def find_for(self, assignee: str | None = None, stage: str | None = None) -> list[Issue]:
        """Find active issues matching workflow fields."""


class FilesystemStore:
    def __init__(self, issues_dir: Path | str) -> None:
        self.issues_dir = Path(issues_dir)

    def read_active_issues(self) -> list[Issue]:
        return read_active_issues(self.issues_dir)

    def read_completed_issues(self) -> list[Issue]:
        return read_completed_issues(self.issues_dir)

    def read_all_issues(self) -> tuple[list[Issue], list[Issue], list[Issue]]:
        return read_all_issues(self.issues_dir)

    def get_issue(self, issue_id: int) -> Issue | None:
        issue = find_issue_by_id(self.read_active_issues(), issue_id)
        if issue is not None:
            return issue
        return find_issue_by_id(self.read_completed_issues(), issue_id)

    def find_for(self, assignee: str | None = None, stage: str | None = None) -> list[Issue]:
        return [
            issue
            for issue in self.read_active_issues()
            if not issue.decode_error
            and (assignee is None or issue.assignee == assignee)
            and (stage is None or issue.stage == stage)
        ]


class ApiStore:
    def __init__(self, config: IssuekitConfig, client: IssuekitClient | None = None) -> None:
        self.config = config
        self.client = client or IssuekitClient(
            config.api_url,
            project=config.project,
            timeout=config.api_timeout,
        )

    def read_active_issues(self) -> list[Issue]:
        return [issue for issue in self._list_issues() if issue.issue_status != "completed"]

    def read_completed_issues(self) -> list[Issue]:
        return [issue for issue in self._list_issues(status="completed") if issue.issue_status == "completed"]

    def read_all_issues(self) -> tuple[list[Issue], list[Issue], list[Issue]]:
        active_issues = self.read_active_issues()
        completed_issues = self.read_completed_issues()
        all_issues = sorted(active_issues + completed_issues, key=lambda issue: (issue.id or 0, issue.relative_path))
        return active_issues, completed_issues, all_issues

    def get_issue(self, issue_id: int) -> Issue | None:
        try:
            return self._issue_from_response(self.client.get_issue(issue_id))
        except WorkflowError as exc:
            if exc.code == "not_found":
                return None
            raise

    def find_for(self, assignee: str | None = None, stage: str | None = None) -> list[Issue]:
        issues = self._list_issues(assignee=assignee, stage=stage)
        return [issue for issue in issues if issue.issue_status != "completed"]

    def create_issue(
        self,
        *,
        title: str,
        body: str,
        priority: str,
        author: str,
        assignee: str | None = None,
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
                )
            )
        )

    def claim_issue(self, issue_id: int, *, assignee: str) -> Issue:
        return self._issue_from_response(self.client.claim(issue_id, assignee=assignee))

    def claim_next(self, *, assignee: str, priority: str | None = None) -> Issue | None:
        raw = self.client.claim_next(assignee=assignee, priority=priority)
        return None if raw is None else self._issue_from_response(raw)

    def submit_for_review(
        self,
        issue_id: int,
        *,
        summary: str,
        branch: str | None = None,
        commit: str | None = None,
        reviewer: str | None = None,
    ) -> Issue:
        return self._issue_from_response(
            self.client.submit(
                issue_id,
                summary=summary,
                branch=branch,
                commit=commit,
                reviewer=reviewer,
            )
        )

    def request_changes(
        self,
        issue_id: int,
        *,
        notes: str,
        reviewer: str | None = None,
        assignee: str | None = None,
    ) -> Issue:
        return self._issue_from_response(
            self.client.request_changes(
                issue_id,
                notes=notes,
                reviewer=reviewer,
                assignee=assignee,
            )
        )

    def approve_issue(
        self,
        issue_id: int,
        *,
        summary: str,
        verification: str,
        reviewer: str,
    ) -> Issue:
        return self._issue_from_response(
            self.client.approve(
                issue_id,
                summary=summary,
                verification=verification,
                reviewer=reviewer,
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

    def _list_issues(
        self,
        *,
        status: str | None = None,
        assignee: str | None = None,
        stage: str | None = None,
    ) -> list[Issue]:
        issues = [
            self._issue_from_response(raw)
            for raw in self.client.list_all_issues(status=status, assignee=assignee, stage=stage)
        ]
        return sorted(issues, key=lambda issue: (issue.id or 0, issue.relative_path))

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
            "title": _title(raw, body, issue_id),
        }
        synthetic_ref = f"{self.config.project}#{issue_id}"
        status = "completed" if metadata["status"] == "completed" else "active"
        return Issue(
            id=issue_id,
            file_name_id=issue_id,
            file_name=synthetic_ref,
            file_path=Path(synthetic_ref),
            relative_path=synthetic_ref,
            title=metadata["title"],
            status=status,
            issue_status=metadata["status"],
            created=metadata["created"],
            completed=metadata["completed"],
            priority=metadata["priority"],
            assignee=metadata["assignee"],
            stage=metadata["stage"],
            implementer=metadata["implementer"],
            author=metadata["author"],
            content=body,
            frontmatter=Frontmatter(data=metadata, body=body, has_frontmatter=True),
            decode_error=False,
        )


def get_store(config: IssuekitConfig, issues_dir: Path | str | None = None) -> IssueStore:
    if config.use_filesystem_store:
        return FilesystemStore(issues_dir or config.issues_path(Path.cwd()))
    if not config.api_url:
        raise WorkflowError(
            "API store requires api_url. Set api_url in issuekit.toml/[tool.issuekit] "
            "or ISSUEKIT_API_URL; set use_filesystem_store = true only for legacy local tracker access.",
            code="missing_api_url",
        )
    return ApiStore(config)


def _string(value: object) -> str:
    return "" if value is None else str(value).strip()


def _body(value: object) -> str:
    return "" if value is None else str(value)


def _drop_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _title(raw: dict[str, Any], body: str, issue_id: int) -> str:
    raw_title = _string(raw.get("title"))
    if raw_title:
        return raw_title
    heading = get_issue_heading(body)
    if heading:
        return heading.group(1).strip()
    return f"Issue #{issue_id}"
