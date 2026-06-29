from pathlib import Path

import pytest

from issuekit.commands.complete import complete_issue
from issuekit.config import IssuekitConfig
from issuekit.workflow import (
    WorkflowError,
    claim_issue,
    claim_next,
    find_for,
    request_changes,
    submit_for_review,
)

from tests.issue_helpers import issue_text, write_issue


def assert_single_frontmatter_body_gap(content: str) -> None:
    assert "\n---\n\n# Issue" in content
    assert "\n---\n\n\n" not in content


def test_claim_next_picks_highest_priority_then_lowest_id(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_low.md", issue_text(1, "Low", priority="low"))
    write_issue(issues_dir / "active" / "002_high.md", issue_text(2, "High", priority="high"))
    write_issue(issues_dir / "active" / "003_high.md", issue_text(3, "High 2", priority="high"))
    write_issue(issues_dir / "active" / "004_planned.md", issue_text(4, "Planned", status="planned"))

    issue = claim_next(issues_dir, "codex")

    assert issue is not None
    assert issue.id == 2
    assert issue.issue_status == "in_progress"
    assert issue.assignee == "codex"
    assert issue.stage == "implementing"
    assert issue.implementer == "codex"
    assert_single_frontmatter_body_gap(issue.file_path.read_text(encoding="utf-8"))


def test_claim_next_respects_priority_filter(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_low.md", issue_text(1, "Low", priority="low"))
    write_issue(issues_dir / "active" / "002_high.md", issue_text(2, "High", priority="high"))

    issue = claim_next(issues_dir, "codex", priority="low")

    assert issue is not None
    assert issue.id == 1


def test_claim_issue_rejects_explicit_author_self_assignment(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", assignee="codex", author="codex"),
    )

    with pytest.raises(WorkflowError, match="authored by codex"):
        claim_issue(issues_dir, 1, "codex")


def test_claim_next_rejects_explicit_author_self_assignment(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", assignee="codex", author="codex"),
    )

    with pytest.raises(WorkflowError, match="author self-implementation"):
        claim_next(issues_dir, "codex")


def test_claim_allows_different_implementer_for_authored_issue(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", assignee="kimi", author="codex"),
    )

    issue = claim_issue(issues_dir, 1, "kimi")

    assert issue.assignee == "kimi"
    assert issue.stage == "implementing"
    assert issue.implementer == "kimi"
    assert issue.author == "codex"


def test_claim_open_pool_allows_same_name_author_claim(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", author="codex"),
    )

    issue = claim_next(issues_dir, "codex")

    assert issue is not None
    assert issue.assignee == "codex"
    assert issue.stage == "implementing"
    assert issue.implementer == "codex"


def test_submit_for_review_flips_to_reviewer_and_appends_note(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="implementing",
            implementer="codex",
        ),
    )

    issue = submit_for_review(
        issues_dir,
        1,
        summary="Implemented workflow.",
        branch="codex/workflow",
        commit="abc123",
    )

    content = issue.file_path.read_text(encoding="utf-8")
    assert issue.assignee == "claude"
    assert issue.stage == "review"
    assert issue.implementer == "codex"
    assert_single_frontmatter_body_gap(content)
    assert "implementer: codex" in content
    assert "## Handoff" in content
    assert "- Branch: `codex/workflow`" in content
    assert "- Commit: `abc123`" in content


def test_submit_for_review_rejects_wrong_assignee(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", status="in_progress", assignee="claude", stage="implementing"),
    )

    with pytest.raises(WorkflowError, match="not codex"):
        submit_for_review(issues_dir, 1, summary="Done.")


def test_submit_for_review_rejects_explicit_self_assignment(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="implementing",
            implementer="codex",
        ),
    )

    with pytest.raises(WorkflowError, match="omit `reviewer`"):
        submit_for_review(issues_dir, 1, summary="Done.", reviewer="codex")


def test_submit_for_review_open_pool_allows_same_name_review(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="implementing",
            implementer="codex",
        ),
    )

    submitted = submit_for_review(
        issues_dir,
        1,
        summary="Done.",
        config=IssuekitConfig(default_reviewer="auto"),
    )
    assert submitted.assignee == ""
    assert submitted.stage == "review"

    approved = complete_issue(
        issues_dir,
        1,
        reviewer="codex",
        summary="Approved by codex.",
        verification="pytest",
    )
    assert approved.status == "completed"


