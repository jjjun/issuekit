import json
import subprocess
from pathlib import Path

from issuekit import cli
from issuekit import proposals_api
from issuekit.author_guard import read_author_guard
from issuekit.config import IssuekitConfig, TriagePolicy
from issuekit.proposals_api import _git_commit
from issuekit.proposals import ProposalError, origin_destination
from issuekit.testing import FakeIssuekitClient

from tests.issue_helpers import api_issue


def _write_workspace_refs(path: Path, *names: str) -> None:
    lines = ["[projects]"]
    for name in names:
        lines.append(f'{name} = "{name}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def test_origin_destination_uses_project_segment() -> None:
    assert origin_destination("source#42@abc123") == "source"


def test_api_cli_propose_posts_expected_body_and_dedupes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    created_projects: list[str] = []
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )

    def fake_client(*args, **kwargs):
        created_projects.append(kwargs["project"])
        return client

    monkeypatch.setattr(proposals_api, "IssuekitClient", fake_client)
    monkeypatch.chdir(tmp_path)
    client.register_catalog_project("target")

    argv = [
        "propose",
        "--to",
        "target",
        "--title",
        "API Proposal",
        "--body",
        "## Suggested Change\n\nDo this.",
        "--json",
    ]
    assert cli.main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli.main(argv) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["id"] == second["id"]
    assert first["origin"] == "source#0@unknown"
    assert first["title"] == "API Proposal"
    assert first["dependency_ref"] == "target#proposal:1"
    assert second["dependency_ref"] == "target#proposal:1"
    assert first["payload_mismatch"] is False
    assert first["stop"] == "STOP_NOW"
    guard = read_author_guard(tmp_path)
    assert guard is not None
    assert guard.kind == "proposal"
    assert guard.project == "source"
    assert guard.target_project == "target"
    assert second["payload_mismatch"] is False
    assert "idempotent_existing" not in second
    assert client.calls[0] == {
        "method": "create_proposal",
        "body": {
            "origin": "source#0@unknown",
            "title": "API Proposal",
            "body": "## Suggested Change\n\nDo this.",
        },
    }
    assert created_projects == ["source", "target", "source", "target"]
    assert not (tmp_path / "docs" / "issues" / "incoming").exists()


def test_api_cli_propose_requires_local_project_context(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISSUEKIT_API_URL", "https://mine.example")
    monkeypatch.setenv("ISSUEKIT_PROJECT", "issuekit")

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "target",
                "--title",
                "Scratch proposal",
                "--body",
                "Body.",
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert "needs a local issuekit project context" in captured.err
    assert "--project <project>" in captured.err


def test_api_cli_propose_project_override_allows_scratch_cwd(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    created_projects: list[str] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISSUEKIT_API_URL", "https://mine.example")
    monkeypatch.setenv("ISSUEKIT_PROJECT", "wrong-project")
    client.register_catalog_project("target")

    def fake_client(*args, **kwargs):
        created_projects.append(kwargs["project"])
        return client

    monkeypatch.setattr(proposals_api, "IssuekitClient", fake_client)

    assert (
        cli.main(
            [
                "propose",
                "--project",
                "source",
                "--to",
                "target",
                "--title",
                "Explicit source",
                "--body",
                "Body.",
                "--json",
            ]
        )
        == 0
    )

    sent = json.loads(capsys.readouterr().out)
    assert sent["origin"] == "source#0@unknown"
    assert client.calls[0]["body"]["origin"] == "source#0@unknown"
    assert created_projects == ["source", "target"]


def test_api_cli_propose_accepts_worker_repo_target(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    created_projects: list[str] = []
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )

    def fake_client(*args, **kwargs):
        created_projects.append(kwargs["project"])
        return client

    monkeypatch.setattr(proposals_api, "IssuekitClient", fake_client)
    monkeypatch.chdir(tmp_path)
    client.register_catalog_project("target")

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "checkout.target",
                "--title",
                "Directed",
                "--body",
                "Body.",
                "--json",
            ]
        )
        == 0
    )

    sent = json.loads(capsys.readouterr().out)
    assert sent["target_worker"] == "checkout"
    assert client.calls[0] == {
        "method": "create_proposal",
        "body": {
            "origin": "source#0@unknown",
            "title": "Directed",
            "body": "Body.",
            "target_worker": "checkout",
        },
    }
    assert created_projects == ["source", "target"]


