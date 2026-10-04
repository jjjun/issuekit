"""Best-effort API worker registry helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from issuekit.api import JsonDict
from issuekit.api.factory import client_for, require_api_url
from issuekit.config import IssuekitConfig
from issuekit.core import (
    Issue,
    issue_dict,
    worker_display_from_row,
    worker_key_matches_row,
    worker_keys_from_row,
    worker_keys_match,
)
from issuekit.errors import WorkflowError
from issuekit.store import get_store
from issuekit.timestamps import parse_timestamp


class WorkerListingError(RuntimeError):
    """Raised when the worker catalog cannot be listed."""


class WorkerRemovalError(RuntimeError):
    """Raised when worker or repo removal is refused before mutation."""


@dataclass(frozen=True)
class WorkerRemovalResult:
    worker: JsonDict
    deleted: JsonDict
    implementing_issues: tuple[Issue, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "worker": self.worker,
            "display": worker_display_from_row(self.worker),
            "deleted": self.deleted,
            "implementing_issues": [
                issue_dict(issue) | {"worker": issue.worker}
                for issue in self.implementing_issues
            ],
        }


@dataclass(frozen=True)
class WorkerClaim:
    issue: Issue
    worker: str
    last_transition: str = ""


@dataclass(frozen=True)
class WorkerPruneCandidate:
    worker: JsonDict
    stale_seconds: float


@dataclass(frozen=True)
class WorkerPruneResult:
    candidates: tuple[WorkerPruneCandidate, ...]
    deleted: tuple[JsonDict, ...]
    dry_run: bool
    skipped_projects: tuple[JsonDict, ...] = ()


@dataclass(frozen=True)
class RepoRemovalResult:
    repo_key: str
    deleted: JsonDict

    def to_dict(self) -> dict[str, object]:
        return {"repo_key": self.repo_key, "deleted": self.deleted}


def list_api_workers(
    config: IssuekitConfig,
    *,
    repo_id: str | None = None,
    project: str | None = None,
) -> list[JsonDict]:
    require_api_url(config, "Listing workers", error=WorkerListingError)
    with client_for(config) as client:
        return client.list_workers(repo_id=repo_id, project=project)


def remove_api_worker(
    config: IssuekitConfig,
    address: str,
    *,
    force: bool = False,
) -> WorkerRemovalResult:
    require_api_url(config, "Removing workers", error=WorkerListingError)
    worker = resolve_api_worker(config, address)
    try:
        issues = _worker_implementing_issues(config, worker)
    except Exception as exc:
        if not force:
            project = _worker_project(config, worker)
            reason = str(exc) or type(exc).__name__
            raise WorkerRemovalError(
                f"Cannot read issues for worker project {project}: {reason}; "
                "rerun with --force to remove it anyway."
            ) from exc
        issues = []
    if issues and not force:
        issue_list = ", ".join(f"#{issue.id}" for issue in issues)
        raise WorkerRemovalError(
            f"Worker {worker_display_from_row(worker)} holds implementing issue(s) "
            f"{issue_list}; rerun with --force to remove it anyway."
        )
    worker_id = _worker_delete_id(worker)
    with client_for(config) as client:
        deleted = client.delete_worker(worker_id)
    return WorkerRemovalResult(
        worker=worker,
        deleted=deleted,
        implementing_issues=tuple(issues),
    )


def prune_api_workers(
    config: IssuekitConfig,
    *,
    stale_after_sec: float,
    dry_run: bool,
    expected_candidates: tuple[WorkerPruneCandidate, ...] | None = None,
    now: datetime | None = None,
) -> WorkerPruneResult:
    require_api_url(config, "Pruning workers", error=WorkerListingError)
    current = now or datetime.now(UTC)
    workers = list_api_workers(config)
    workers_by_project: dict[str, list[JsonDict]] = {}
    for worker in workers:
        workers_by_project.setdefault(_worker_project(config, worker), []).append(worker)

    issues_by_project: dict[str, list[Issue]] = {}
    skipped_projects: list[JsonDict] = []
    for project in workers_by_project:
        try:
            issues_by_project[project] = _project_issues(config, project)
        except Exception as exc:
            skipped_projects.append(
                {"project": project, "error": str(exc) or type(exc).__name__}
            )

    candidates = tuple(
        _candidate
        for project, project_workers in workers_by_project.items()
        if project in issues_by_project
        for worker in project_workers
        if (
            _candidate := _prune_candidate(
                worker,
                issues_by_project[project],
                current,
                stale_after_sec,
            )
        )
        is not None
    )
    if dry_run:
        return WorkerPruneResult(
            candidates=candidates,
            deleted=(),
            dry_run=True,
            skipped_projects=tuple(skipped_projects),
        )
    candidate_ids = {_worker_delete_id(candidate.worker) for candidate in candidates}
    expected_ids = (
        {_worker_delete_id(candidate.worker) for candidate in expected_candidates}
        if expected_candidates is not None
        else None
    )
    if expected_ids is not None and candidate_ids != expected_ids:
        raise WorkerRemovalError(
            "Worker prune candidate count changed or the candidate set changed; "
            "rerun --dry-run and confirm again."
        )
    deleted: list[JsonDict] = []
    with client_for(config) as client:
        for candidate in candidates:
            deleted.append(client.delete_worker(_worker_delete_id(candidate.worker)))
    return WorkerPruneResult(
        candidates=candidates,
        deleted=tuple(deleted),
        dry_run=False,
        skipped_projects=tuple(skipped_projects),
    )


ACTIVE_CLAIM_STAGES = ("implementing", "review", "changes_requested")


def list_worker_claims(
    config: IssuekitConfig,
    *,
    worker: str | None = None,
    stage: str | None = None,
) -> list[WorkerClaim]:
    with get_store(config) as store:
        issues = store.find_for()
    return [
        _worker_claim(issue)
        for issue in issues
        if _is_active_worker_claim(issue, worker=worker, stage=stage)
    ]


def worker_claim_dict(claim: WorkerClaim) -> dict[str, object]:
    issue = claim.issue
    data: dict[str, object] = {
        "id": issue.id,
        "ref": issue.ref,
        "title": issue.title,
        "stage": issue.stage,
        "assignee": issue.assignee,
        "worker": claim.worker,
        "target_worker": issue.target_worker,
    }
    if claim.last_transition:
        data["last_transition"] = claim.last_transition
    return data


def remove_api_repo(config: IssuekitConfig, repo_key: str) -> RepoRemovalResult:
    require_api_url(config, "Removing repos", error=WorkerListingError)
    with client_for(config) as client:
        try:
            deleted = client.delete_repo(repo_key)
        except WorkflowError as exc:
            raise _repo_removal_error(exc, repo_key) from exc
    return RepoRemovalResult(repo_key=repo_key, deleted=deleted)


def resolve_api_worker(config: IssuekitConfig, address: str) -> JsonDict:
    target = address.strip()
    if not target:
        raise WorkerRemovalError("Worker address is required.")
    workers = list_api_workers(config)
    matches = [
        worker
        for worker in workers
        if worker_key_matches_row(target, worker, directed_target=True)
    ]
    if not matches:
        raise WorkerRemovalError(f"Worker was not found: {address}")
    if len(matches) > 1:
        displays = ", ".join(_qualified_worker_display(worker) for worker in matches)
        raise WorkerRemovalError(f"Worker address is ambiguous: {address} ({displays})")
    return matches[0]


def _qualified_worker_display(worker: Mapping[str, object]) -> str:
    display = worker_display_from_row(worker)
    machine_id = str(worker.get("machine_id") or "").strip()
    return f"{display}@{machine_id}" if machine_id else display


def _worker_implementing_issues(
    config: IssuekitConfig,
    worker: Mapping[str, object],
) -> list[Issue]:
    project_config = replace(config, project=_worker_project(config, worker))
    return [
        claim.issue
        for claim in list_worker_claims(project_config, stage="implementing")
        if worker_key_matches_row(claim.worker, worker)
    ]


def _worker_project(config: IssuekitConfig, worker: Mapping[str, object]) -> str:
    for value in (worker.get("project"), worker.get("repo_id")):
        project = str(value or "").strip()
        if project:
            return project
    return config.project


def _project_issues(config: IssuekitConfig, project: str) -> list[Issue]:
    with get_store(replace(config, project=project)) as store:
        return store.find_for()


def error_detail_text(details: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _worker_delete_id(worker: Mapping[str, object]) -> str:
    row_id = error_detail_text(dict(worker), "id")
    if row_id:
        return row_id
    display = worker_display_from_row(worker)
    if display != "?.?":
        return display
    raise WorkerRemovalError("Worker row did not include an id or worker.repo key.")


def _prune_candidate(
    worker: Mapping[str, object],
    issues: list[Issue],
    now: datetime,
    stale_after_sec: float,
) -> WorkerPruneCandidate | None:
    seen = parse_timestamp(worker.get("last_seen"))
    if seen is None:
        return None
    age = (now - seen).total_seconds()
    if age <= stale_after_sec:
        return None
    keys = worker_keys_from_row(worker)
    if not keys:
        return None
    for issue in issues:
        if issue.stage == "implementing" and issue.worker:
            if worker_key_matches_row(issue.worker, worker):
                return None
        if issue.target_worker:
            if worker_key_matches_row(
                issue.target_worker,
                worker,
                directed_target=True,
            ):
                return None
    return WorkerPruneCandidate(worker=dict(worker), stale_seconds=age)


def _is_active_worker_claim(
    issue: Issue,
    *,
    worker: str | None,
    stage: str | None,
) -> bool:
    if issue.stage not in ACTIVE_CLAIM_STAGES:
        return False
    if stage is not None and issue.stage != stage:
        return False
    claim_worker = _claim_worker(issue)
    if not claim_worker:
        return False
    if worker is None:
        return True
    return worker_keys_match(claim_worker, worker)


def _worker_claim(issue: Issue) -> WorkerClaim:
    return WorkerClaim(
        issue=issue,
        worker=_claim_worker(issue),
        last_transition=_first_metadata_value(issue, "updated_at"),
    )


def _claim_worker(issue: Issue) -> str:
    return issue.worker or _first_metadata_value(issue, "implementation_worker")


def _first_metadata_value(issue: Issue, *keys: str) -> str:
    for key in keys:
        value = issue.metadata.get(key)
        if value:
            return str(value)
    return ""


def _repo_removal_error(exc: WorkflowError, repo_key: str) -> WorkflowError:
    if not _is_repo_reference_conflict(exc):
        return exc
    counts = _reference_counts(exc.details)
    if not counts:
        return WorkflowError(
            f"Repo {repo_key} cannot be removed because it is still referenced.",
            code=exc.code,
            details=exc.details,
        )
    suffix = ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
    return WorkflowError(
        f"Repo {repo_key} cannot be removed because it is still referenced: {suffix}.",
        code=exc.code,
        details=exc.details,
    )


def _is_repo_reference_conflict(exc: WorkflowError) -> bool:
    code = (exc.code or "").lower()
    detail_code = error_detail_text(exc.details, "code").lower()
    nested = exc.details.get("details")
    nested_code = (
        error_detail_text(nested, "code").lower() if isinstance(nested, dict) else ""
    )
    return (
        code == "http_409"
        or "conflict" in code
        or code == "repo_referenced"
        or detail_code == "repo_referenced"
        or nested_code == "repo_referenced"
    )


def _reference_counts(details: dict[str, object]) -> dict[str, int]:
    sources: list[dict[str, object]] = [details]
    nested = details.get("details")
    if isinstance(nested, dict):
        sources.append(nested)
    counts: dict[str, int] = {}
    for source in sources:
        counts.update(_reference_counts_from_mapping(source))
    return counts


def _reference_counts_from_mapping(details: dict[str, object]) -> dict[str, int]:
    raw = details.get("reference_counts")
    if isinstance(raw, dict):
        return {
            str(key): int(value)
            for key, value in raw.items()
            if isinstance(value, int) and value > 0
        }
    counts: dict[str, int] = {}
    for key, value in details.items():
        if key.endswith("_count") and isinstance(value, int) and value > 0:
            counts[key] = value
    return counts
