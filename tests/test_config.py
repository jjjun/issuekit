import os
import subprocess
import sys
from collections.abc import Iterator
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
from issuekit.config.settings import PROFILE_TAG_MAX_LEN

_ENV_KEYS = (
    "ISSUEKIT_API_PASSWORD",
    "ISSUEKIT_API_TIMEOUT",
    "ISSUEKIT_API_TOKEN",
    "ISSUEKIT_API_URL",
    "ISSUEKIT_API_USER",
    "ISSUEKIT_ALLOW_INSECURE",
    "ISSUEKIT_CONFIG",
    "ISSUEKIT_ENFORCE_AUTHOR_HANDOFF",
    "ISSUEKIT_PROJECT",
    "ISSUEKIT_SESSION",
    "ISSUEKIT_TOKEN_CACHE",
    "ISSUEKIT_WORKSPACE",
    "DOTENV_EXTRA",
    "MALFORMED_LINE",
)
_REPO_CONFIG_SOURCES = (
    ("issuekit.toml", "issuekit.toml", "[agents.codex]"),
    (
        "pyproject.toml",
        "pyproject [tool.issuekit]",
        "[tool.issuekit.agents.codex]",
    ),
)
_MACHINE_ONLY_AGENT_SETTINGS = (
    ("binary", "binary = 'codex'\n"),
    ("adapter", "adapter = 'codex'\n"),
    ("runtime", "runtime = 'exec'\n"),
    ("app_server_argv", "app_server_argv = ['app-server']\n"),
    ("lease_ttl_seconds", "lease_ttl_seconds = 60\n"),
    ("known_paths", "known_paths = ['/opt/codex']\n"),
    ("headless_argv", "headless_argv = ['exec']\n"),
    ("resumable", "resumable = true\n"),
    ("session_flag", "session_flag = '--session-id'\n"),
    ("resume_flag", "resume_flag = '--resume'\n"),
    ("approval_flag", "approval_flag = '--sandbox'\n"),
    ("approval_value", "approval_value = 'workspace-write'\n"),
    ("output_format_flag", "output_format_flag = '--output'\n"),
    ("output_format", "output_format = 'json'\n"),
    ("model_flag", "model_flag = '--model'\n"),
    ("effort_argv", "effort_argv = ['--effort', '{value}']\n"),
    ("speed_argv", "speed_argv = ['--fast']\n"),
)


@pytest.fixture(autouse=True)
def restore_config_env() -> Iterator[None]:
    original = {key: os.environ.get(key) for key in _ENV_KEYS}
    original_config = os.environ.get("ISSUEKIT_CONFIG")
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
    os.environ["ISSUEKIT_CONFIG"] = ""
    yield
    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    if original_config is None:
        os.environ.pop("ISSUEKIT_CONFIG", None)
    else:
        os.environ["ISSUEKIT_CONFIG"] = original_config


def test_load_config_reads_standalone_issuekit_toml(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "project = 'standalone-project'\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.project == "standalone-project"


def test_load_config_from_subdirectory_uses_repository_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "issuekit.toml").write_text(
        "project = 'repo-project'\n", encoding="utf-8", newline="\n"
    )
    (tmp_path / ".env").write_text(
        "ISSUEKIT_API_URL=https://mine.example\n", encoding="utf-8", newline="\n"
    )
    nested = tmp_path / "docs"
    nested.mkdir()
    monkeypatch.delenv("ISSUEKIT_API_URL", raising=False)

    config = load_config(nested)

    assert config.project == "repo-project"
    assert config.api_url == "https://mine.example"
    assert config.repo_config_source == "issuekit.toml"


