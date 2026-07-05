"""Best-effort API worker registry helpers."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
import threading

from issuekit.client import IssuekitClient, JsonDict
from issuekit.config import IssuekitConfig
from issuekit.project_profile import load_project_profile
from issuekit.worker import canonical_git_origin_url
from issuekit.workflow import WorkflowError


WORKER_HEARTBEAT_INTERVAL_SEC = 60.0
LOGGER = logging.getLogger(__name__)


class WorkerListingError(RuntimeError):
    """Raised when the worker catalog cannot be listed."""


class WorkerRegistryConflict(RuntimeError):
    """Raised when the API rejects a worker registration conflict."""


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