def test_api_cli_propose_sends_machine_qualified_target_worker(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    client = FakeIssuekitClient()

    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)
    client.register_catalog_project("target")

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "checkout.target@pike3",
                "--title",
                "Directed",
                "--body",
                "Body.",
                "--json",
            ]
        )
        == 0
    )

    sent = json.loads(capsys.readouterr().out)
    assert sent["target_worker"] == "checkout@pike3"
    assert client.calls[0]["body"]["target_worker"] == "checkout@pike3"


def test_api_cli_propose_rejects_machine_qualifier_without_worker(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "target@pike3",
                "--title",
                "Bad",
                "--body",
                "Body.",
            ]
        )
        == 1
    )

    assert "machine qualifier requires the worker.repo@machine form" in (
        capsys.readouterr().err
    )


def test_api_cli_propose_rejects_invalid_worker_repo_target(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "bad.worker.target",
                "--title",
                "Bad",
                "--body",
                "Body.",
            ]
        )
        == 1
    )

    assert "Expected repo, worker.repo, or worker.repo@machine" in capsys.readouterr().err


def test_api_cli_propose_rejects_unknown_target_when_profile_catalog_exists(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    client.project = "registered"
    client.put_project_profile(summary="Registered project.", profile_md="# Registered\n")
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "stale-alias",
                "--title",
                "Bad target",
                "--body",
                "Body.",
            ]
        )
        == 1
    )

    assert "Unknown target project 'stale-alias'" in capsys.readouterr().err
    assert not any(call["method"] == "create_proposal" for call in client.calls)


def test_api_cli_propose_allows_registered_target_with_repo_id_mismatch(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    client.project = "registered-target"
    client.put_project_profile(summary="Target project.", profile_md="# Target\n")
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source-project'\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.local.toml").write_text(
        (
            "[worker]\n"
            "machine_id = \"machine\"\n"
            "repo_id = \"physical-repo\"\n"
            "worker_id = \"checkout\"\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "registered-target",
                "--title",
                "Registered target",
                "--body",
                "Body.",
                "--json",
            ]
        )
        == 0
    )

    sent = json.loads(capsys.readouterr().out)
    assert sent["origin"] == "source-project#0@unknown"
    assert sent["payload_mismatch"] is False


def test_api_cli_propose_accepts_worker_project_catalog(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from issuekit.workflow import WorkflowError

    class WorkerCatalogClient(FakeIssuekitClient):
        def list_project_profiles(self):
            raise WorkflowError("profile endpoint not found", code="http_404")

        def list_workers(self, *, repo_id=None, project=None):
            return [
                {
                    "machine_id": "machine",
                    "repo_id": "physical-repo",
                    "worker_id": "checkout",
                    "project": "registered-target",
                }
            ]

    client = WorkerCatalogClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "registered-target",
                "--title",
                "Worker catalog target",
                "--body",
                "Body.",
                "--json",
            ]
        )
        == 0
    )

    assert json.loads(capsys.readouterr().out)["title"] == "Worker catalog target"


def test_api_cli_propose_rejects_target_for_empty_supported_profile_catalog(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from issuekit.workflow import WorkflowError

    class EmptyProfileCatalogClient(FakeIssuekitClient):
        def list_project_profiles(self):
            return []

        def list_workers(self, *, repo_id=None, project=None):
            raise WorkflowError("worker endpoint not found", code="http_404")

    client = EmptyProfileCatalogClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "unknown-target",
                "--title",
                "Empty profile catalog",
                "--body",
                "Body.",
            ]
        )
        == 1
    )

    assert "Unknown target project 'unknown-target'" in capsys.readouterr().err
    assert not any(call["method"] == "create_proposal" for call in client.calls)


def test_api_cli_propose_rejects_target_for_empty_supported_worker_catalog(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from issuekit.workflow import WorkflowError

    class EmptyWorkerCatalogClient(FakeIssuekitClient):
        def list_project_profiles(self):
            raise WorkflowError("profile endpoint not found", code="http_404")

        def list_workers(self, *, repo_id=None, project=None):
            return []

    client = EmptyWorkerCatalogClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "unknown-target",
                "--title",
                "Empty worker catalog",
                "--body",
                "Body.",
            ]
        )
        == 1
    )

    assert "Unknown target project 'unknown-target'" in capsys.readouterr().err
    assert not any(call["method"] == "create_proposal" for call in client.calls)


