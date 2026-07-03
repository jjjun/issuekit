"""Best-effort API worker registry helpers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading

from issuekit.client import IssuekitClient, JsonDict
from issuekit.config import IssuekitConfig
from issuekit.project_profile import load_project_profile
from issuekit.workflow import WorkflowError


WORKER_HEARTBEAT_INTERVAL_SEC = 60.0


class WorkerListingError(RuntimeError):
    """Raised when the worker catalog cannot be listed."""


def post_worker_registration(
    config: IssuekitConfig,
    cwd: Path | str,
    *,
    on_error: Callable[[Exception], None] | None = None,
) -> bool:
    if not config.api_url or config.worker is None:
        return False

    repo_path = Path(cwd).resolve()
    worker = config.worker
    with IssuekitClient(
        config.api_url,
        project=config.project,
        timeout=config.api_timeout,
    ) as client:
        client.upsert_worker(
            machine_id=worker.machine_id,
            repo_id=worker.repo_id,
            worker_id=worker.worker_id,
            path=repo_path.as_posix(),
            role=config.worker_role or None,
            description=config.worker_description or None,
        )
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

    Tolerates a backend that predates mine-py#172 (404/405) and stale-source
    rejections: such failures are logged through on_error and swallowed.
    """
    try:
        profile = load_project_profile(config, cwd)
        if profile is None:
            return
        client.put_project_profile(**profile.to_payload())
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
    on_error: Callable[[Exception], None] | None = None,
) -> bool:
    try:
        return post_worker_registration(config, cwd, on_error=on_error)
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