def test_submit_for_review_routes_to_explicit_reviewer(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="claude",
            stage="implementing",
            implementer="claude",
        ),
    )

    issue = submit_for_review(
        issues_dir,
        1,
        summary="Done.",
        assignee="claude",
        reviewer="codex",
    )

    assert issue.assignee == "codex"
    assert issue.stage == "review"
    assert issue.implementer == "claude"


def test_author_can_review_and_approve_different_implementer(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="kimi",
            stage="implementing",
            implementer="kimi",
            author="codex",
        ),
    )

    submitted = submit_for_review(
        issues_dir,
        1,
        summary="Done.",
        assignee="kimi",
        reviewer="codex",
        config=IssuekitConfig(require_distinct_reviewer=True),
    )
    approved = complete_issue(
        issues_dir,
        1,
        reviewer="codex",
        summary="Approved by author.",
        verification="pytest",
        config=IssuekitConfig(require_distinct_reviewer=True),
    )

    assert submitted.assignee == "codex"
    assert submitted.stage == "review"
    assert approved.status == "completed"
    assert approved.author == "codex"


def test_submit_for_review_auto_opens_review_pool_when_guard_is_off(
    tmp_path: Path,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="implementing",
            implementer="codex",
        ),
    )

    issue = submit_for_review(
        issues_dir,
        1,
        summary="Done.",
        config=IssuekitConfig(default_reviewer="auto"),
    )

    assert issue.assignee == ""
    assert issue.stage == "review"


def test_submit_for_review_auto_opens_review_even_when_guard_is_required(
    tmp_path: Path,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="implementing",
            implementer="codex",
        ),
    )

    issue = submit_for_review(
        issues_dir,
        1,
        summary="Done.",
        config=IssuekitConfig(default_reviewer="auto", require_distinct_reviewer=True),
    )

    assert issue.assignee == ""
    assert issue.stage == "review"


def test_request_changes_returns_issue_to_codex_and_can_be_reclaimed(tmp_path: Path) -> None:
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

    issue = request_changes(issues_dir, 1, notes="Please add tests.")
    reclaimed = claim_next(issues_dir, "codex")

    assert issue.assignee == "codex"
    assert issue.stage == "changes_requested"
    assert issue.implementer == "codex"
    assert "## Review Feedback" in issue.file_path.read_text(encoding="utf-8")
    assert reclaimed is not None
    assert reclaimed.id == 1
    assert reclaimed.stage == "implementing"
    assert reclaimed.implementer == "codex"
    assert_single_frontmatter_body_gap(reclaimed.file_path.read_text(encoding="utf-8"))


def test_request_changes_defaults_to_recorded_implementer(tmp_path: Path) -> None:
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

    issue = request_changes(issues_dir, 1, reviewer="codex", notes="Please add tests.")

    assert issue.assignee == "claude"
    assert issue.stage == "changes_requested"
    assert issue.implementer == "claude"


def test_request_changes_reviewer_mismatch_names_assigned_reviewer(tmp_path: Path) -> None:
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

    with pytest.raises(WorkflowError) as excinfo:
        request_changes(
            issues_dir,
            1,
            reviewer="claude",
            notes="Please add tests.",
            config=IssuekitConfig(default_reviewer="auto"),
        )

    message = str(excinfo.value)
    assert "review is assigned to reviewer 'codex'" in message
    assert "You passed reviewer='claude'" in message
    assert "Omit `reviewer` to use default_reviewer" in message


def test_open_review_can_be_approved_by_any_agent(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="",
            stage="review",
            implementer="codex",
        ),
    )

    issue = complete_issue(
        issues_dir, 1, reviewer="claude", summary="Approved by claude.", verification="pytest"
    )

    assert issue.status == "completed"
    assert "Approved by claude." in issue.file_path.read_text(encoding="utf-8")


def test_open_review_can_be_returned_by_any_agent(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="",
            stage="review",
            implementer="codex",
        ),
    )

    issue = request_changes(issues_dir, 1, reviewer="claude", notes="Please add tests.")

    assert issue.assignee == "codex"
    assert issue.stage == "changes_requested"


def test_open_review_rejects_self_review_when_guard_is_required(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="",
            stage="review",
            implementer="codex",
        ),
    )

    with pytest.raises(WorkflowError, match="self-review is not allowed"):
        complete_issue(
            issues_dir,
            1,
            reviewer="codex",
            verification="pytest",
            config=IssuekitConfig(require_distinct_reviewer=True),
        )