def test_api_cli_propose_can_mark_blocking(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)
    client.register_catalog_project("target")

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "target",
                "--title",
                "Blocking API Proposal",
                "--body",
                "Needed by source.",
                "--blocking",
                "--json",
            ]
        )
        == 0
    )
    sent = json.loads(capsys.readouterr().out)

    assert sent["blocking"] is True
    assert client.calls[0] == {
        "method": "create_proposal",
        "body": {
            "origin": "source#0@unknown",
            "title": "Blocking API Proposal",
            "body": "Needed by source.",
            "blocking": True,
        },
    }


def test_api_cli_propose_attaches_dependency_refs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)
    client.register_catalog_project("mine-js-monorepo")

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "mine-js-monorepo",
                "--title",
                "Dashboard follow-up",
                "--body",
                "Use the accepted API contract.",
                "--depends-on",
                "mine-py#42",
                "--json",
            ]
        )
        == 0
    )
    sent = json.loads(capsys.readouterr().out)

    assert sent["depends_on"] == ["mine-py#42"]
    assert sent["dependency_ref"] == "mine-js-monorepo#proposal:1"
    assert "warnings" not in sent
    assert client.calls[0] == {
        "method": "create_proposal",
        "body": {
            "origin": "source#0@unknown",
            "title": "Dashboard follow-up",
            "body": "Use the accepted API contract.",
            "depends_on": ["mine-py#42"],
        },
    }


def test_api_cli_propose_reads_structured_dependency_body_refs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)
    client.register_catalog_project("mine-js-monorepo")

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "mine-js-monorepo",
                "--title",
                "Dashboard follow-up",
                "--body",
                "Depends-On: mine-py#42\n\nUse the accepted API contract.",
                "--json",
            ]
        )
        == 0
    )
    sent = json.loads(capsys.readouterr().out)

    assert sent["depends_on"] == ["mine-py#42"]
    assert client.calls[0]["body"]["depends_on"] == ["mine-py#42"]


def test_api_cli_propose_accepts_explicit_dependency_refs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)
    client.register_catalog_project("target")

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "target",
                "--title",
                "Explicit dependencies",
                "--body",
                "Depends-On: mine-py#proposal:44\n\nUse the API.",
                "--depends-on",
                "mine-py#42 mine-py#issue:43",
                "--json",
            ]
        )
        == 0
    )

    sent = json.loads(capsys.readouterr().out)
    assert sent["depends_on"] == [
        "mine-py#42",
        "mine-py#issue:43",
        "mine-py#proposal:44",
    ]
    assert client.calls[0]["body"]["depends_on"] == sent["depends_on"]


def test_api_cli_propose_warns_for_unreferenced_upstream_dependency(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'mine-js-monorepo'\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_workspace_refs(tmp_path / "issuekit.workspace.toml", "mine-js-monorepo", "mine-py")
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)
    client.register_catalog_project("dashboard-ui")

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "dashboard-ui",
                "--title",
                "Dashboard follow-up",
                "--body",
                "This depends on mine-py adding the dashboard API.",
                "--json",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    sent = json.loads(captured.out)

    assert "Dependency preflight" in captured.err
    assert "mine-py" in captured.err
    assert sent["warnings"] == [
        "Dependency preflight: proposal body appears to depend on mine-py, "
        "but no upstream reference was supplied. Create or propose the "
        "upstream owner work first, then pass "
        "`--depends-on <project#proposal:N>` or add a "
        "`Depends-On: <project#proposal:N>` body line. Use explicit "
        "project#issue:N or project#proposal:N refs when both could exist."
    ]
    assert "depends_on" not in client.calls[0]["body"]


def test_api_cli_propose_warns_instead_of_rejecting_freeform_dependency_line(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'mine-js-monorepo'\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_workspace_refs(tmp_path / "issuekit.workspace.toml", "mine-js-monorepo", "mine-py")
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)
    client.register_catalog_project("dashboard-ui")

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "dashboard-ui",
                "--title",
                "Dashboard follow-up",
                "--body",
                "Depends on: mine-py adding the dashboard API.",
            ]
        )
        == 0
    )

    assert "Dependency preflight" in capsys.readouterr().err
    assert "depends_on" not in client.calls[0]["body"]


