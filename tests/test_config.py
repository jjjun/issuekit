from collections.abc import Iterator
import os
from pathlib import Path

import pytest

from issuekit.config import AgentRunConfig, IssuekitConfig, WorkerIdentity, load_config


_ENV_KEYS = (
    "ISSUEKIT_API_PASSWORD",
    "ISSUEKIT_API_TIMEOUT",
    "ISSUEKIT_API_TOKEN",
    "ISSUEKIT_API_URL",
    "ISSUEKIT_API_USER",
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
) -> None:
    monkeypatch.setenv("ISSUEKIT_API_URL", "https://mine.real-env")
    (tmp_path / ".env").write_text(
        "ISSUEKIT_API_URL=https://mine.dotenv\n",
        encoding="utf-8",
        newline="\n",
    )

    config = load_config(tmp_path)

    assert config.api_url == "https://mine.real-env"


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
    assert os.environ["DOTENV_EXTRA"] == "extra value"
    assert "MALFORMED_LINE" not in os.environ


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


def test_config_worker_key_returns_registered_identity() -> None:
    assert IssuekitConfig().worker_key() is None
    config = IssuekitConfig(worker=WorkerIdentity("machine", "repo", "checkout"))

    assert config.worker_key() == "machine/repo/checkout"


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
        approval_flag="--full-auto",
        resumable=True,
        session_flag="--session-id",
        model_flag="--model",
        model="gpt-5.3-codex-spark",
        prompt_suffix="Keep diffs small.",
        model_prompts=(("gpt-5.3-codex-spark", "Spark-only guardrail."),),
        mojibake_gate=True,
        diff_shape_warn_deletions=12,
    )


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
        prompt_suffix=codex_default.prompt_suffix,
        model_prompts=codex_default.model_prompts,
        mojibake_gate=True,
        diff_shape_warn_deletions=40,
    )
    assert agents["kimi"] == dict(IssuekitConfig.agents)["kimi"]
    assert agents["claude"] == dict(IssuekitConfig.agents)["claude"]


def test_load_config_honors_false_builtin_agent_override(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agents.codex]\nmojibake_gate = false\n",
        encoding="utf-8",
        newline="\n",
    )

    codex = dict(load_config(tmp_path).agents)["codex"]

    assert codex.mojibake_gate is False
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

    assert codex.model is None
    assert codex.prompt_suffix is not None
    assert "minimal, additive diffs" in codex.prompt_suffix
    assert "mojibake" in codex.prompt_suffix
    assert codex.mojibake_gate is True
    assert codex.diff_shape_warn_deletions == 40


def test_shipped_kimi_defaults_do_not_enable_guardrails() -> None:
    kimi = dict(IssuekitConfig.agents)["kimi"]

    assert kimi.prompt_suffix is None
    assert kimi.mojibake_gate is False
    assert kimi.diff_shape_warn_deletions is None


def test_shipped_only_claude_defaults_enable_resumable_sessions() -> None:
    agents = dict(IssuekitConfig.agents)

    assert agents["claude"].resumable is True
    assert agents["claude"].session_flag == "--session-id"
    assert agents["codex"].resumable is False
    assert agents["codex"].session_flag is None
    assert agents["kimi"].resumable is False
    assert agents["kimi"].session_flag is None
