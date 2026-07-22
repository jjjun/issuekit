"""Worker identity registration helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re
from urllib.parse import urlparse
from urllib.parse import urlsplit, urlunsplit

from issuekit.config import WorkerIdentity
from issuekit.core import is_valid_workflow_token
from issuekit.gitutil import git_origin_url as _git_origin_url
from issuekit.gitutil import git_root
from issuekit.localconfig import (
    LOCAL_CONFIG_NAME,
    LocalConfigError,
    ensure_gitignore_entries,
    local_config_text,
    load_toml,
    read_local_config,
    write_local_config,
)
from issuekit.workers.keys import worker_key as current_worker_key


WORKER_REGISTRY_ENV_VAR = "ISSUEKIT_WORKER_REGISTRY"


class WorkerRegistrationError(RuntimeError):
    """Raised when worker identity registration cannot be completed."""


@dataclass(frozen=True)
class WorkerRegistration:
    identity: WorkerIdentity
    sources: dict[str, str]
    written: bool
    canonical_url: str | None = None


def register_worker(
    cwd: Path | str = ".",
    *,
    machine_id: str | None = None,
    repo_id: str | None = None,
    worker_id: str | None = None,
    force: bool = False,
    registry_path: Path | str | None = None,
) -> WorkerRegistration:
    repo_path = Path(cwd).resolve()
    git_repo = git_root(repo_path)
    if git_repo is None:
        raise WorkerRegistrationError(
            "issuekit add requires a git-managed checkout. Run it from inside "
            "a git repository, or initialize git first."
        )
    repo_path = git_repo.resolve()
    existing = load_local_worker(repo_path)
    defaults = _default_identity(repo_path)
    canonical_url = canonical_git_origin_url(repo_path)
    if (
        defaults.sources["repo_id"] == "working-directory basename"
        and repo_id is None
        and existing is None
    ):
        raise WorkerRegistrationError(
            "This git checkout has no remote origin. Add remote origin or pass "
            "--repo-id <repository-id> explicitly."
        )

    resolved_machine_id, machine_source = _resolve_id(
        "machine_id",
        override=machine_id,
        pinned=existing.machine_id if existing else None,
        default=defaults.identity.machine_id,
        default_source=defaults.sources["machine_id"],
    )
    resolved_repo_id, repo_source = _resolve_id(
        "repo_id",
        override=repo_id,
        pinned=existing.repo_id if existing else None,
        default=defaults.identity.repo_id,
        default_source=defaults.sources["repo_id"],
    )
    resolved_worker_id, worker_source = _resolve_worker_id(
        override=worker_id,
        pinned=existing.worker_id if existing else None,
        default=defaults.identity.worker_id,
        default_source=defaults.sources["worker_id"],
        force=force,
    )

    identity = WorkerIdentity(
        machine_id=resolved_machine_id,
        repo_id=resolved_repo_id,
        worker_id=resolved_worker_id,
    )
    _validate_identity(identity)
    _validate_local_collision(identity, repo_path, force=force, registry_path=registry_path)

    written = save_local_worker(identity, repo_path)
    _record_worker(identity, repo_path, registry_path=registry_path)
    ensure_gitignore_entries(repo_path)

    return WorkerRegistration(
        identity=identity,
        sources={
            "machine_id": machine_source,
            "repo_id": repo_source,
            "worker_id": worker_source,
        },
        written=written,
        canonical_url=canonical_url,
    )


def load_local_worker(cwd: Path | str = ".") -> WorkerIdentity | None:
    worker = _read_local_config(cwd).worker
    if worker is None:
        return None
    machine_id = str(worker.get("machine_id", "")).strip()
    repo_id = str(worker.get("repo_id", "")).strip()
    worker_id = str(worker.get("worker_name") or worker.get("worker_id") or "").strip()
    if not (machine_id and repo_id and worker_id):
        return None
    return WorkerIdentity(machine_id=machine_id, repo_id=repo_id, worker_id=worker_id)


def save_local_worker(identity: WorkerIdentity, cwd: Path | str = ".") -> bool:
    path = Path(cwd) / LOCAL_CONFIG_NAME
    local_config = _read_local_config(cwd)
    desired_worker = {
        "machine_id": identity.machine_id,
        "repo_id": identity.repo_id,
        "worker_name": identity.worker_name,
    }
    desired_content = local_config_text(
        worker=desired_worker,
        refs=local_config.refs,
        disabled_agents=local_config.disabled_agents,
        author_guard=local_config.author_guard,
    )
    if path.exists():
        existing_content = path.read_text(encoding="utf-8-sig")
    else:
        existing_content = ""
    if existing_content == desired_content:
        return False
    write_local_config(cwd, worker=desired_worker, refs=local_config.refs)
    return True


def parse_repo_id_from_remote(remote_url: str) -> str | None:
    value = remote_url.strip()
    if not value:
        return None
    value = value.rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]

    scp_match = re.match(r"^[^/@:]+@[^:]+:(?P<path>.+)$", value)
    if scp_match:
        return _repo_name_from_path(scp_match.group("path"))

    parsed = urlparse(value)
    if parsed.scheme and parsed.path:
        return _repo_name_from_path(parsed.path)

    return _repo_name_from_path(value)


def git_origin_url(cwd: Path | str = ".") -> str | None:
    return _git_origin_url(cwd)


def canonical_git_origin_url(cwd: Path | str = ".") -> str | None:
    remote_url = git_origin_url(cwd)
    if remote_url is None:
        return None
    return canonicalize_remote_url(remote_url)


def canonicalize_remote_url(remote_url: str) -> str | None:
    """Return a transport-agnostic identity for a git remote.

    The canonical URL is an identity key, never a clone target, so the same
    repository must map to one value regardless of how a checkout was cloned.
    ssh/scp/git remotes and https remotes for the same host and path therefore
    all collapse to a single ``https://<host>/<path>`` form (userinfo dropped),
    so a machine cloning over ssh matches a peer that cloned over https.
    """
    value = remote_url.strip()
    if not value:
        return None
    value = value.rstrip("/")

    scp_match = re.match(r"^(?P<user>[^/@:]+)@(?P<host>[^:]+):(?P<path>.+)$", value)
    if scp_match:
        host = scp_match.group("host").lower()
        path = _canonical_remote_path(scp_match.group("path"))
        if not path:
            return None
        return f"https://{host}/{path}"

    parsed = urlsplit(value)
    if parsed.scheme and parsed.netloc:
        host = parsed.hostname
        if not host:
            return None
        host = host.lower()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        path = _canonical_remote_path(parsed.path)
        if not path:
            return None
        return urlunsplit(("https", host, f"/{path}", "", ""))

    path = _canonical_remote_path(value)
    return path or None


def _default_identity(cwd: Path) -> WorkerRegistration:
    hostname = platform.node().strip()
    machine_id = _token(hostname, default="machine")
    remote_url = git_origin_url(cwd)
    remote_repo = parse_repo_id_from_remote(remote_url) if remote_url else None
    repo_id = _token(remote_repo or cwd.name, default="repo")
    worker_id = _token(cwd.name, default="worker")
    return WorkerRegistration(
        identity=WorkerIdentity(machine_id=machine_id, repo_id=repo_id, worker_id=worker_id),
        sources={
            "machine_id": "hostname",
            "repo_id": "git remote origin" if remote_repo else "working-directory basename",
            "worker_id": "working-directory basename, pinned",
        },
        written=False,
        canonical_url=canonical_git_origin_url(cwd),
    )


def _resolve_id(
    name: str,
    *,
    override: str | None,
    pinned: str | None,
    default: str,
    default_source: str,
) -> tuple[str, str]:
    if override is not None:
        return override.strip(), "flag"
    if pinned:
        return pinned, "pinned"
    return default, default_source


def _resolve_worker_id(
    *,
    override: str | None,
    pinned: str | None,
    default: str,
    default_source: str,
    force: bool,
) -> tuple[str, str]:
    if override is not None:
        value = override.strip()
        if pinned and value != pinned and not force:
            raise WorkerRegistrationError(
                "Overriding a pinned worker_id requires --force."
            )
        return value, "flag"
    if pinned:
        return pinned, "pinned"
    return default, default_source


def _validate_identity(identity: WorkerIdentity) -> None:
    for name, value in (
        ("machine_id", identity.machine_id),
        ("repo_id", identity.repo_id),
        ("worker_id", identity.worker_id),
    ):
        if not value or not is_valid_workflow_token(value):
            raise WorkerRegistrationError(f"Invalid {name}: {value}")


def _validate_local_collision(
    identity: WorkerIdentity,
    cwd: Path,
    *,
    force: bool,
    registry_path: Path | str | None,
) -> None:
    registry = _load_worker_registry(registry_path)
    key = _worker_key(identity)
    existing_path = registry.get(key)
    if not existing_path:
        return
    existing = Path(existing_path).expanduser()
    if existing.resolve() == cwd:
        return
    if not existing.exists():
        return
    if force:
        return
    raise WorkerRegistrationError(
        "Worker identity collision: "
        f"{key} is already registered for {existing.resolve().as_posix()}. "
        "Use --force to overwrite it."
    )


def _record_worker(
    identity: WorkerIdentity,
    cwd: Path,
    *,
    registry_path: Path | str | None,
) -> None:
    path = _worker_registry_path(registry_path)
    registry = _load_worker_registry(path)
    registry[_worker_key(identity)] = cwd.as_posix()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["[workers]"]
    for key in sorted(registry):
        lines.append(f"{json.dumps(key)} = {json.dumps(registry[key])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _load_worker_registry(registry_path: Path | str | None) -> dict[str, str]:
    path = _worker_registry_path(registry_path)
    if not path.exists():
        return {}
    data = _load_toml(path)
    workers = data.get("workers", {})
    if not isinstance(workers, dict):
        raise WorkerRegistrationError(f"{path} must contain a [workers] table.")
    return {str(key): str(value) for key, value in workers.items()}


def _worker_registry_path(registry_path: Path | str | None) -> Path:
    if registry_path is not None:
        return Path(registry_path).expanduser().resolve()
    env_path = os.environ.get(WORKER_REGISTRY_ENV_VAR, "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    return (Path.home() / ".issuekit" / "workers.toml").resolve()


def worker_key(identity: WorkerIdentity) -> str:
    return current_worker_key(identity.repo_id, identity.worker_name)


def _worker_key(identity: WorkerIdentity) -> str:
    return worker_key(identity)


def _load_toml(path: Path) -> dict[str, object]:
    try:
        return load_toml(path)
    except LocalConfigError as exc:
        raise WorkerRegistrationError(str(exc)) from exc


def _read_local_config(cwd: Path | str = "."):
    try:
        return read_local_config(cwd)
    except LocalConfigError as exc:
        raise WorkerRegistrationError(str(exc)) from exc


def _repo_name_from_path(value: str) -> str | None:
    normalized = value.strip().strip("/")
    if not normalized:
        return None
    return normalized.replace("\\", "/").split("/")[-1] or None


def _canonical_remote_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/").strip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    parts = [part for part in normalized.split("/") if part]
    return "/".join(parts)


def _token(value: str | None, *, default: str) -> str:
    raw = (value or "").strip().lower()
    token = re.sub(r"[^a-z0-9_-]+", "-", raw)
    token = re.sub(r"-+", "-", token).strip("-_")
    if not token:
        token = default
    if not token[0].isalnum():
        token = f"{default}-{token}"
    return token[:32]
