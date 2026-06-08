from pathlib import Path

from issuekit import cli

from tests.issue_helpers import issue_text, write_indexes, write_issue


def test_approve_completes_review_stage_issue(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="claude",
            stage="review",
            implementer="codex",
        ),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["approve", "1", "--verification", "uv run pytest"])

    captured = capsys.readouterr()
    completed = issues_dir / "completed" / "001_first.md"
    assert exit_code == 0
    assert "Approved issue #1" in captured.out
    assert not (issues_dir / "active" / "001_first.md").exists()
    assert completed.exists()
    content = completed.read_text(encoding="utf-8")
    assert "status: completed" in content
    assert "stage: done" in content
    assert "Approved by claude." in content
    assert "- Verification: `uv run pytest`" in content


def test_approve_accepts_explicit_summary_and_reviewer(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="review",
            implementer="claude",
        ),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(
        [
            "approve",
            "1",
            "--verification",
            "uv run pytest",
            "--summary",
            "Reviewed and approved.",
            "--reviewer",
            "codex",
        ]
    )

    assert exit_code == 0
    assert "Approved issue #1" in capsys.readouterr().out
    content = (issues_dir / "completed" / "001_first.md").read_text(encoding="utf-8")
    assert "Reviewed and approved." in content
    assert "Approved by codex." not in content


def test_approve_rejects_non_review_stage_without_force(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", status="in_progress", assignee="codex", stage="implementing"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["approve", "1", "--verification", "uv run pytest"])

    assert exit_code == 1
    assert "must be at the review stage before approval" in capsys.readouterr().err
    assert not (issues_dir / "completed" / "001_first.md").exists()


def test_approve_force_bypasses_review_stage_requirement(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", status="in_progress", assignee="codex", stage="implementing"),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["approve", "1", "--verification", "pytest", "--force"])

    assert exit_code == 0
    assert "Approved issue #1" in capsys.readouterr().out
    assert (issues_dir / "completed" / "001_first.md").exists()


def test_approve_respects_self_review_guard(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "require_distinct_reviewer = true\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="review",
            implementer="codex",
        ),
    )
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(
        ["approve", "1", "--verification", "uv run pytest", "--reviewer", "codex"]
    )

    assert exit_code == 1
    assert "self-review is not allowed" in capsys.readouterr().err
    assert not (issues_dir / "completed" / "001_first.md").exists()