def test_api_cli_propose_does_not_warn_for_target_owned_query_param_contract(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    client.register_catalog_project("mine-py")
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'mine-js-monorepo'\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_workspace_refs(tmp_path / "issuekit.workspace.toml", "mine-js-monorepo", "mine-py")
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "mine-py",
                "--title",
                "Define API contract",
                "--body",
                "This depends on the final endpoint path and query-param contract owned here.",
                "--json",
            ]
        )
        == 0
    )
    sent = json.loads(capsys.readouterr().out)

    assert "warnings" not in sent
    assert "depends_on" not in client.calls[0]["body"]


def test_api_cli_propose_warns_for_self_target_without_reply(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    client.register_catalog_project("source")
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["propose", "--to", "source", "--title", "Local", "--body", "Body."]) == 0

    captured = capsys.readouterr()
    assert "Dependency ref: source#proposal:1" in captured.out
    assert "Self-target proposal preflight" in captured.err


def test_api_cli_propose_rejects_invalid_dependency_ref(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "target",
                "--title",
                "Bad dependency",
                "--body",
                "Body.",
                "--depends-on",
                "mine-py",
            ]
        )
        == 1
    )

    assert "Invalid dependency reference" in capsys.readouterr().err


def test_api_cli_propose_rejects_malformed_dependency_prefix(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "target",
                "--title",
                "Bad dependency",
                "--body",
                "Body.",
                "--depends-on",
                "mine-py#foo:42",
            ]
        )
        == 1
    )

    err = capsys.readouterr().err
    assert "Invalid dependency reference: mine-py#foo:42" in err
    assert "project#N, project#issue:N, or project#proposal:N" in err


def test_api_cli_propose_warns_for_bare_ref_collision(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class CollisionClient(FakeIssuekitClient):
        def create_proposal(self, **kwargs):
            proposal = super().create_proposal(**kwargs)
            proposal["dependencies"] = [
                {
                    "ref": "mine-py#42",
                    "state": "attention",
                    "issue_status": "completed",
                    "proposal": {"id": 42, "status": "pending"},
                }
            ]
            return proposal

    client = CollisionClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)
    client.register_catalog_project("target")

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "target",
                "--title",
                "Collision",
                "--body",
                "Body.",
                "--depends-on",
                "mine-py#42",
                "--json",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    sent = json.loads(captured.out)
    assert sent["warnings"] == [
        "Dependency reference mine-py#42 is ambiguous: the API found both an issue "
        "and a proposal. Use mine-py#issue:42 or mine-py#proposal:42; for pending "
        "proposals prefer mine-py#proposal:42."
    ]
    assert "Dependency reference mine-py#42 is ambiguous" in captured.err


def test_api_cli_propose_rejects_non_ascii_body(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    # No client mock: the ASCII check must fail fast in build_proposal, before
    # any API call, so a missing client would not matter.
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "target",
                "--title",
                "Clean title",
                "--body",
                "Body with an em dash — here.",
            ]
        )
        == 1
    )

    err = capsys.readouterr().err
    assert "--title/--body must be ASCII-only." in err
    assert "em dashes" in err


def test_api_cli_propose_rejects_non_ascii_title(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "propose",
                "--to",
                "target",
                "--title",
                "Cur“ly” quotes",
                "--body",
                "Clean body.",
            ]
        )
        == 1
    )

    assert "--title/--body must be ASCII-only." in capsys.readouterr().err


def test_api_cli_propose_from_issue_reads_api_store(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class NoListClient(FakeIssuekitClient):
        def list_all_issues(self, *args, **kwargs):
            raise AssertionError("build_proposal should fetch the source issue directly")

    client = NoListClient(
        issues=[
            api_issue(
                7,
                "Source Issue",
                body="# Issue #7: Source Issue\n\n## Suggested Change\n\nFrom API.",
            )
        ]
    )
    client.register_catalog_project("target")
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.setattr("issuekit.store.IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["propose", "--to", "target", "--from-issue", "7", "--json"]) == 0
    sent = json.loads(capsys.readouterr().out)

    assert sent["origin"] == "source#7@unknown"
    assert sent["title"] == "Source Issue"
    assert sent["body"] == "# Issue #7: Source Issue\n\n## Suggested Change\n\nFrom API."


def test_api_cli_propose_same_origin_payload_mismatch_fails(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "source#0@unknown",
                "title": "Old title",
                "body": "Old body.",
                "status": "pending",
            }
        ]
    )
    client.register_catalog_project("target")
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    argv = ["propose", "--to", "target", "--title", "New title", "--body", "New body."]
    assert cli.main([*argv, "--json"]) == 1
    captured = capsys.readouterr()
    output = json.loads(captured.out)

    assert output["id"] == 1
    assert output["title"] == "Old title"
    assert output["idempotent_existing"] is True
    assert output["payload_mismatch"] is True
    assert output["payload_mismatch_fields"] == ["title", "body"]
    assert "--from-issue" in captured.err
    assert "source#0@unknown" in captured.err

    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert "Sent proposal" not in captured.out
    assert "--from-issue" in captured.err


