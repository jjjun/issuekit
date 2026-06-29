from pathlib import Path

from issuekit import cli

from tests.issue_helpers import issue_text, write_issue


def test_init_fresh_dir_gets_full_scaffold(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["init"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Wrote: .gitattributes" in captured.out
    assert (tmp_path / ".gitattributes").exists()
    assert (tmp_path / ".editorconfig").exists()
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == (
        "issuekit.local.toml\n.agent-runs/\n"
    )
    assert (tmp_path / ".pre-commit-config.yaml").exists()
    assert (tmp_path / "docs" / "issues" / "README.md").exists()
    assert not (tmp_path / "docs" / "issues" / "incoming").exists()
    assert not (tmp_path / "docs" / "issues" / "active").exists()
    assert not (tmp_path / "docs" / "issues" / "indexes").exists()
    assert cli.main(["migrate-to-api", "--dry-run"]) == 0


def test_init_rerun_preserves_existing_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cli.main(["init"])
    custom = "custom\n"
    (tmp_path / ".gitattributes").write_text(custom, encoding="utf-8")

    exit_code = cli.main(["init"])

    assert exit_code == 0
    assert (tmp_path / ".gitattributes").read_text(encoding="utf-8") == custom


def test_init_adds_missing_agent_runs_gitignore_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / ".gitignore").write_text("issuekit.local.toml\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["init"])

    assert exit_code == 0
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == (
        "issuekit.local.toml\n.agent-runs/\n"
    )


def test_init_force_overwrites_templates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cli.main(["init"])
    (tmp_path / ".editorconfig").write_text("custom\n", encoding="utf-8")

    exit_code = cli.main(["init", "--force"])

    assert exit_code == 0
    assert "charset = utf-8" in (tmp_path / ".editorconfig").read_text(encoding="utf-8")


def test_init_preserves_existing_pre_commit_and_prints_guidance(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["init"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert (tmp_path / ".pre-commit-config.yaml").read_text(encoding="utf-8") == "repos: []\n"
    assert "issuekit check-encoding" in captured.out


def test_init_written_files_have_no_bom_or_crlf(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    cli.main(["init"])

    for path in [
        tmp_path / ".gitattributes",
        tmp_path / ".editorconfig",
        tmp_path / ".pre-commit-config.yaml",
        tmp_path / "docs" / "issues" / "README.md",
    ]:
        content = path.read_bytes()
        assert not content.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in content


def test_init_sets_ascii_threshold_for_legacy_non_ascii_issues(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = \"legacy\"\n", encoding="utf-8")
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "003_legacy.md",
        issue_text(3, "Legacy") + "\nLegacy text: \u3042\n",
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["init"])

    assert exit_code == 0
    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.issuekit]" in pyproject
    assert "ascii_id_threshold = 4" in pyproject
    assert "api_url" in pyproject
    assert cli.main(["migrate-to-api", "--dry-run"]) == 0


def test_init_prints_threshold_block_when_pyproject_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "003_legacy.md",
        issue_text(3, "Legacy") + "\nLegacy text: \u3042\n",
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["init"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Add this block to pyproject.toml" in captured.out
    assert "ascii_id_threshold = 4" in captured.out
