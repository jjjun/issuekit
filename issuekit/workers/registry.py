"""Best-effort API worker registry helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import threading

from issuekit.api import IssuekitClient, JsonDict
from issuekit.config import IssuekitConfig
from issuekit.core import Issue, worker_display_from_row, worker_keys_from_row, worker_keys_match
from issuekit.config.project_profile import load_project_profile
from issuekit.store import get_store
from issuekit.worker_constants import WORKER_HEARTBEAT_INTERVAL_SEC
from issuekit.workers.identity import canonical_git_origin_url
from issuekit.workflow import WorkflowError


LOGGER = logging.getLogger(__name__)


class WorkerListingError(RuntimeError):
    """Raised when the worker catalog cannot be listed."""


class WorkerRegistryConflict(RuntimeError):
    """Raised when the API rejects a worker registration conflict."""


class WorkerRemovalError(RuntimeError):
    """Raised when worker or repo removal is refused before mutation."""


@dataclass(frozen=True)
class WorkerRemovalResult:
    worker: JsonDict
    deleted: JsonDict
    implementing_issues: tuple[Issue, ...]


@dataclass(frozen=True)
class WorkerClaim:
    issue: Issue
    worker: str
    claimed: str = ""
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


@dataclass(frozen=True)
class RepoRemovalResult:
    repo_key: str
    deleted: JsonDict


def post_worker_registration(
    config: IssuekitConfig,
    cwd: Path | str,
    *,
    canonical_url: str | None = None,
    on_error: Callable[[Exception], None] | None = None,
) -> bool:
    if not config.api_url or config.worker is None:
        return False

    repo_path = Path(cwd).resolve()
    worker = config.worker
    resolved_canonical_url = canonical_url or canonical_git_origin_url(repo_path)
    worker_metadata = dict(config.worker_metadata)
    if config.worker_role and "role" not in worker_metadata:
        worker_metadata["role"] = config.worker_role
    if config.worker_description and "description" not in worker_metadata:
        worker_metadata["description"] = config.worker_description
    with IssuekitClient(
        config.api_url,
        project=config.project,
        timeout=config.api_timeout,
    ) as client:
        try:
            try:
                client.upsert_repo(
                    repo_key=worker.repo_id,
                    canonical_url=resolved_canonical_url,
                    description=config.repo_description or None,
                    meta=config.repo_metadata or None,
                )
            except WorkflowError as exc:
                if _is_missing_repo_endpoint(exc):
                    if on_error is not None:
                        on_error(exc)
                    else:
                        LOGGER.debug("%s", exc)
                else:
                    raise _registration_error(exc, config, default_conflict="repo") from exc
            client.upsert_worker(
                machine_id=worker.machine_id,
                repo_id=worker.repo_id,
                worker_name=worker.worker_name,
                path=repo_path.as_posix(),
                project=config.project,
                role=config.worker_role or None,
                description=config.worker_description or None,
                worker_metadata=worker_metadata or None,
                accept_directed=True if config.worker_accept_directed else None,
            )
        except WorkflowError as exc:
            raise _registration_error(exc, config) from exc
        _push_project_profile(config, cwd, client, on_error=on_error)
    return True


def _push_project_profile(
    config: IssuekitConfig,
    cwd: Path | str,
    client: IssuekitClient,
    *,
    on_error: Callable[[Exception], None] | None,
) -> None:
    """PUT the local project profile if one exists; never fail registration.

    Tolerates a backend that predates project profiles (404/405) and stale
    writes (HTTP 200 with stale:true): such failures are logged through
    on_error and swallowed.
    """
    try:
        profile = load_project_profile(config, cwd)
        if profile is None:
            return
        response = client.put_project_profile(**profile.to_payload())
        if bool(response.get("stale", False)):
            exc = WorkflowError(
                "Project profile push was stale; server kept the newer stored profile.",
                code="stale_project_profile",
            )
            if on_error is not None:
                on_error(exc)
            else:
                LOGGER.debug("%s", exc)
    except (WorkflowError, ValueError, OSError) as exc:
        if on_error is not None:
            on_error(exc)


def list_api_workers(
    config: IssuekitConfig,
    *,
    repo_id: str | None = None,
    project: str | None = None,
) -> list[JsonDict]:
    if not config.api_url:
        raise WorkerListingError(
            "Listing workers requires api_url in issuekit.toml/[tool.issuekit] "
            "or ISSUEKIT_API_URL."
        )
    with IssuekitClient(
        config.api_url,
        project=config.project,
        timeout=config.api_timeout,
    ) as client:
        return client.list_workers(repo_id=repo_id, project=project)


def remove_api_worker(
    config: IssuekitConfig,
    address: str,
    *,
    force: bool = False,
) -> WorkerRemovalResult:
    if not config.api_url:
        raise WorkerListingError(
            "Removing workers requires api_url in issuekit.toml/[tool.issuekit] "
            "or ISSUEKIT_API_URL."
        )
    worker = resolve_api_worker(config, address)
    issues = _worker_implementing_issues(config, worker)
    if issues and not force:
        issue_list = ", ".join(f"#{issue.id}" for issue in issues)
        raise WorkerRemovalError(
            f"Worker {worker_display_from_row(worker)} holds implementing issue(s) "
            f"{issue_list}; rerun with --force to remove it anyway."
        )
    worker_id = _worker_delete_id(worker)
    with IssuekitClient(
        config.api_url,
        project=config.project,
        timeout=config.api_timeout,
    ) as client:
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
    expected_count: int | None = None,
    now: datetime | None = None,
) -> WorkerPruneResult:
    if not config.api_url:
        raise WorkerListingError(
            "Pruning workers requires api_url in issuekit.toml/[tool.issuekit] "
            "or ISSUEKIT_API_URL."
        )
    current = now or datetime.now(timezone.utc)
    workers = list_api_workers(config)
    with get_store(config) as store:
        issues = store.find_for()
    candidates = tuple(
        _candidate
        for worker in workers
        if (_candidate := _prune_candidate(worker, issues, current, stale_after_sec))
        is not None
    )
    if dry_run:
        return WorkerPruneResult(candidates=candidates, deleted=(), dry_run=True)
    if expected_count is not None and len(candidates) != expected_count:
        raise WorkerRemovalError(
            "Worker prune candidate count changed; rerun --dry-run and confirm again."
        )
    deleted: list[JsonDict] = []
    with IssuekitClient(
        config.api_url,
        project=config.project,
        timeout=config.api_timeout,
    ) as client:
        for candidate in candidates:
            deleted.append(client.delete_worker(_worker_delete_id(candidate.worker)))
    return WorkerPruneResult(
        candidates=candidates,
        deleted=tuple(deleted),
        dry_run=False,
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
    if claim.claimed:
        data["claimed"] = claim.claimed
    if claim.last_transition:
        data["last_transition"] = claim.last_transition
    return data


def remove_api_repo(config: IssuekitConfig, repo_key: str) -> RepoRemovalResult:
    if not config.api_url:
        raise WorkerListingError(
            "Removing repos requires api_url in issuekit.toml/[tool.issuekit] "
            "or ISSUEKIT_API_URL."
        )
    with IssuekitClient(
        config.api_url,
        project=config.project,
        timeout=config.api_timeout,
    ) as client:
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
    matches = [worker for worker in workers if target in worker_keys_from_row(worker)]
    if not matches:
        raise WorkerRemovalError(f"Worker was not found: {address}")
    if len(matches) > 1:
        displays = ", ".join(worker_display_from_row(worker) for worker in matches)
        raise WorkerRemovalError(f"Worker address is ambiguous: {address} ({displays})")
    return matches[0]


def try_post_worker_registration(
    config: IssuekitConfig,
    cwd: Path | str,
    *,
    canonical_url: str | None = None,
    on_error: Callable[[Exception], None] | None = None,
) -> bool:
    try:
        return post_worker_registration(
            config,
            cwd,
            canonical_url=canonical_url,
            on_error=on_error,
        )
    except Exception as exc:
        if on_error is not None:
            on_error(exc)
        return False


class WorkerHeartbeat:
    def __init__(
        self,
        config: IssuekitConfig,
        cwd: Path | str,
        *,
        interval: float = WORKER_HEARTBEAT_INTERVAL_SEC,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self.config = config
        self.cwd = Path(cwd)
        self.interval = interval
        self.on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.config.api_url or self.config.worker is None:
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="issuekit-worker-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        while not self._stop.wait(max(0.0, self.interval)):
            try_post_worker_registration(self.config, self.cwd, on_error=self.on_error)


def _registration_error(
    exc: WorkflowError,
    config: IssuekitConfig,
    *,
    default_conflict: str = "worker",
) -> Exception:
    code = (exc.code or "").lower()
    if code != "http_409" and "conflict" not in code and code != "duplicate_worker":
        return exc
    details = exc.details
    conflict = _detail_text(details, "conflict", "type", "code")
    if default_conflict == "repo" or "repo" in conflict or _has_any(
        details,
        "canonical_url",
        "registered_canonical_url",
        "existing_canonical_url",
    ):
        registered = _detail_text(
            details,
            "registered_canonical_url",
            "existing_canonical_url",
            "canonical_url",
        )
        suffix = (
            f" Registered canonical_url for this repo key: {registered}."
            if registered
            else ""
        )
        return WorkerRegistryConflict(
            f"{exc}.{suffix} Rerun `issuekit add --repo-id <unique-repo-id>` "
            "to register this checkout under an explicit repository id."
        )
    worker = config.worker
    if worker is None:
        return exc
    suggestion = f"{worker.machine_id}-{worker.worker_name}"
    return WorkerRegistryConflict(
        f"{exc}. Worker name '{worker.worker_name}' is already registered for "
        f"repo_id '{worker.repo_id}' by another machine. Rerun with "
        f"`issuekit add --worker-id {suggestion}` or choose an explicit --worker-id."
    )


def _detail_text(details: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _has_any(details: dict[str, object], *keys: str) -> bool:
    return any(key in details for key in keys)


def _is_missing_repo_endpoint(exc: WorkflowError) -> bool:
    return (exc.code or "").lower() in {"http_404", "http_405"}


def _worker_implementing_issues(
    config: IssuekitConfig,
    worker: Mapping[str, object],
) -> list[Issue]:
    keys = worker_keys_from_row(worker)
    return [
        claim.issue
        for claim in list_worker_claims(config, stage="implementing")
        if any(worker_keys_match(claim.worker, key) for key in keys)
    ]


def _worker_delete_id(worker: Mapping[str, object]) -> str:
    row_id = _detail_text(dict(worker), "id")
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
    seen = _parse_timestamp(worker.get("last_seen"))
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
            if any(worker_keys_match(issue.worker, key) for key in keys):
                return None
        if issue.target_worker:
            if any(worker_keys_match(issue.target_worker, key) for key in keys):
                return None
    return WorkerPruneCandidate(worker=dict(worker), stale_seconds=age)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
        claimed=_first_metadata_value(issue, "claimed", "claimed_at"),
        last_transition=_first_metadata_value(
            issue,
            "last_transition",
            "last_transition_at",
            "last_transitioned_at",
            "updated_at",
            "updated",
        ),
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
    detail_code = _detail_text(exc.details, "code").lower()
    nested = exc.details.get("details")
    nested_code = (
        _detail_text(nested, "code").lower() if isinstance(nested, dict) else ""
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