def test_api_cli_outgoing_lists_own_proposals(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {"id": 1, "origin": "source#1@abc", "title": "Mine pending", "body": "b", "status": "pending"},
            {"id": 2, "origin": "other#1@abc", "title": "Not mine", "body": "b", "status": "pending"},
            {
                "id": 3,
                "origin": "source#2@abc",
                "title": "Mine adopted",
                "body": "b",
                "status": "adopted",
                "adopted_issue_number": 42,
            },
        ]
    )
    created_projects: list[str] = []

    def fake_client(*args, **kwargs):
        created_projects.append(kwargs["project"])
        return client

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", fake_client)
    monkeypatch.chdir(tmp_path)
    client.register_catalog_project("target")

    assert cli.main(["outgoing", "--to", "target", "--json"]) == 0
    outgoing = json.loads(capsys.readouterr().out)
    assert [proposal["id"] for proposal in outgoing] == [1, 3]
    assert set(created_projects) == {"source", "target"}

    assert cli.main(["outgoing", "--to", "target", "--status", "adopted", "--json"]) == 0
    adopted = json.loads(capsys.readouterr().out)
    assert [proposal["id"] for proposal in adopted] == [3]

    assert cli.main(["outgoing", "--to", "target", "--id", "3", "--json"]) == 0
    single = json.loads(capsys.readouterr().out)
    assert [proposal["id"] for proposal in single] == [3]

    assert cli.main(["outgoing", "--to", "target"]) == 0
    text = capsys.readouterr().out
    assert "target#42" in text
    assert "Mine pending" in text
    assert "Not mine" not in text


def test_api_cli_outgoing_rejects_foreign_and_invalid_lookups(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {"id": 2, "origin": "other#1@abc", "title": "Not mine", "body": "b", "status": "pending"},
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)
    client.register_catalog_project("target")

    assert cli.main(["outgoing", "--to", "target", "--id", "2"]) == 1
    assert "was not sent by source" in capsys.readouterr().err

    assert cli.main(["outgoing", "--to", "target", "--status", "bogus"]) == 1
    assert "Invalid proposal status" in capsys.readouterr().err


def test_api_cli_incoming_lists_pending_large_inbox(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": proposal_id,
                "origin": f"source#{proposal_id}@abc123",
                "title": f"Proposal {proposal_id}",
                "body": "Body",
                "status": "pending",
            }
            for proposal_id in range(1, 121)
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["incoming", "--json"]) == 0
    incoming = json.loads(capsys.readouterr().out)

    assert len(incoming) == 120
    assert incoming[0]["id"] == 1
    assert incoming[-1]["id"] == 120


def test_api_cli_adopt_and_discard_use_proposal_ids(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {"id": 1, "origin": "source#1@abc123", "title": "Adopt", "body": "Adopt body."},
            {"id": 2, "origin": "source#2@abc123", "title": "Discard", "body": "Discard body."},
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)
    append_file = tmp_path / "plan.md"
    append_file.write_text("## Implementation Plan\n\nDo this.\n", encoding="utf-8", newline="\n")

    assert cli.main(["adopt", "1", "--priority", "high", "--append-file", str(append_file), "--json"]) == 0
    adopted = json.loads(capsys.readouterr().out)
    assert cli.main(["discard", "2", "--json"]) == 0
    discarded = json.loads(capsys.readouterr().out)

    assert adopted["title"] == "Adopt"
    assert adopted["priority"] == "high"
    assert adopted["api_result"] == "created_issue"
    assert adopted["created_api_issue"] is True
    assert adopted["proposal_id"] == "1"
    assert adopted["issue_id"] == 1
    assert adopted["issue_ref"] == "target#1"
    assert adopted["next_command"] == "issuekit claim --id 1 --assignee <agent>"
    assert adopted["issue"]["title"] == "Adopt"
    assert adopted["issue"]["body"] == "Adopt body.\n\n## Implementation Plan\n\nDo this."
    assert client.get_proposal(1)["status"] == "adopted"
    assert client.get_issue(1)["body"] == "Adopt body.\n\n## Implementation Plan\n\nDo this."
    assert discarded["status"] == "discarded"
    assert client.get_proposal(2)["status"] == "discarded"