def test_load_config_uses_nested_package_config_from_package_and_source(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    package_root = tmp_path / "pkg"
    source_root = package_root / "src"
    source_root.mkdir(parents=True)
    (package_root / "issuekit.toml").write_text(
        "project = 'pkg'\n", encoding="utf-8", newline="\n"
    )

    assert load_config(package_root).project == "pkg"
    assert load_config(source_root).project == "pkg"


def test_resolve_repository_root_returns_cwd_when_git_root_is_unrelated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    working_repo = tmp_path / "a"
    nested = working_repo / "sub"
    other_repo = tmp_path / "c"
    nested.mkdir(parents=True)
    other_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=working_repo, check=True)
    subprocess.run(["git", "init", "-q"], cwd=other_repo, check=True)
    monkeypatch.setenv("GIT_DIR", str(other_repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other_repo))

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from issuekit.config import resolve_repository_root; "
            "import sys; print(resolve_repository_root(sys.argv[1]))",
            str(nested),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert Path(result.stdout.strip()) == nested.resolve()


def test_empty_environment_values_fall_back_to_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'repo-project'\napi_timeout = 12.5\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_API_URL", "")
    monkeypatch.setenv("ISSUEKIT_PROJECT", "")
    monkeypatch.setenv("ISSUEKIT_API_TIMEOUT", "")
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "trusted_api_origins = ['https://mine.example']\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    config = load_config(tmp_path)

    assert config.api_url == "https://mine.example"
    assert config.project == "repo-project"
    assert config.api_timeout == 12.5


def test_empty_xdg_config_home_uses_home_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ISSUEKIT_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from issuekit.config import resolve_machine_config_path

    assert resolve_machine_config_path() == tmp_path / ".config" / "issuekit" / "config.toml"


def test_missing_explicit_machine_config_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_path = tmp_path / "missing-machine.toml"
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(missing_path))

    assert load_config(tmp_path).machine_config_path is None
    assert str(missing_path) in capsys.readouterr().err


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


def test_load_config_reads_machine_config(tmp_path: Path, monkeypatch) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text("project = 'machine-project'\n", encoding="utf-8")
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    config = load_config(tmp_path)

    assert config.project == "machine-project"
    assert config.machine_config_path == machine_path


def test_load_config_tracks_api_url_source_by_configuration_layer(
    tmp_path: Path, monkeypatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text("api_url = 'https://machine.example'\n", encoding="utf-8")
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    assert load_config(tmp_path).api_url_source == "machine_config"
    assert load_config(tmp_path).api_url_trusted_by == "machine_config"

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://repo.example'\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="https://repo.example.*https://machine.example"):
        load_config(tmp_path)

    machine_path.write_text(
        "api_url = 'https://machine.example'\n"
        "trusted_api_origins = ['https://repo.example/private/path']\n",
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config.api_url_source == "repo_config"
    assert config.api_url_trusted_by == "trusted_api_origins"
    assert config.trusted_api_origins == ("https://repo.example",)

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://repo.example'\ntrusted_api_origins = []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="trusted_api_origins can only be set in machine config"):
        load_config(tmp_path)

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://repo.example'\n", encoding="utf-8"
    )

    monkeypatch.setenv("ISSUEKIT_API_URL", "https://environment.example")
    config = load_config(tmp_path)
    assert config.api_url_source == "env"
    assert config.api_url_trusted_by == "env"


def test_repo_api_url_requires_a_trusted_origin(tmp_path: Path, monkeypatch) -> None:
    machine_path = tmp_path / "machine.toml"
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://repo.example'\n", encoding="utf-8"
    )

    with pytest.raises(ValueError) as excinfo:
        load_config(tmp_path)

    message = str(excinfo.value)
    assert "https://repo.example" in message
    assert "issuekit.toml" in message
    assert "trusted: none" in message
    assert str(machine_path) in message
    assert "ISSUEKIT_API_URL" in message


