from collections.abc import Iterator
import os
from pathlib import Path

import pytest

from issuekit.config import (
    AgentRunConfig,
    IssuekitConfig,
    RoleOverlay,
    TriagePolicy,
    WorkerIdentity,
    load_config,
)


_ENV_KEYS = (
    "ISSUEKIT_API_PASSWORD",
    "ISSUEKIT_API_TIMEOUT",
    "ISSUEKIT_API_TOKEN",
    "ISSUEKIT_API_URL",
    "ISSUEKIT_API_USER",
    "ISSUEKIT_ENFORCE_AUTHOR_HANDOFF",
    "ISSUEKIT_PROJECT",
    "ISSUEKIT_TOKEN_CACHE",
    "DOTENV_EXTRA",
    "MALFORMED_LINE",
)


@pytest.fixture(autouse=True)
def restore_config_env() -> Iterator[None]:
    original = {key: os.environ.get(key) for key in _ENV_KEYS}
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
    yield
    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_load_config_reads_standalone_issuekit_toml(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "ascii_id_threshold = 407\nissues_dir = 'docs/issues'\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.ascii_id_threshold == 407
    assert config.issues_dir == "docs/issues"


def test_load_config_reads_gate_halfwidth_kana(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "gate_halfwidth_kana = false\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path).gate_halfwidth_kana is False


def test_load_config_reads_check_encoding_exclude(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "check_encoding_exclude = ['packages/*/src/generated/**']\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path).check_encoding_exclude == (
        "packages/*/src/generated/**",
    )


def test_load_config_reads_send_agent_runtime(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "send_agent_runtime = false\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path).send_agent_runtime is False


def test_load_config_reads_machine_config(tmp_path: Path, monkeypatch) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text("issues_dir = 'machine/issues'\n", encoding="utf-8")
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    config = load_config(tmp_path)

    assert config.issues_dir == "machine/issues"
    assert config.machine_config_path == machine_path


def test_repo_config_overrides_machine_and_merges_agent_keys(
    tmp_path: Path, monkeypatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        (
            "issues_dir = 'machine/issues'\n[agents.codex]\n"
            "model = 'machine-model'\nreasoning_effort = 'medium'\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "issuekit.toml").write_text(
        "issues_dir = 'repo/issues'\n[agents.codex]\napproval_flag = '--full-auto'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    config = load_config(tmp_path)
    codex = dict(config.agents)["codex"]

    assert config.issues_dir == "repo/issues"
    assert codex.model == "machine-model"
    assert codex.reasoning_effort == "medium"
    assert codex.approval_flag == "--full-auto"


def test_default_implementer_uses_machine_config_unless_repo_overrides(
    tmp_path: Path, monkeypatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "assignees = ['codex', 'claude']\ndefault_implementer = 'claude'\n",
        encoding="utf-8",
    )
    (tmp_path / "issuekit.toml").write_text(
        "default_implementer = 'codex'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    assert load_config(tmp_path).default_implementer == "codex"


def test_empty_issuekit_config_disables_machine_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ISSUEKIT_CONFIG", "")

    assert load_config(tmp_path).machine_config_path is None


def test_machine_config_rejects_worker(tmp_path: Path, monkeypatch) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text("[worker]\nworker_id = 'shared'\n", encoding="utf-8")
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    with pytest.raises(ValueError, match="cannot define worker"):
        load_config(tmp_path)


def test_default_machine_config_path_uses_xdg_config_home(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ISSUEKIT_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from issuekit.config import resolve_machine_config_path

    assert resolve_machine_config_path() == tmp_path / "issuekit" / "config.toml"


def test_default_machine_config_path_defaults_to_home_config_dir(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ISSUEKIT_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from issuekit.config import resolve_machine_config_path

    assert resolve_machine_config_path() == tmp_path / ".config" / "issuekit" / "config.toml"


def test_load_config_prefers_pyproject_tool_issuekit(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.issuekit]\nascii_id_threshold = 100\nissues_dir = 'py/issues'\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.toml").write_text(
        "ascii_id_threshold = 407\nissues_dir = 'standalone/issues'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        ascii_id_threshold=100,
        issues_dir="py/issues",
    )


def test_load_config_reads_api_fields_from_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        (
            "[tool.issuekit]\n"
            "api_url = 'https://mine.example'\n"
            "project = 'demo_project'\n"
            "api_timeout = 12.5\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.api_url == "https://mine.example"
    assert config.project == "demo_project"
    assert config.api_timeout == 12.5
    assert config.default_reviewer == "auto"
    assert config.require_distinct_reviewer is True


def test_load_config_reads_work_branch(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "work_branch = 'main'\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.work_branch == "main"


def test_load_config_defaults_work_branch_to_empty(tmp_path: Path) -> None:
    assert load_config(tmp_path).work_branch == ""


def test_load_config_reads_claim_sync_options(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "claim_sync = false\nclaim_sync_interval_sec = 10.5\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.claim_sync is False
    assert config.claim_sync_interval_sec == 10.5


def test_load_config_defaults_claim_sync_on(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    assert config.claim_sync is True
    assert config.claim_sync_interval_sec == 60.0


def test_load_config_rejects_negative_claim_sync_interval(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "claim_sync_interval_sec = -1\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="claim_sync_interval_sec"):
        load_config(tmp_path)


@pytest.mark.parametrize("value", ["feature branch", "main\u3042"])
def test_load_config_rejects_invalid_work_branch(tmp_path: Path, value: str) -> None:
    (tmp_path / "issuekit.toml").write_text(
        f"work_branch = '{value}'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="Invalid work_branch token"):
        load_config(tmp_path)


def test_load_config_reads_triage_policy(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "[triage]\n"
            "auto_adopt = true\n"
            "trusted_origins = ['frontend', 'api_worker']\n"
            "default_priority = 'high'\n"
            "require_blocking = true\n"
            "max_adoptions_per_cycle = 2\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.triage == TriagePolicy(
        auto_adopt=True,
        trusted_origins=("frontend", "api_worker"),
        default_priority="high",
        require_blocking=True,
        max_adoptions_per_cycle=2,
    )


def test_load_config_reads_triage_author_agent(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "[triage]\n"
            "author_agent = 'codex'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.triage.author_agent == "codex"


def test_load_config_defaults_triage_author_agent_to_empty(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    assert config.triage.author_agent == ""


def test_load_config_rejects_invalid_triage_author_agent(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "[triage]\n"
            "author_agent = 'bad agent'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="triage.author_agent"):
        load_config(tmp_path)


def test_load_config_reads_project_profile_metadata(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "profile_file = 'PROFILE.md'\n"
            "profile_summary = 'Workflow CLI over the mine-py API.'\n"
            "profile_tags = ['python', 'cli', 'workflow']\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.profile_file == "PROFILE.md"
    assert config.profile_summary == "Workflow CLI over the mine-py API."
    assert config.profile_tags == ("python", "cli", "workflow")


def test_load_config_defaults_project_profile_metadata(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    assert config.profile_file == "ISSUEKIT.md"
    assert config.profile_summary == ""
    assert config.profile_tags == ()


def test_load_config_rejects_overlong_profile_summary(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        f"profile_summary = '{'x' * 501}'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="profile_summary"):
        load_config(tmp_path)


def test_load_config_rejects_too_many_profile_tags(tmp_path: Path) -> None:
    tags = ", ".join(f"'tag{i}'" for i in range(21))
    (tmp_path / "issuekit.toml").write_text(
        f"profile_tags = [{tags}]\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="profile_tags"):
        load_config(tmp_path)


def test_load_config_rejects_invalid_profile_tag_token(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "profile_tags = ['Bad Tag']\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="profile_tags"):
        load_config(tmp_path)


def test_load_config_reads_worker_role_and_description(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "worker_role = 'api-server'\n"
            "worker_description = 'Hosts the mine-py issue API.'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.worker_role == "api-server"
    assert config.worker_description == "Hosts the mine-py issue API."


def test_load_config_defaults_worker_metadata_to_empty(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    assert config.worker_role == ""
    assert config.worker_description == ""
    assert config.worker_accept_directed is False


def test_load_config_reads_worker_accept_directed(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "worker_accept_directed = true\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.worker_accept_directed is True


def test_load_config_rejects_overlong_worker_role(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        f"worker_role = '{'x' * 81}'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="worker_role"):
        load_config(tmp_path)


def test_load_config_rejects_overlong_worker_description(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        f"worker_description = '{'x' * 501}'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="worker_description"):
        load_config(tmp_path)


def test_load_config_rejects_invalid_triage_policy(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "[triage]\n"
            "trusted_origins = ['bad origin']\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="triage.trusted_origins"):
        load_config(tmp_path)


def test_load_config_reads_api_url_from_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ISSUEKIT_API_URL", raising=False)
    (tmp_path / ".env").write_text(
        "ISSUEKIT_API_URL=https://mine.env\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.api_url == "https://mine.env"
    assert config.api_url == "https://mine.env"


def test_load_config_real_environment_overrides_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ISSUEKIT_API_URL", "https://mine.real-env")
    (tmp_path / ".env").write_text(
        "ISSUEKIT_API_URL=https://mine.dotenv\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.api_url == "https://mine.real-env"
    assert capsys.readouterr().err == ""


def test_load_config_dotenv_parses_comments_quotes_export_and_skips_malformed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "ISSUEKIT_API_URL",
        "ISSUEKIT_PROJECT",
        "ISSUEKIT_API_TIMEOUT",
        "DOTENV_EXTRA",
        "MALFORMED_LINE",
    ):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        (
            "\n"
            "  # comment\n"
            "export ISSUEKIT_API_URL = 'https://mine.quoted'\n"
            'ISSUEKIT_PROJECT = "quoted_project"\n'
            "ISSUEKIT_API_TIMEOUT = 7.5\n"
            "DOTENV_EXTRA = extra value\n"
            "MALFORMED_LINE\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.api_url == "https://mine.quoted"
    assert config.project == "quoted_project"
    assert config.api_timeout == 7.5
    assert "DOTENV_EXTRA" not in os.environ
    assert "MALFORMED_LINE" not in os.environ


def test_load_config_dotenv_warns_when_sensitive_api_key_is_loaded(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "ISSUEKIT_API_TOKEN=repo-token\nISSUEKIT_PROJECT=demo\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.project == "demo"
    assert os.environ["ISSUEKIT_API_TOKEN"] == "repo-token"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(dotenv_path) in captured.err
    assert "ISSUEKIT_API_TOKEN" in captured.err


def test_load_config_missing_dotenv_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ISSUEKIT_API_URL", raising=False)

    config = load_config(tmp_path)

    assert config == IssuekitConfig()


def test_load_config_api_mode_uses_server_reviewer_policy(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "api_url = 'https://mine.example'\n"
            "default_reviewer = 'claude'\n"
            "require_distinct_reviewer = false\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.default_reviewer == "auto"
    assert config.require_distinct_reviewer is True


def test_load_config_rejects_invalid_project_token(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "project = 'bad value'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="Invalid project token"):
        load_config(tmp_path)


def test_load_config_uses_issuekit_toml_when_pyproject_has_no_issuekit_table(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'example'\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.toml").write_text(
        "ascii_id_threshold = 407\nissues_dir = 'standalone/issues'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        ascii_id_threshold=407,
        issues_dir="standalone/issues",
    )


def test_load_config_uses_defaults_without_config_files(tmp_path: Path) -> None:
    assert load_config(tmp_path) == IssuekitConfig()


def test_default_assignees_includes_kimi() -> None:
    assert "kimi" in IssuekitConfig.assignees


def test_load_config_filters_disabled_agents_from_assignees_and_agents(
    tmp_path: Path,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "disabled_agents = ['kimi']\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.disabled_agents == ("kimi",)
    assert config.assignees == ("codex", "claude")
    assert tuple(dict(config.agents)) == ("codex", "claude")


def test_load_config_local_disabled_agents_override_committed_config(
    tmp_path: Path,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "disabled_agents = ['kimi']\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.local.toml").write_text(
        (
            "disabled_agents = []\n"
            "\n"
            "[refs]\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.disabled_agents == ()
    assert "kimi" in config.assignees
    assert "kimi" in dict(config.agents)


def test_load_config_defaults_assignees_to_enabled_agent_names(
    tmp_path: Path,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "disabled_agents = ['kimi']\n"
            "[agents.custom]\n"
            "binary = 'custom-agent'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.assignees == ("codex", "claude", "custom")


def test_load_config_explicit_assignees_override_enabled_agent_names(
    tmp_path: Path,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "disabled_agents = ['kimi']\n"
            "assignees = ['human', 'kimi', 'codex']\n"
            "default_reviewer = 'human'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.assignees == ("human", "codex")


def test_load_config_rejects_invalid_disabled_agent_token(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "disabled_agents = ['bad agent']\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="Invalid disabled_agents token"):
        load_config(tmp_path)


def test_load_config_reads_agent_roles(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agent_roles]\nclaude = 'implementer'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path).agent_roles == {"claude": "implementer"}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[agent_roles]\nclaude = 'invalid'\n", "Invalid agent_roles role"),
        ("[agent_roles]\n'bad agent' = 'implementer'\n", "Invalid agent_roles token"),
    ],
)
def test_load_config_rejects_invalid_agent_roles(
    tmp_path: Path, body: str, message: str
) -> None:
    (tmp_path / "issuekit.toml").write_text(body, encoding="utf-8", newline="\n")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "disabled_agents = ['claude']\ndefault_reviewer = 'claude'\n",
            "default_reviewer references disabled agent: claude",
        ),
        (
            "disabled_agents = ['codex']\n[router]\nagent = 'codex'\n",
            "router.agent references disabled agent: codex",
        ),
        (
            "disabled_agents = ['codex']\n[triage]\nauthor_agent = 'codex'\n",
            "triage.author_agent references disabled agent: codex",
        ),
    ],
)
def test_load_config_rejects_disabled_agent_policy_references(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        body,
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_default_stages_match_server_vocabulary() -> None:
    assert IssuekitConfig.stages == (
        "planned",
        "todo",
        "implementing",
        "review",
        "changes_requested",
        "done",
    )


def test_config_worker_key_returns_registered_identity() -> None:
    assert IssuekitConfig().worker_key() is None
    assert IssuekitConfig().qualified_worker_key() is None
    config = IssuekitConfig(worker=WorkerIdentity("machine", "repo", "checkout"))

    assert config.worker_key() == "checkout.repo"
    assert config.qualified_worker_key() == "checkout.repo@machine"
    assert config.worker_lookup_keys() == (
        "checkout.repo@machine",
        "checkout.repo",
    )


def test_load_config_malformed_issuekit_toml_names_file(tmp_path: Path) -> None:
    issuekit_path = tmp_path / "issuekit.toml"
    issuekit_path.write_text(
        "ascii_id_threshold = [\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match=r"issuekit\.toml"):
        load_config(tmp_path)


def test_load_config_reads_workflow_sets_from_issuekit_toml(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "assignees = ['alice', 'bob']\n"
            "stages = ['draft', 'review']\n"
            "default_reviewer = 'bob'\n"
            "require_distinct_reviewer = true\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        assignees=("alice", "bob"),
        stages=("draft", "review"),
        default_reviewer="bob",
        require_distinct_reviewer=True,
    )


def test_load_config_accepts_auto_default_reviewer(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "assignees = ['alice', 'bob']\ndefault_reviewer = 'auto'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        assignees=("alice", "bob"),
        default_reviewer="auto",
    )


def test_load_config_coerces_string_distinct_reviewer_flag(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "require_distinct_reviewer = 'yes'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path).require_distinct_reviewer is True


def test_load_config_rejects_invalid_default_reviewer_token(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'bad value'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="Invalid default_reviewer token"):
        load_config(tmp_path)


def test_load_config_rejects_unknown_default_reviewer(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "assignees = ['alice', 'bob']\ndefault_reviewer = 'claude'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="Unknown default_reviewer"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("default_implementer = 'bad value'\n", "Invalid default_implementer token"),
        (
            "assignees = ['codex']\ndefault_reviewer = 'codex'\n"
            "default_implementer = 'claude'\n",
            "Unknown default_implementer",
        ),
        (
            "disabled_agents = ['codex']\ndefault_implementer = 'codex'\n",
            "default_implementer references disabled agent: codex",
        ),
    ],
)
def test_load_config_validates_default_implementer(
    tmp_path: Path, body: str, message: str
) -> None:
    (tmp_path / "issuekit.toml").write_text(body, encoding="utf-8", newline="\n")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_load_config_reads_agent_guardrail_fields(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "[agents.codex]\n"
            "binary = 'codex'\n"
            "headless_argv = ['exec']\n"
            "model_flag = '--model'\n"
            "model = 'gpt-5.3-codex-spark'\n"
            "prompt_suffix = 'Keep diffs small.'\n"
            "resumable = true\n"
            "session_flag = '--session-id'\n"
            "mojibake_gate = true\n"
            "diff_shape_warn_deletions = 12\n"
            "[agents.codex.model_prompts]\n"
            "'gpt-5.3-codex-spark' = 'Spark-only guardrail.'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)
    codex = dict(config.agents)["codex"]

    assert codex == AgentRunConfig(
        binary="codex",
        known_paths=(
            "~/.codex/.sandbox-bin/codex",
            "~/.codex/.sandbox-bin/codex.exe",
        ),
        headless_argv=("exec",),
        approval_flag="--dangerously-bypass-approvals-and-sandbox",
        resumable=True,
        session_flag="--session-id",
        model_flag="--model",
        model="gpt-5.3-codex-spark",
        effort_argv=("-c", "model_reasoning_effort={value}"),
        prompt_suffix="Keep diffs small.",
        model_prompts=(("gpt-5.3-codex-spark", "Spark-only guardrail."),),
    )
    assert dict(config.agent_policies)["codex"].mojibake_gate is True
    assert dict(config.agent_policies)["codex"].diff_shape_warn_deletions == 12


def test_load_config_merges_builtin_agent_overrides(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "[agents.codex]\n"
            "approval_flag = '--sandbox'\n"
            "approval_value = 'danger-full-access'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)
    agents = dict(config.agents)
    codex_default = dict(IssuekitConfig.agents)["codex"]

    assert tuple(agents) == ("kimi", "codex", "claude")
    assert agents["codex"] == AgentRunConfig(
        binary=codex_default.binary,
        known_paths=codex_default.known_paths,
        headless_argv=codex_default.headless_argv,
        approval_flag="--sandbox",
        approval_value="danger-full-access",
        output_format_flag=codex_default.output_format_flag,
        output_format=codex_default.output_format,
        model_flag=codex_default.model_flag,
        model=codex_default.model,
        reasoning_effort=codex_default.reasoning_effort,
        effort_argv=codex_default.effort_argv,
        prompt_suffix=codex_default.prompt_suffix,
        model_prompts=codex_default.model_prompts,
    )
    assert agents["kimi"] == dict(IssuekitConfig.agents)["kimi"]
    assert agents["claude"] == dict(IssuekitConfig.agents)["claude"]


def test_load_config_reads_claude_reasoning_effort(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agents.claude]\nreasoning_effort = 'medium'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert dict(load_config(tmp_path).agents)["claude"].reasoning_effort == "medium"


def test_load_config_reads_reasoning_effort_without_effort_argv(
    tmp_path: Path,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agents.kimi]\nreasoning_effort = 'medium'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert dict(load_config(tmp_path).agents)["kimi"].reasoning_effort == "medium"


def test_load_config_reads_agent_role_overlays(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "[agents.claude]\nmodel = 'claude-sonnet-5'\n"
            "[agents.claude.roles.reviewer]\n"
            "model = 'claude-opus-4-8'\nreasoning_effort = 'high'\n"
            "[agents.claude.roles.router]\n"
            "model = 'claude-opus-4-8'\nreasoning_effort = 'high'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert dict(dict(config.agent_role_overlays)["claude"]) == {
        "reviewer": RoleOverlay(model="claude-opus-4-8", reasoning_effort="high"),
        "router": RoleOverlay(model="claude-opus-4-8", reasoning_effort="high"),
    }


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "[agents.claude.roles.invalid]\nmodel = 'claude'\n",
            "Invalid agents.claude.roles role: invalid",
        ),
        (
            "[agents.claude.roles.author]\nmodel = 'claude'\n",
            "supported roles: implementer, reviewer, router, triage",
        ),
        (
            "[agents.claude.roles.reviewer]\nbinary = 'claude'\n",
            "only supports model and reasoning_effort",
        ),
    ],
)
def test_load_config_validates_agent_role_overlays(
    tmp_path: Path, body: str, message: str
) -> None:
    (tmp_path / "issuekit.toml").write_text(body, encoding="utf-8", newline="\n")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_load_config_reads_role_reasoning_effort_without_effort_argv(
    tmp_path: Path,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agents.kimi.roles.reviewer]\nreasoning_effort = 'medium'\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert dict(dict(config.agent_role_overlays)["kimi"])["reviewer"] == RoleOverlay(
        reasoning_effort="medium"
    )


def test_load_config_honors_false_builtin_agent_override(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agents.codex]\nmojibake_gate = false\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)
    codex = dict(config.agents)["codex"]

    assert dict(config.agent_policies)["codex"].mojibake_gate is False
    assert codex.prompt_suffix == dict(IssuekitConfig.agents)["codex"].prompt_suffix


def test_load_config_empty_agent_string_clears_optional_default(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agents.codex]\napproval_flag = ''\n",
        encoding="utf-8",
        newline="\n",
    )

    codex = dict(load_config(tmp_path).agents)["codex"]

    assert codex.approval_flag is None


def test_shipped_codex_defaults_enable_guardrails() -> None:
    codex = dict(IssuekitConfig.agents)["codex"]
    policy = dict(IssuekitConfig.agent_policies)["codex"]

    assert codex.model is None
    assert codex.prompt_suffix is not None
    assert "minimal, additive diffs" in codex.prompt_suffix
    assert "mojibake" in codex.prompt_suffix
    assert policy.mojibake_gate is True
    assert policy.diff_shape_warn_deletions == 40


def test_shipped_kimi_defaults_do_not_enable_guardrails() -> None:
    kimi = dict(IssuekitConfig.agents)["kimi"]
    policy = dict(IssuekitConfig.agent_policies).get("kimi")

    assert kimi.prompt_suffix is None
    assert policy is None


def test_shipped_only_claude_defaults_enable_resumable_sessions() -> None:
    agents = dict(IssuekitConfig.agents)

    assert agents["claude"].resumable is True
    assert agents["claude"].session_flag == "--session-id"
    assert agents["codex"].resumable is False
    assert agents["codex"].session_flag is None
    assert agents["kimi"].resumable is False
    assert agents["kimi"].session_flag is None