def test_api_cli_adopt_normal_output_includes_next_step(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {"id": 1, "origin": "source#1@abc123", "title": "Adopt", "body": "Adopt body."},
        ]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["adopt", "1", "--priority", "high"]) == 0

    out = capsys.readouterr().out
    assert "Adopted proposal #1 as API issue #1 (target#1)." in out
    assert "Next: issuekit claim --id 1 --assignee <agent>" in out


def test_auto_adopt_incoming_proposals_filters_policy_and_caps(monkeypatch) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "source#1@abc123",
                "title": "Blocking",
                "body": "Blocking body.",
                "blocking": True,
            },
            {
                "id": 2,
                "origin": "source#2@abc123",
                "title": "Not blocking",
                "body": "Body.",
                "blocking": False,
            },
            {
                "id": 3,
                "origin": "other#1@abc123",
                "title": "Foreign",
                "body": "Body.",
                "blocking": True,
            },
            {
                "id": 4,
                "origin": "source#3@abc123",
                "title": "Second blocking",
                "body": "Body.",
                "blocking": True,
            },
        ]
    )
    config = IssuekitConfig(
        api_url="https://mine.example",
        project="target",
        triage=TriagePolicy(
            trusted_origins=("source",),
            default_priority="high",
            require_blocking=True,
            max_adoptions_per_cycle=1,
        ),
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)

    adopted = proposals_api.auto_adopt_incoming_proposals(config)

    assert [item["proposal_id"] for item in adopted] == ["1"]
    assert adopted[0]["auto_adopted"] is True
    assert adopted[0]["blocking"] is True
    assert client.get_proposal(1)["status"] == "adopted"
    assert client.get_issue(1)["priority"] == "high"
    assert client.get_issue(1)["origin_proposal_id"] == "1"
    assert client.get_proposal(2)["status"] == "pending"
    assert client.get_proposal(3)["status"] == "pending"
    assert client.get_proposal(4)["status"] == "pending"


def test_auto_adopt_incoming_proposals_does_not_discard_superseded_refs(
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "source#1@abc123",
                "title": "Original",
                "body": "Original body.",
                "blocking": True,
            },
            {
                "id": 2,
                "origin": "source#2@def456",
                "title": "Amended",
                "body": "Amended body.\n\nSupersedes: target#1",
                "blocking": True,
            },
        ]
    )
    config = IssuekitConfig(
        api_url="https://mine.example",
        project="target",
        triage=TriagePolicy(
            trusted_origins=("source",),
            require_blocking=True,
            max_adoptions_per_cycle=2,
        ),
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)

    adopted = proposals_api.auto_adopt_incoming_proposals(config)

    assert [item["proposal_id"] for item in adopted] == ["1", "2"]
    assert client.get_proposal(1)["status"] == "adopted"
    assert client.get_proposal(2)["status"] == "adopted"
    assert [call["method"] for call in client.calls] == [
        "adopt_proposal",
        "adopt_proposal",
    ]


def test_api_cli_adopt_requires_integer_id(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["adopt", "proposal.md"]) == 1

    assert "Proposal id must be an integer" in capsys.readouterr().err


def test_proposal_commands_require_api_url(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    assert cli.main(["incoming"]) == 1

    assert "Proposal commands require api_url" in capsys.readouterr().err


def test_invalid_origin_destination_raises() -> None:
    try:
        origin_destination("not-an-origin")
    except ProposalError as exc:
        assert "Invalid proposal origin" in str(exc)
    else:
        raise AssertionError("expected ProposalError")


def test_git_commit_timeout_returns_unknown(tmp_path: Path, monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        assert kwargs["timeout"] == 5
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _git_commit(tmp_path) == "unknown"


def test_git_commit_redirects_stdin(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _git_commit(tmp_path) == "abc123"
    assert captured["stdin"] == subprocess.DEVNULL
