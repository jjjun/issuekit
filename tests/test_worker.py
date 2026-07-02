from pathlib import Path
import subprocess

import pytest

from issuekit import cli
from issuekit.config import IssuekitConfig, WorkerIdentity, load_config
from issuekit.worker import (
    WorkerRegistrationError,
    parse_repo_id_from_remote,
    register_worker,
    worker_key,
)
from issuekit.refs import add_ref


@pytest.mark.parametrize(
    ("remote_url", "expected"),
    [
        ("git@github.com:owner/mine-js-monorepo.git", "mine-js-monorepo"),
        ("ssh://git@github.com/owner/mine-js-monorepo.git", "mine-js-monorepo"),
        ("https://github.com/owner/mine-js-monorepo.git", "mine-js-monorepo"),
        ("https://github.com/owner/mine-js-monorepo", "mine-js-monorepo"),
    ],
)
def test_parse_repo_id_from_remote(remote_url: str, expected: str) -> None:
    assert parse_repo_id_from_remote(remote_url) == expected


def test_worker_key_matches_registry_key_format() -> None:
    assert worker_key(WorkerIdentity("machine", "repo", "checkout")) == "machine/repo/checkout"


def test_register_worker_uses_remote_repo_and_basename_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "mine-js-monorepo"
    second = tmp_path / "mine-js-monorepo2"
    first.mkdir()
    second.mkdir()
    _init_git(first, "https://github.com/owner/mine-js-monorepo.git")
    _init_git(second, "git@github.com:owner/mine-js-monorepo.git")
    registry = tmp_path / "workers.toml"
    monkeypatch.setattr("issuekit.worker.platform.node", lambda: "win-desktop")

    first_result = register_worker(first, registry_path=registry)
    second_result = register_worker(second, registry_path=registry)

    assert first_result.identity == WorkerIdentity(
        machine_id="win-desktop",
        repo_id="mine-js-monorepo",
        worker_id="mine-js-monorepo",
    )
    assert second_result.identity == WorkerIdentity(
        machine_id="win-desktop",
        repo_id="mine-js-monorepo",
        worker_id="mine-js-monorepo2",
    )
    assert "repo_id = \"mine-js-monorepo\"" in (second / "issuekit.local.toml").read_text(
        encoding="utf-8"
    )


def test_register_worker_falls_back_to_directory_for_repo_without_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "local-only"
    repo.mkdir()
    _init_git(repo)
    monkeypatch.setattr("issuekit.worker.platform.node", lambda: "win-desktop")

    result = register_worker(repo, registry_path=tmp_path / "workers.toml")

    assert result.identity.repo_id == "local-only"
    assert result.sources["repo_id"] == "working-directory basename"


def test_register_worker_pins_worker_id_across_directory_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    _init_git(repo, "https://github.com/owner/project.git")
    registry = tmp_path / "workers.toml"
    monkeypatch.setattr("issuekit.worker.platform.node", lambda: "win-desktop")

    first = register_worker(repo, registry_path=registry)
    moved = tmp_path / "renamed-checkout"
    repo.rename(moved)
    second = register_worker(moved, registry_path=registry)

    assert first.identity.worker_id == "checkout"
    assert second.identity.worker_id == "checkout"
    assert second.sources["worker_id"] == "pinned"