def test_repo_cannot_enable_insecure_api_url(tmp_path: Path, monkeypatch) -> None:
    machine_path = tmp_path / "machine.toml"
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    (tmp_path / "issuekit.toml").write_text(
        "allow_insecure_api_url = true\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="allow_insecure_api_url can only be set in machine config"):
        load_config(tmp_path)


def test_load_config_reads_machine_insecure_api_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text("allow_insecure_api_url = true\n", encoding="utf-8")
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    assert load_config(tmp_path).allow_insecure_api_url is True


def test_load_config_reports_unreadable_machine_config_clearly(
    tmp_path: Path, monkeypatch
) -> None:
    from issuekit.config import settings

    machine_path = tmp_path / "machine.toml"
    machine_path.write_text("api_url = 'https://private.example'\n", encoding="utf-8")
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    def raise_permission_error(_path: Path) -> dict[str, object]:
        raise PermissionError(13, "Permission denied", str(machine_path))

    monkeypatch.setattr(settings, "_load_config_toml", raise_permission_error)

    with pytest.raises(ValueError, match="Cannot read machine config") as excinfo:
        load_config(tmp_path)

    assert str(machine_path) in str(excinfo.value)
    assert "sandboxed process may be denied access" in str(excinfo.value)
    assert "private.example" not in str(excinfo.value)


def test_repo_config_overrides_machine_and_merges_agent_keys(
    tmp_path: Path, monkeypatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        (
            "project = 'machine-project'\n[agents.codex]\n"
            "model = 'machine-model'\nreasoning_effort = 'medium'\n"
            "speed = 'on'\nspeed_argv = ['--speed', 'priority']\n"
            "approval_flag = '--approve-for-me'\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "issuekit.toml").write_text(
        "project = 'repo-project'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    config = load_config(tmp_path)
    codex = dict(config.agents)["codex"]

    assert config.project == "repo-project"
    assert codex.model == "machine-model"
    assert codex.reasoning_effort == "medium"
    assert codex.speed is True
    assert codex.speed_argv == ("--speed", "priority")
    assert codex.approval_flag == "--approve-for-me"


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


def test_machine_config_ignores_newer_role_overlay(tmp_path: Path, monkeypatch) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "[agents.claude.roles.router]\nmodel = 'claude-opus-4-8'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    monkeypatch.setattr(
        "issuekit.config.settings.ROLE_OVERLAY_ROLES",
        frozenset({"implementer", "reviewer", "triage"}),
    )

    with pytest.warns(UserWarning, match="agents.claude.roles.router"):
        config = load_config(tmp_path)

    assert "claude" not in dict(config.agent_role_overlays)


def test_repo_config_rejects_unknown_role_overlay(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agents.claude.roles.router]\nmodel = 'claude-opus-4-8'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(
        "issuekit.config.settings.ROLE_OVERLAY_ROLES",
        frozenset({"implementer", "reviewer", "triage"}),
    )

    with pytest.raises(ValueError, match="Invalid agents.claude.roles role: router"):
        load_config(tmp_path)


def test_machine_config_ignores_unknown_top_level_and_agent_settings(
    tmp_path: Path, monkeypatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "unknown = 'value'\ndefault_reviewer = 'claude'\n"
        "[agents.claude]\nunknown = 'value'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    with pytest.warns(UserWarning) as warnings_record:
        config = load_config(tmp_path)

    assert [str(warning.message) for warning in warnings_record] == [
        f"Ignoring unsupported machine config setting unknown in {machine_path}.",
        f"Ignoring unsupported machine config setting default_reviewer in {machine_path}.",
        f"Ignoring unsupported machine config setting agents.claude.unknown in {machine_path}.",
    ]
    assert dict(config.agents)["claude"] == dict(IssuekitConfig.agents)["claude"]


@pytest.mark.parametrize(("filename", "source", "agent_table"), _REPO_CONFIG_SOURCES)
@pytest.mark.parametrize(("key", "setting"), _MACHINE_ONLY_AGENT_SETTINGS)
def test_repo_config_rejects_machine_only_agent_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    source: str,
    agent_table: str,
    key: str,
    setting: str,
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text("", encoding="utf-8", newline="\n")
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    (tmp_path / filename).write_text(
        f"{agent_table}\n{setting}",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError) as excinfo:
        load_config(tmp_path)

    message = str(excinfo.value)
    assert source in message
    assert f"agents.codex.{key}" in message
    assert str(machine_path) in message
    assert "can only be set in machine config" in message


@pytest.mark.parametrize(("filename", "source", "agent_table"), _REPO_CONFIG_SOURCES)
def test_repo_config_rejects_role_approval_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    source: str,
    agent_table: str,
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text("", encoding="utf-8", newline="\n")
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    agent_prefix = agent_table.removesuffix("]")
    (tmp_path / filename).write_text(
        f"{agent_prefix}.roles.reviewer]\napproval_argv = ['--safe-mode']\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError) as excinfo:
        load_config(tmp_path)

    message = str(excinfo.value)
    assert source in message
    assert "agents.codex.roles.reviewer.approval_argv" in message
    assert str(machine_path) in message


@pytest.mark.parametrize(("filename", "source", "agent_table"), _REPO_CONFIG_SOURCES)
def test_repo_config_rejects_checkout_relative_binary_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    source: str,
    agent_table: str,
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text("", encoding="utf-8", newline="\n")
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    (tmp_path / filename).write_text(
        f"{agent_table}\nbinary = 'scripts/x'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError) as excinfo:
        load_config(tmp_path)

    assert source in str(excinfo.value)
    assert "agents.codex.binary" in str(excinfo.value)


@pytest.mark.parametrize(("filename", "source", "agent_table"), _REPO_CONFIG_SOURCES)
def test_repo_config_rejects_custom_agent_definitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    source: str,
    agent_table: str,
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text("", encoding="utf-8", newline="\n")
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    agent_prefix = agent_table.removesuffix("codex]")
    (tmp_path / filename).write_text(
        f"{agent_prefix}custom]\nmodel = 'custom-model'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError) as excinfo:
        load_config(tmp_path)

    message = str(excinfo.value)
    assert f"{source} defines agent 'custom'" in message
    assert "define new agents in machine config" in message
    assert str(machine_path) in message


@pytest.mark.parametrize(("filename", "source", "agent_table"), _REPO_CONFIG_SOURCES)
def test_repo_config_keeps_agent_model_and_policy_settings(
    tmp_path: Path,
    filename: str,
    source: str,
    agent_table: str,
) -> None:
    agent_prefix = agent_table.removesuffix("]")
    (tmp_path / filename).write_text(
        (
            f"{agent_table}\n"
            "model = 'gpt-6-sol'\n"
            "reasoning_effort = 'high'\n"
            "speed = true\n"
            "prompt_suffix = 'Use focused changes.'\n"
            "mojibake_gate = true\n"
            "diff_shape_warn_deletions = 12\n"
            f"{agent_prefix}.model_prompts]\n"
            "'gpt-6-sol' = 'Model guidance.'\n"
            f"{agent_prefix}.roles.reviewer]\n"
            "model = 'gpt-6-sol-review'\n"
            "reasoning_effort = 'medium'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)
    codex = dict(config.agents)["codex"]

    assert codex.model == "gpt-6-sol"
    assert codex.reasoning_effort == "high"
    assert codex.speed is True
    assert codex.prompt_suffix == "Use focused changes."
    assert codex.model_prompts == (("gpt-6-sol", "Model guidance."),)
    assert dict(config.agent_policies)["codex"].mojibake_gate is True
    assert dict(config.agent_policies)["codex"].diff_shape_warn_deletions == 12
    assert dict(dict(config.agent_role_overlays)["codex"])["reviewer"] == RoleOverlay(
        model="gpt-6-sol-review", reasoning_effort="medium"
    )


def test_machine_config_loads_agent_launch_settings(tmp_path: Path, monkeypatch) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "[agents.codex]\n"
        + "".join(setting for _key, setting in _MACHINE_ONLY_AGENT_SETTINGS),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    codex = dict(load_config(tmp_path).agents)["codex"]

    assert codex.binary == "codex"
    assert codex.adapter == "codex"
    assert codex.runtime == "exec"
    assert codex.app_server_argv == ("app-server",)
    assert codex.lease_ttl_seconds == 60
    assert codex.known_paths == ("/opt/codex",)
    assert codex.headless_argv == ("exec",)
    assert codex.resumable is True
    assert codex.session_flag == "--session-id"
    assert codex.resume_flag == "--resume"
    assert codex.approval_flag == "--sandbox"
    assert codex.approval_value == "workspace-write"
    assert codex.output_format_flag == "--output"
    assert codex.output_format == "json"
    assert codex.model_flag == "--model"
    assert codex.effort_argv == ("--effort", "{value}")
    assert codex.speed_argv == ("--fast",)


@pytest.mark.parametrize("binary", ["scripts/x", "scripts\\x"])
def test_machine_config_rejects_relative_agent_binary_paths(
    tmp_path: Path, monkeypatch, binary: str
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        f"[agents.codex]\nbinary = '{binary}'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    with pytest.raises(ValueError, match="binary must be a bare command name"):
        load_config(tmp_path)


@pytest.mark.parametrize("path", ["scripts/x", "scripts\\x"])
def test_machine_config_rejects_relative_known_agent_paths(
    tmp_path: Path, monkeypatch, path: str
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        f"[agents.codex]\nknown_paths = ['{path}']\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    with pytest.raises(ValueError, match="known_paths must be a bare command name"):
        load_config(tmp_path)


@pytest.mark.parametrize("binary", ["codex", "/opt/codex/bin/codex"])
def test_machine_config_accepts_bare_or_absolute_agent_binary(
    tmp_path: Path, monkeypatch, binary: str
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        f"[agents.codex]\nbinary = '{binary}'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    assert dict(load_config(tmp_path).agents)["codex"].binary == binary


def test_machine_config_expands_user_home_agent_binary(
    tmp_path: Path, monkeypatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "[agents.codex]\nbinary = '~/codex/bin/codex'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    assert dict(load_config(tmp_path).agents)["codex"].binary == str(
        Path.home() / "codex/bin/codex"
    )


def test_machine_config_ignores_invalid_triage_default_priority(
    tmp_path: Path, monkeypatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "[triage]\ndefault_priority = 'invalid'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    with pytest.warns(UserWarning, match="triage.default_priority = invalid"):
        config = load_config(tmp_path)

    assert config.triage.default_priority == TriagePolicy.default_priority


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


def test_load_config_names_invalid_float_setting(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_timeout = 'abc'\n", encoding="utf-8", newline="\n"
    )

    with pytest.raises(ValueError, match="api_timeout: could not convert string to float"):
        load_config(tmp_path)


def test_load_config_prefers_pyproject_tool_issuekit(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.issuekit]\nproject = 'py-project'\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.toml").write_text(
        "project = 'standalone-project'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        project="py-project",
    )


def test_load_config_reads_api_fields_from_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "trusted_api_origins = ['https://mine.example']\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    config = load_config(tmp_path)

    assert config.api_url == "https://mine.example"
    assert config.project == "demo_project"
    assert config.api_timeout == 12.5


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


def test_load_config_reads_worker_heartbeat_interval(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "worker_heartbeat_interval_sec = 12.5\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path).worker_heartbeat_interval_sec == 12.5


def test_load_config_defaults_worker_heartbeat_interval(tmp_path: Path) -> None:
    assert load_config(tmp_path).worker_heartbeat_interval_sec == 60.0


@pytest.mark.parametrize("value", ["0", "-1"])
def test_load_config_rejects_non_positive_worker_heartbeat_interval(
    tmp_path: Path,
    value: str,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        f"worker_heartbeat_interval_sec = {value}\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="worker_heartbeat_interval_sec"):
        load_config(tmp_path)


def test_load_config_rejects_negative_claim_sync_interval(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "claim_sync_interval_sec = -1\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="claim_sync_interval_sec"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "value",
    ["feature branch", "main\u3042", "--upload-pack=x", "a..b", "x.lock", "-x"],
)
def test_load_config_rejects_invalid_work_branch(tmp_path: Path, value: str) -> None:
    (tmp_path / "issuekit.toml").write_text(
        f"work_branch = '{value}'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="Invalid work_branch:"):
        load_config(tmp_path)


@pytest.mark.parametrize("value", ["main", "release/1.2", "feat/abc-1"])
def test_load_config_accepts_valid_work_branch(tmp_path: Path, value: str) -> None:
    (tmp_path / "issuekit.toml").write_text(
        f"work_branch = '{value}'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path).work_branch == value


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


def test_load_config_rejects_non_boolean_hold_auto_adopted(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        '[triage]\nhold_auto_adopted = "false"\n',
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="triage.hold_auto_adopted.*boolean"):
        load_config(tmp_path)


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


def test_profile_tag_length_matches_workflow_token_limit(tmp_path: Path) -> None:
    assert PROFILE_TAG_MAX_LEN == 32
    valid_tag = "a" * PROFILE_TAG_MAX_LEN
    (tmp_path / "issuekit.toml").write_text(
        f"profile_tags = ['{valid_tag}']\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path).profile_tags == ("a" * PROFILE_TAG_MAX_LEN,)

    invalid_tag = "a" * (PROFILE_TAG_MAX_LEN + 1)
    (tmp_path / "issuekit.toml").write_text(
        f"profile_tags = ['{invalid_tag}']\n",
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
    assert config.api_url_source == "dotenv"
    assert config.api_url_trusted_by == "dotenv"


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
    assert config.api_url_source == "env"
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


def test_load_config_dotenv_only_loads_allowlisted_issuekit_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ISSUEKIT_CONFIG", "")
    for key in (
        "ISSUEKIT_API_URL",
        "ISSUEKIT_API_USER",
        "ISSUEKIT_API_PASSWORD",
        "ISSUEKIT_API_TOKEN",
        "ISSUEKIT_PROJECT",
        "ISSUEKIT_API_TIMEOUT",
        "ISSUEKIT_TOKEN_CACHE",
        "ISSUEKIT_ALLOW_INSECURE",
        "ISSUEKIT_ENFORCE_AUTHOR_HANDOFF",
        "ISSUEKIT_WORKSPACE",
        "ISSUEKIT_SESSION",
    ):
        monkeypatch.delenv(key, raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "ISSUEKIT_API_URL=https://dotenv.example\n"
        "ISSUEKIT_API_USER=dotenv-user\n"
        "ISSUEKIT_API_PASSWORD=dotenv-password\n"
        "ISSUEKIT_API_TOKEN=dotenv-token\n"
        "ISSUEKIT_PROJECT=dotenv-project\n"
        "ISSUEKIT_API_TIMEOUT=8.5\n"
        "ISSUEKIT_CONFIG=/tmp/attacker.toml\n"
        "ISSUEKIT_TOKEN_CACHE=/tmp/attacker-token.json\n"
        "ISSUEKIT_ALLOW_INSECURE=1\n"
        "ISSUEKIT_ENFORCE_AUTHOR_HANDOFF=0\n"
        "ISSUEKIT_WORKSPACE=/tmp/attacker-workspace\n"
        "ISSUEKIT_SESSION=attacker-session\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)
    captured = capsys.readouterr()

    assert config.api_url == "https://dotenv.example"
    assert config.api_url_source == "dotenv"
    assert config.project == "dotenv-project"
    assert config.api_timeout == 8.5
    assert os.environ["ISSUEKIT_API_USER"] == "dotenv-user"
    assert os.environ["ISSUEKIT_API_PASSWORD"] == "dotenv-password"
    assert os.environ["ISSUEKIT_API_TOKEN"] == "dotenv-token"
    assert os.environ["ISSUEKIT_CONFIG"] == ""
    assert "ISSUEKIT_TOKEN_CACHE" not in os.environ
    assert "ISSUEKIT_ALLOW_INSECURE" not in os.environ
    assert "ISSUEKIT_ENFORCE_AUTHOR_HANDOFF" not in os.environ
    assert "ISSUEKIT_WORKSPACE" not in os.environ
    assert "ISSUEKIT_SESSION" not in os.environ
    for key in (
        "ISSUEKIT_CONFIG",
        "ISSUEKIT_TOKEN_CACHE",
        "ISSUEKIT_ALLOW_INSECURE",
        "ISSUEKIT_ENFORCE_AUTHOR_HANDOFF",
        "ISSUEKIT_WORKSPACE",
        "ISSUEKIT_SESSION",
    ):
        assert captured.err.count(f"ignored {key}") == 1


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


def test_load_config_refuses_git_tracked_dotenv(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".env").write_text(
        "ISSUEKIT_API_URL=https://committed.example\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", ".env"], cwd=tmp_path, check=True)

    with pytest.raises(ValueError, match="Repo-local .env is tracked by git") as excinfo:
        load_config(tmp_path)

    assert "git rm --cached .env" in str(excinfo.value)


def test_load_config_ignores_stale_reviewer_policy_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "api_url = 'https://mine.example'\n"
            "default_reviewer = 'claude'\n"
            "require_distinct_reviewer = false\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "trusted_api_origins = ['https://mine.example']\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    config = load_config(tmp_path)

    assert config.api_url == "https://mine.example"


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
        "project = 'standalone-project'\n",
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        project="standalone-project",
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "[agents.custom]\nbinary = 'custom-agent'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    (tmp_path / "issuekit.toml").write_text(
        "disabled_agents = ['kimi']\n",
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
        "project = [\n",
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
        ),
        encoding="utf-8",
        newline="\n",
    )

    assert load_config(tmp_path) == IssuekitConfig(
        assignees=("alice", "bob"),
        stages=("draft", "review"),
    )


@pytest.mark.parametrize(
    ("filename", "body"),
    [
        (
            "issuekit.toml",
            "assignees = ['alice', 'bob']\ndefault_reviewer = 'nobody'\n"
            "require_distinct_reviewer = false\n",
        ),
        (
            "pyproject.toml",
            "[tool.issuekit]\nassignees = ['alice', 'bob']\n"
            "default_reviewer = 'nobody'\nrequire_distinct_reviewer = false\n",
        ),
    ],
)
def test_load_config_ignores_stale_reviewer_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    body: str,
) -> None:
    monkeypatch.setenv("ISSUEKIT_CONFIG", "")
    (tmp_path / filename).write_text(body, encoding="utf-8", newline="\n")

    config = load_config(tmp_path)

    assert config.assignees == ("alice", "bob")
    assert not hasattr(config, "default_reviewer")
    assert not hasattr(config, "require_distinct_reviewer")


def test_load_config_allows_disabling_claude_without_api_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ISSUEKIT_CONFIG", "")
    (tmp_path / "issuekit.toml").write_text(
        "disabled_agents = ['claude']\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.api_url == ""
    assert "claude" not in config.assignees


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("default_implementer = 'bad value'\n", "Invalid default_implementer token"),
        (
            "assignees = ['codex']\ndefault_implementer = 'claude'\n",
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


def test_load_config_reads_agent_guardrail_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        (
            "[agents.codex]\n"
            "binary = 'codex'\n"
            "headless_argv = ['exec']\n"
            "model_flag = '--model'\n"
            "speed_argv = ['--speed', 'priority']\n"
            "resumable = true\n"
            "session_flag = '--session-id'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    (tmp_path / "issuekit.toml").write_text(
        (
            "[agents.codex]\n"
            "model = 'gpt-5.3-codex-spark'\n"
            "speed = true\n"
            "prompt_suffix = 'Keep diffs small.'\n"
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
        adapter="codex",
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
        speed=True,
        speed_argv=("--speed", "priority"),
        prompt_suffix="Keep diffs small.",
        model_prompts=(("gpt-5.3-codex-spark", "Spark-only guardrail."),),
    )
    assert dict(config.agent_policies)["codex"].mojibake_gate is True
    assert dict(config.agent_policies)["codex"].diff_shape_warn_deletions == 12


def test_load_config_rejects_invalid_effort_argv_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        '[agents.codex]\neffort_argv = ["--x", "{"]\n',
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    with pytest.raises(ValueError, match="Invalid effort_argv template"):
        load_config(tmp_path)


def test_load_config_reads_false_speed(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agents.codex]\nspeed = false\n",
        encoding="utf-8",
        newline="\n",
    )

    assert dict(load_config(tmp_path).agents)["codex"].speed is False


def test_load_config_rejects_invalid_speed(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agents.codex]\nspeed = 'priority'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="Invalid boolean config value: priority"):
        load_config(tmp_path)


def test_load_config_merges_builtin_agent_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        (
            "[agents.codex]\n"
            "approval_flag = '--sandbox'\n"
            "approval_value = 'danger-full-access'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    config = load_config(tmp_path)
    agents = dict(config.agents)
    codex_default = dict(IssuekitConfig.agents)["codex"]

    assert tuple(agents) == ("kimi", "codex", "claude")
    assert agents["codex"] == AgentRunConfig(
        binary=codex_default.binary,
        adapter=codex_default.adapter,
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
        speed=codex_default.speed,
        speed_argv=codex_default.speed_argv,
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


def test_load_config_codex_app_server_is_explicit_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        (
            "[agents.codex]\n"
            "runtime = 'codex_app_server'\n"
            "app_server_argv = ['app-server', '--listen', 'stdio://']\n"
            "lease_ttl_seconds = 90\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    codex = dict(load_config(tmp_path).agents)["codex"]

    assert codex.runtime == "codex_app_server"
    assert codex.app_server_argv == ("app-server", "--listen", "stdio://")
    assert codex.lease_ttl_seconds == 90


def test_load_config_codex_exec_remains_default(tmp_path: Path) -> None:
    codex = dict(load_config(tmp_path).agents)["codex"]

    assert codex.runtime == "exec"


@pytest.mark.parametrize(
    ("config_text", "message"),
    [
        ("runtime = 'remote'\n", "Agent runtime"),
        ("lease_ttl_seconds = 14\n", "lease_ttl_seconds"),
        ("lease_ttl_seconds = 301\n", "lease_ttl_seconds"),
        ("app_server_argv = ['app-server', '--listen', 'ws://127.0.0.1:1']\n", "stdio"),
        ("app_server_argv = ['exec']\n", "app-server"),
    ],
)
def test_load_config_rejects_invalid_app_server_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_text: str,
    message: str,
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        f"[agents.codex]\n{config_text}",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_load_config_rejects_app_server_runtime_for_non_codex_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "[agents.claude]\nruntime = 'codex_app_server'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    with pytest.raises(ValueError, match="only for agents.codex"):
        load_config(tmp_path)


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


def test_load_config_reads_negotiation_approval_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        '[agents.kimi.roles.negotiation]\napproval_argv = ["--sandbox", "read-only"]\n',
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    config = load_config(tmp_path)

    assert dict(dict(config.agent_role_overlays)["kimi"]) == {
        "negotiation": RoleOverlay(approval_argv=("--sandbox", "read-only"))
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
            "supported roles: implementer, reviewer, router, triage, negotiation",
        ),
        (
            "[agents.claude.roles.reviewer]\nbinary = 'claude'\n",
            "can only be set in machine config",
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


def test_load_config_empty_agent_string_clears_optional_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "[agents.codex]\napproval_flag = ''\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))

    codex = dict(load_config(tmp_path).agents)["codex"]

    assert codex.approval_flag is None


def test_shipped_codex_defaults_enable_guardrails() -> None:
    codex = dict(IssuekitConfig.agents)["codex"]
    policy = dict(IssuekitConfig.agent_policies)["codex"]

    assert codex.model is None
    assert codex.prompt_suffix is not None
    assert "minimal, additive diffs" in codex.prompt_suffix
    assert "does not ask you to change" in codex.prompt_suffix
    assert "in a comment" in codex.prompt_suffix
    assert "mojibake" in codex.prompt_suffix
    assert policy.mojibake_gate is True
    assert policy.diff_shape_warn_deletions == 40


def test_shipped_claude_defaults_enable_guardrails() -> None:
    claude = dict(IssuekitConfig.agents)["claude"]
    policy = dict(IssuekitConfig.agent_policies)["claude"]

    assert claude.prompt_suffix is not None
    assert "does not ask you to change" in claude.prompt_suffix
    assert "in a comment" in claude.prompt_suffix
    assert "mojibake" in claude.prompt_suffix
    assert policy.mojibake_gate is True


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