def test_open_review_allows_non_implementer_when_guard_is_required(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="",
            stage="review",
            implementer="codex",
        ),
    )

    issue = complete_issue(
        issues_dir,
        1,
        reviewer="claude",
        summary="Approved by claude.",
        verification="pytest",
        config=IssuekitConfig(require_distinct_reviewer=True),
    )

    assert issue.status == "completed"
    assert "Approved by claude." in issue.file_path.read_text(encoding="utf-8")


def test_workflow_transitions_do_not_grow_frontmatter_body_gap(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First"))

    claimed = claim_next(issues_dir, "codex")
    assert claimed is not None
    after_claim = claimed.file_path.read_text(encoding="utf-8")
    assert_single_frontmatter_body_gap(after_claim)
    assert "implementer: codex" in after_claim

    submitted = submit_for_review(
        issues_dir,
        1,
        summary="Implemented workflow.",
        branch="codex/workflow",
        commit="abc123",
    )
    after_submit = submitted.file_path.read_text(encoding="utf-8")
    assert_single_frontmatter_body_gap(after_submit)
    assert submitted.implementer == "codex"

    requested = request_changes(issues_dir, 1, notes="Please add tests.")
    after_request = requested.file_path.read_text(encoding="utf-8")
    assert_single_frontmatter_body_gap(after_request)
    assert requested.implementer == "codex"

    resubmitted = submit_for_review(
        issues_dir,
        1,
        summary="Added tests.",
        branch="codex/workflow",
        commit="def456",
    )
    after_resubmit = resubmitted.file_path.read_text(encoding="utf-8")
    assert_single_frontmatter_body_gap(after_resubmit)
    assert resubmitted.implementer == "codex"

    assert "## Handoff" in after_resubmit
    assert "## Review Feedback" in after_resubmit


def test_find_for_lists_matching_active_issues(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_review.md",
        issue_text(1, "Review", status="in_progress", assignee="claude", stage="review"),
    )
    write_issue(
        issues_dir / "active" / "002_work.md",
        issue_text(2, "Work", status="in_progress", assignee="codex", stage="implementing"),
    )

    assert [issue.id for issue in find_for(issues_dir, "claude", stage="review")] == [1]


def test_complete_issue_sets_done_stage_and_clears_assignee(tmp_path: Path) -> None:
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

    issue = complete_issue(issues_dir, 1, summary="Approved.", verification="pytest")
    content = issue.file_path.read_text(encoding="utf-8")

    assert issue.status == "completed"
    assert issue.stage == "done"
    assert issue.assignee == ""
    assert issue.implementer == ""
    assert_single_frontmatter_body_gap(content)
    assert "stage: done" in content
    assert "assignee:" not in content
    assert "implementer:" not in content


def test_workflow_transitions_preserve_unknown_frontmatter(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First").replace(
            "title: First\n",
            "origin: source#42@abc123\ntitle: First\n",
        ),
    )

    claimed = claim_next(issues_dir, "codex")
    submitted = submit_for_review(issues_dir, 1, summary="Done.", reviewer="claude")
    completed = complete_issue(issues_dir, 1, summary="Approved.", verification="pytest")

    assert claimed is not None
    assert claimed.frontmatter.data["origin"] == "source#42@abc123"
    assert submitted.frontmatter.data["origin"] == "source#42@abc123"
    assert completed.frontmatter.data["origin"] == "source#42@abc123"


def test_complete_issue_allows_self_review_by_default(tmp_path: Path) -> None:
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

    issue = complete_issue(issues_dir, 1, reviewer="codex")

    assert issue.status == "completed"


def test_complete_issue_rejects_self_review_when_guard_is_required(tmp_path: Path) -> None:
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

    with pytest.raises(WorkflowError, match="self-review is not allowed"):
        complete_issue(
            issues_dir,
            1,
            reviewer="codex",
            config=IssuekitConfig(require_distinct_reviewer=True),
        )


def test_workflow_rejects_invalid_tokens_and_non_ascii_text(tmp_path: Path) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", status="in_progress", assignee="codex", stage="implementing"),
    )

    with pytest.raises(WorkflowError, match="Invalid assignee token"):
        claim_next(issues_dir, "codex\nstage: done")
    with pytest.raises(WorkflowError, match="ASCII-only"):
        submit_for_review(issues_dir, 1, summary="\u3042")