def test_register_worker_worker_id_override_requires_force(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    register_worker(
        repo,
        machine_id="machine",
        repo_id="project",
        worker_id="checkout",
        registry_path=tmp_path / "workers.toml",
    )

    with pytest.raises(WorkerRegistrationError, match="requires --force"):
        register_worker(
            repo,
            machine_id="machine",
            repo_id="project",
            worker_id="other",
            registry_path=tmp_path / "workers.toml",
        )

    result = register_worker(
        repo,
        machine_id="machine",
        repo_id="project",
        worker_id="other",
        force=True,
        registry_path=tmp_path / "workers.toml",
    )

    assert result.identity.worker_id == "other"


def test_register_worker_refuses_local_collision(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    registry = tmp_path / "workers.toml"
    kwargs = {
        "machine_id": "machine",
        "repo_id": "project",
        "worker_id": "worker",
        "registry_path": registry,
    }
    register_worker(first, **kwargs)

    with pytest.raises(WorkerRegistrationError, match="collision"):
        register_worker(second, **kwargs)


def test_config_reads_local_worker_without_unrelated_local_overrides(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        (
            "[tool.issuekit]\n"
            "project = \"explicit\"\n"
            "issues_dir = \"committed/issues\"\n"
            "[tool.issuekit.worker]\n"
            "machine_id = \"committed-machine\"\n"
            "repo_id = \"committed-repo\"\n"
            "worker_id = \"committed-worker\"\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.local.toml").write_text(
        (
            "project = \"ignored\"\n"
            "issues_dir = \"ignored/issues\"\n"
            "[worker]\n"
            "machine_id = \"local-machine\"\n"
            "repo_id = \"local-repo\"\n"
            "worker_id = \"local-worker\"\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        project="explicit",
        issues_dir="committed/issues",
        worker=WorkerIdentity(
            machine_id="local-machine",
            repo_id="local-repo",
            worker_id="local-worker",
        ),
    )


def test_config_uses_worker_repo_id_as_project_when_project_unset(tmp_path: Path) -> None:
    (tmp_path / "issuekit.local.toml").write_text(
        (
            "[worker]\n"
            "machine_id = \"machine\"\n"
            "repo_id = \"local-repo\"\n"
            "worker_id = \"checkout\"\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.project == "local-repo"
    assert config.worker == WorkerIdentity("machine", "local-repo", "checkout")


def test_worker_and_refs_share_local_config_without_clobbering(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    target = tmp_path / "target"
    repo.mkdir()
    target.mkdir()

    register_worker(
        repo,
        machine_id="machine",
        repo_id="project",
        worker_id="repo",
        registry_path=tmp_path / "workers.toml",
    )
    add_ref("target", target, repo)

    local_config = (repo / "issuekit.local.toml").read_text(encoding="utf-8")
    assert "[worker]" in local_config
    assert "machine_id = \"machine\"" in local_config
    assert "[refs]" in local_config
    assert "target" in local_config


def test_add_cli_writes_worker_and_gitignore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISSUEKIT_WORKER_REGISTRY", str(tmp_path / "workers.toml"))
    monkeypatch.setattr("issuekit.worker.platform.node", lambda: "win-desktop")

    assert cli.main(["register", "--repo-id", "project"]) == 0

    captured = capsys.readouterr()
    assert "machine_id = win-desktop" in captured.out
    assert "repo_id    = project" in captured.out
    assert (tmp_path / "issuekit.local.toml").exists()
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == (
        "issuekit.local.toml\n.agent-runs/\n"
    )


def test_add_cli_best_effort_posts_worker_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from issuekit import worker_registry

    client = FakeRegistryClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISSUEKIT_WORKER_REGISTRY", str(tmp_path / "workers.toml"))
    monkeypatch.setattr("issuekit.worker.platform.node", lambda: "win-desktop")
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)

    assert cli.main(["add", "--repo-id", "demo", "--worker-id", "checkout"]) == 0

    assert client.calls == [
        {
            "machine_id": "win-desktop",
            "repo_id": "demo",
            "worker_id": "checkout",
            "path": tmp_path.resolve().as_posix(),
        }
    ]


def test_add_cli_ignores_worker_registry_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from issuekit import worker_registry

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISSUEKIT_WORKER_REGISTRY", str(tmp_path / "workers.toml"))
    monkeypatch.setattr("issuekit.worker.platform.node", lambda: "win-desktop")
    monkeypatch.setattr(worker_registry, "IssuekitClient", FailingRegistryClient)

    assert cli.main(["add", "--repo-id", "demo"]) == 0

    captured = capsys.readouterr()
    assert "machine_id = win-desktop" in captured.out
    assert "worker registry update failed" in captured.err


def test_add_cli_posts_configured_role_and_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from issuekit import worker_registry

    client = FakeRegistryClient()
    (tmp_path / "issuekit.toml").write_text(
        (
            "api_url = 'https://mine.example'\n"
            "project = 'demo'\n"
            "worker_role = 'api-server'\n"
            "worker_description = 'Hosts the mine-py issue API.'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISSUEKIT_WORKER_REGISTRY", str(tmp_path / "workers.toml"))
    monkeypatch.setattr("issuekit.worker.platform.node", lambda: "win-desktop")
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)

    assert cli.main(["add", "--repo-id", "demo", "--worker-id", "checkout"]) == 0

    assert client.calls == [
        {
            "machine_id": "win-desktop",
            "repo_id": "demo",
            "worker_id": "checkout",
            "path": tmp_path.resolve().as_posix(),
            "role": "api-server",
            "description": "Hosts the mine-py issue API.",
        }
    ]


class FakeRegistryClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, str | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def upsert_worker(
        self,
        *,
        machine_id: str,
        repo_id: str,
        worker_id: str,
        path: str | None,
        role: str | None = None,
        description: str | None = None,
    ) -> dict[str, str | None]:
        call = {
            "machine_id": machine_id,
            "repo_id": repo_id,
            "worker_id": worker_id,
            "path": path,
        }
        if role is not None:
            call["role"] = role
        if description is not None:
            call["description"] = description
        self.calls.append(call)
        return {"id": f"{machine_id}/{repo_id}/{worker_id}", **call}


class FailingRegistryClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def upsert_worker(self, **kwargs):
        raise RuntimeError("registry offline")


def _init_git(path: Path, remote_url: str | None = None) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.DEVNULL)
    if remote_url:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=path,
            check=True,
            stdout=subprocess.DEVNULL,
        )
