"""Self-registration and heartbeat helpers for API workers."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from issuekit.api import IssuekitClient
from issuekit.api.factory import client_for
from issuekit.config import IssuekitConfig
from issuekit.config.project_profile import load_project_profile
from issuekit.errors import WorkflowError
from issuekit.gitutil import run_git
from issuekit.worker_constants import WORKER_HEARTBEAT_INTERVAL_SEC
from issuekit.workers.identity import canonical_git_origin_url
from issuekit.workers.registry import error_detail_text

LOGGER = logging.getLogger(__name__)


class WorkerRegistryConflict(RuntimeError):
    """Raised when the API rejects a worker registration conflict."""



def post_worker_registration(
    config: IssuekitConfig,
    cwd: Path | str,
    *,
    canonical_url: str | None = None,
    client: IssuekitClient | None = None,
    push_profile: bool = True,
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
    client_context = client_for(config) if client is None else nullcontext(client)
    with client_context as active_client:
        try:
            active_client.upsert_repo(
                repo_key=worker.repo_id,
                canonical_url=resolved_canonical_url,
                description=config.repo_description or None,
                meta=config.repo_metadata or None,
            )
            active_client.upsert_worker(
                machine_id=worker.machine_id,
                repo_id=worker.repo_id,
                worker_name=worker.worker_name,
                path=repo_path.as_posix(),
                project=config.project,
                role=config.worker_role or None,
                description=config.worker_description or None,
                meta=worker_metadata or None,
                accept_directed=True if config.worker_accept_directed else None,
            )
        except WorkflowError as exc:
            raise _registration_error(exc, config) from exc
        if push_profile:
            _push_project_profile(config, cwd, active_client, on_error=on_error)
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



def try_post_worker_registration(
    config: IssuekitConfig,
    cwd: Path | str,
    *,
    canonical_url: str | None = None,
    client: IssuekitClient | None = None,
    push_profile: bool = True,
    on_error: Callable[[Exception], None] | None = None,
) -> bool:
    try:
        return post_worker_registration(
            config,
            cwd,
            canonical_url=canonical_url,
            client=client,
            push_profile=push_profile,
            on_error=on_error,
        )
    except (WorkflowError, WorkerRegistryConflict) as exc:
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
        on_error: Callable[[Exception, int, datetime | None], None] | None = None,
    ) -> None:
        self.config = config
        self.cwd = Path(cwd)
        self.interval = interval
        self.on_error = on_error
        self.consecutive_failures = 0
        self.last_success: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._client: IssuekitClient | None = None
        self._last_profile_key: str | None = None

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
        else:
            self._close_client()

    def beat(self) -> bool:
        errors: list[Exception] = []
        profile_key = _project_profile_change_key(self.config, self.cwd)
        push_profile = profile_key is None or profile_key != self._last_profile_key
        try:
            succeeded = try_post_worker_registration(
                self.config,
                self.cwd,
                client=self._heartbeat_client(),
                push_profile=push_profile,
                on_error=errors.append,
            )
        except Exception:
            self._last_profile_key = None
            raise
        if succeeded:
            with self._state_lock:
                self.consecutive_failures = 0
                self.last_success = datetime.now(UTC)
                last_success = self.last_success
            if self.on_error is not None:
                for exc in errors:
                    self.on_error(exc, 0, last_success)
            self._last_profile_key = (
                profile_key if profile_key is not None and not errors else None
            )
        elif errors:
            self._last_profile_key = None
            self.record_failure(errors[-1])
        else:
            self._last_profile_key = None
        return succeeded

    def _run(self) -> None:
        try:
            self._heartbeat_client()
            while not self._stop.wait(max(0.0, self.interval)):
                try:
                    self.beat()
                except Exception as exc:
                    self.record_failure(exc)
        except Exception as exc:
            self.record_failure(exc)
        finally:
            self._close_client()

    def _heartbeat_client(self) -> IssuekitClient | None:
        if not self.config.api_url or self.config.worker is None:
            return None
        with self._client_lock:
            if self._client is None:
                self._client = client_for(self.config)
            return self._client

    def _close_client(self) -> None:
        with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            client.close()

    def record_failure(self, exc: Exception) -> None:
        with self._state_lock:
            self.consecutive_failures += 1
            consecutive_failures = self.consecutive_failures
            last_success = self.last_success
        if self.on_error is not None:
            self.on_error(exc, consecutive_failures, last_success)


def _project_profile_change_key(
    config: IssuekitConfig,
    cwd: Path | str,
) -> str | None:
    profile_path = Path(cwd) / config.profile_file
    try:
        profile_bytes = profile_path.read_bytes() if profile_path.is_file() else None
    except OSError:
        return None
    head = run_git(["rev-parse", "HEAD"], cwd, timeout=5)
    head_commit = (
        head.stdout.strip() if head is not None and head.returncode == 0 else ""
    )
    key_payload = json.dumps(
        {
            "profile_sha256": (
                hashlib.sha256(profile_bytes).hexdigest()
                if profile_bytes is not None
                else None
            ),
            "summary": config.profile_summary,
            "tags": config.profile_tags,
            "head": head_commit,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(key_payload).hexdigest()


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
    conflict = error_detail_text(details, "conflict", "type", "code")
    if default_conflict == "repo" or "repo" in conflict or _has_any(
        details,
        "canonical_url",
        "registered_canonical_url",
        "existing_canonical_url",
    ):
        registered = error_detail_text(
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


def _has_any(details: dict[str, object], *keys: str) -> bool:
    return any(key in details for key in keys)
