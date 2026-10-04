"""Shared handoff markers used by implementation and review runs."""

from issuekit.prompts import render_review_feedback_prompt

NO_IMPLEMENTATION_CHANGES_MARKER = (
    "No implementation changes: submitted with --allow-no-changes."
)

ISSUEKIT_BODY_SECTION_HEADINGS = (
    "## Handoff",
    "## Review Feedback",
    "## Completion Notes",
)


def review_feedback_prompt(issue_body: str) -> str | None:
    lines = issue_body.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == "## Review Feedback":
            start = index + 1
    if start is None:
        return None

    collected: list[str] = []
    for line in lines[start:]:
        if line.strip() in ISSUEKIT_BODY_SECTION_HEADINGS:
            break
        collected.append(line)
    notes = "\n".join(collected).strip()
    if not notes:
        return None
    return render_review_feedback_prompt(notes)
