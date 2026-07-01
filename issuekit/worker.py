"""Worker identity registration helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tomllib
from urllib.parse import urlparse

from issuekit.config import WorkerIdentity
from issuekit.core import is_valid_workflow_token


LOCAL_CONFIG_NAME = "issuekit.local.toml"
LOCAL_GITIGNORE_ENTRIES = (LOCAL_CONFIG_NAME, ".agent-runs/")
WORKER_REGISTRY_ENV_VAR = "ISSUEKIT_WORKER_REGISTRY"


class WorkerRegistrationError(RuntimeError):
    """Raised when worker identity registration cannot be completed."""


@dataclass(frozen=True)
class WorkerRegistration:
    identity: WorkerIdentity
    sources: dict[str, str]
    written: bool


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
    existing = load_local_worker(repo_path)
    defaults = _default_identity(repo_path)

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
    _ensure_local_config_ignored(repo_path)

    return WorkerRegistration(
        identity=identity,
        sources={
            "machine_id": machine_source,
            "repo_id": repo_source,
            "worker_id": worker_source,
        },
        written=written,
    )


def load_local_worker(cwd: Path | str = ".") -> WorkerIdentity | None:
    path = Path(cwd) / LOCAL_CONFIG_NAME
    if not path.exists():
        return None
    data = _load_toml(path)
    worker = _worker_table(data)
    if worker is None:
        return None
    machine_id = str(worker.get("machine_id", "")).strip()
    repo_id = str(worker.get("repo_id", "")).strip()
    worker_id = str(worker.get("worker_id", "")).strip()
    if not (machine_id and repo_id and worker_id):
        return None
    return WorkerIdentity(machine_id=machine_id, repo_id=repo_id, worker_id=worker_id)


def save_local_worker(identity: WorkerIdentity, cwd: Path | str = ".") -> bool:
    path = Path(cwd) / LOCAL_CONFIG_NAME
    existing_data = _load_toml(path) if path.exists() else {}
    existing_worker = _worker_table(existing_data)
    refs = existing_data.get("refs", {})
    if not isinstance(refs, dict):
        refs = {}
    desired_worker = {
        "machine_id": identity.machine_id,
        "repo_id": identity.repo_id,
        "worker_id": identity.worker_id,
    }
    if existing_worker == desired_worker:
        return False
    content = _local_config_text(worker=desired_worker, refs={str(k): str(v) for k, v in refs.items()})
    path.write_text(content, encoding="utf-8", newline="\n")
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
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(cwd),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


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


def _worker_key(identity: WorkerIdentity) -> str:
    return f"{identity.machine_id}/{identity.repo_id}/{identity.worker_id}"


def _local_config_text(*, worker: dict[str, str], refs: dict[str, str]) -> str:
    lines = ["[worker]"]
    for key in ("machine_id", "repo_id", "worker_id"):
        lines.append(f"{key} = {json.dumps(worker[key])}")
    if refs:
        lines.append("")
        lines.append("[refs]")
        for name in sorted(refs):
            lines.append(f"{name} = {json.dumps(refs[name])}")
    return "\n".join(lines) + "\n"


def _ensure_local_config_ignored(cwd: Path) -> None:
    path = cwd / ".gitignore"
    if not path.exists():
        path.write_text(
            "".join(f"{entry}\n" for entry in LOCAL_GITIGNORE_ENTRIES),
            encoding="utf-8",
            newline="\n",
        )
        return
    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    entries = {line.strip() for line in content.splitlines()}
    missing_entries = [
        entry
        for entry in LOCAL_GITIGNORE_ENTRIES
        if entry not in entries and not (entry == ".agent-runs/" and ".agent-runs" in entries)
    ]
    if not missing_entries:
        return
    separator = "" if content.endswith("\n") or not content else "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(separator)
        for entry in missing_entries:
            handle.write(f"{entry}\n")


def _load_toml(path: Path) -> dict[str, object]:
    try:
        return dict(tomllib.loads(path.read_text(encoding="utf-8-sig")))
    except tomllib.TOMLDecodeError as exc:
        raise WorkerRegistrationError(f"Failed to parse {path}: {exc}") from exc


def _worker_table(data: dict[str, object]) -> dict[str, object] | None:
    worker = data.get("worker")
    if isinstance(worker, dict):
        return worker
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return None
    issuekit = tool.get("issuekit")
    if not isinstance(issuekit, dict):
        return None
    worker = issuekit.get("worker")
    return worker if isinstance(worker, dict) else None


def _repo_name_from_path(value: str) -> str | None:
    normalized = value.strip().strip("/")
    if not normalized:
        return None
    return normalized.replace("\\", "/").split("/")[-1] or None


def _token(value: str | None, *, default: str) -> str:
    raw = (value or "").strip().lower()
    token = re.sub(r"[^a-z0-9_-]+", "-", raw)
    token = re.sub(r"-+", "-", token).strip("-_")
    if not token:
        token = default
    if not token[0].isalnum():
        token = f"{default}-{token}"
    return token[:32]
