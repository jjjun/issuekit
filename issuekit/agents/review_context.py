"""Collect git, handoff, and readability context for automated reviews."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from issuekit.agents.handoff import NO_IMPLEMENTATION_CHANGES_MARKER
from issuekit.agents.paths import is_agent_runtime_entry
from issuekit.agents.review_output import REVIEW_OUTPUT_KEYS
from issuekit.core import Issue
from issuekit.encoding import ASCII_ONLY_HINT
from issuekit.gitutil import GitStatusEntry, git_status_entries, git_status_short, git_stdout
from issuekit.prompts import REVIEW_PROMPT, fence_untrusted

_MAX_DIFF_CHARS = 60000
_SUSPICIOUS_READABILITY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\bimportlib\.import_module\([^\n)]*(?:['\"][^'\"]*['\"]\s*\+)"
        ),
        "string-concatenated import_module path",
    ),
    (
        re.compile(r"\bgetattr\([^,\n]+,\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]\s*\+"),
        "string-concatenated getattr name",
    ),
    (
        re.compile(r"\bsetattr\([^,\n]+,\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"]\s*\+"),
        "string-concatenated setattr name",
    ),
    (
        re.compile(r"\bglobals\(\)\s*\[[^\]\n]+\]\s*="),
        "globals() attribute injection",
    ),
)


@dataclass(frozen=True)
class ReviewDiffContext:
    text: str
    has_changed_files: bool
    has_handoff_evidence: bool = False
    suspicious_warnings: tuple[str, ...] = ()


def _render_review_prompt(
    issue: Issue,
    *,
    diff_context: ReviewDiffContext,
) -> str:
    diff = diff_context.text
    review_target = (
        "the implementation diff"
        if diff_context.has_changed_files
        else "the submitted handoff evidence"
    )
    return REVIEW_PROMPT.render(
        issue_ref=issue.ref,
        review_target=review_target,
        issue_body=fence_untrusted("issue_body", issue.body),
        implementation_context=fence_untrusted("implementation_context", diff),
        readability_hints=_readability_hint_section(diff_context),
        output_keys=", ".join(REVIEW_OUTPUT_KEYS),
        ascii_only_hint=ASCII_ONLY_HINT,
    )


def _collect_git_diff_context(cwd: Path, *, issue: Issue | None = None) -> ReviewDiffContext:
    status_entries = git_status_entries(cwd)
    status = git_status_short(cwd, strip=False, untracked_files="all")
    stat = _git_stdout(
        [
            "--no-pager",
            "diff",
            "--stat",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        ],
        cwd,
    ) or ""
    tracked_diff = (
        _git_stdout(
            [
                "--no-pager",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--unified=80",
                "HEAD",
                "--",
            ],
            cwd,
        )
        or ""
    )
    diff = _combined_diff_evidence(cwd, tracked_diff, status_entries or ())
    handoff_evidence = _handoff_evidence_text(issue) if issue is not None else ""
    has_changed_files = _has_reviewable_changed_files(status_entries)
    no_diff_note = (
        ""
        if has_changed_files
        else "\n\nNo local implementation diff is available in this checkout."
    )
    text = "\n".join(
        (
            "git status --short:",
            status.strip() if status else "(unavailable or clean)",
            "",
            "git diff --stat HEAD --:",
            stat.strip() if stat else "(unavailable or empty)",
            "",
            "git diff HEAD --:",
            diff.strip() if diff else "(unavailable or empty)",
            no_diff_note,
            handoff_evidence,
        )
    ).strip()
    return ReviewDiffContext(
        text=text,
        has_changed_files=has_changed_files,
        has_handoff_evidence=bool(handoff_evidence.strip()),
        suspicious_warnings=_suspicious_readability_warnings(diff),
    )


_HANDOFF_METADATA_LABELS = {
    "branch": "Branch",
    "commit": "Commit",
}

_BODY_EVIDENCE_PATTERN = re.compile(
    r"^\s*(handoff summary|branch|commit|verification|"
    r"verification evidence|command evidence|commands run|checks|live host state)\s*:"
    r"(?P<value>.*)$",
    re.IGNORECASE,
)
_MARKDOWN_HEADING_PATTERN = re.compile(r"^\s*#{1,6}\s+")


def _latest_handoff_has_run_log(issue: Issue) -> bool:
    lines = issue.body.splitlines()
    handoff_index = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if re.fullmatch(r"\s*## Handoff\s*", lines[index])
        ),
        None,
    )
    if handoff_index is None:
        return False
    end_index = next(
        (
            index
            for index in range(handoff_index + 1, len(lines))
            if re.match(r"^\s*##\s+", lines[index])
        ),
        len(lines),
    )
    summary = "\n".join(lines[handoff_index + 1 : end_index])
    lines = summary.splitlines()
    has_allow_no_changes_marker = any(
        line.strip() == NO_IMPLEMENTATION_CHANGES_MARKER for line in lines
    )
    return not has_allow_no_changes_marker and any(
        line.startswith("Run log: ") for line in lines
    )


def _handoff_evidence_text(issue: Issue | None) -> str:
    if issue is None:
        return ""

    entries: list[str] = []
    seen_labels: set[str] = set()
    for key, label in _HANDOFF_METADATA_LABELS.items():
        value = issue.metadata.get(key, "").strip()
        if not value:
            continue
        unique_label = label
        if unique_label in seen_labels:
            unique_label = f"{label} ({key})"
        seen_labels.add(unique_label)
        entries.append(f"{unique_label}: {value}")

    body_evidence = _body_handoff_evidence(issue.body)
    if body_evidence:
        entries.append("Issue body evidence:")
        entries.append(body_evidence)

    if not entries:
        return ""
    return "\n".join(("Handoff evidence:", *entries))


def _body_handoff_evidence(body: str) -> str:
    lines = [line.rstrip() for line in body.splitlines()]
    sections: list[str] = []
    index = 0
    while index < len(lines):
        match = _BODY_EVIDENCE_PATTERN.match(lines[index])
        if match is None:
            index += 1
            continue
        section = [lines[index]]
        has_value = bool(match.group("value").strip())
        index += 1
        while index < len(lines):
            if _MARKDOWN_HEADING_PATTERN.match(lines[index]):
                break
            if _BODY_EVIDENCE_PATTERN.match(lines[index]):
                break
            section.append(lines[index])
            has_value = has_value or bool(lines[index].strip())
            index += 1
        while section and not section[-1].strip():
            section.pop()
        if has_value:
            sections.append("\n".join(section))
    return "\n".join(sections)


def _readability_hint_section(context: ReviewDiffContext) -> str:
    warnings = context.suspicious_warnings
    if not warnings:
        return "Automated readability hints: none."
    return "\n".join(
        (
            "Automated readability hints:",
            *[f"- {warning}" for warning in warnings],
        )
    )


def _suspicious_readability_warnings(diff: str) -> tuple[str, ...]:
    added_text = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    warnings: list[str] = []
    for pattern, label in _SUSPICIOUS_READABILITY_PATTERNS:
        if pattern.search(added_text):
            warnings.append(label)
    return tuple(warnings)


def _has_reviewable_changed_files(
    entries: tuple[GitStatusEntry, ...] | None,
) -> bool:
    if entries is None:
        return False
    for entry in entries:
        paths = tuple(
            path for path in (entry.path, entry.original_path) if path is not None
        )
        if paths and all(is_agent_runtime_entry(path) for path in paths):
            continue
        return True
    return False


def _combined_diff_evidence(
    cwd: Path,
    tracked_diff: str,
    entries: tuple[GitStatusEntry, ...],
) -> str:
    untracked_sections = [
        _untracked_diff_section(cwd, entry.path)
        for entry in entries
        if entry.status == "??" and not is_agent_runtime_entry(entry.path)
    ]
    parts = [part for part in (tracked_diff.strip(), *untracked_sections) if part]
    combined = "\n\n".join(parts)
    if len(combined) <= _MAX_DIFF_CHARS:
        return combined

    omitted = [
        f"[untracked file omitted by review context size limit: {entry.path.as_posix()}]"
        for entry in entries
        if entry.status == "??" and not is_agent_runtime_entry(entry.path)
    ]
    marker_text = "\n".join(omitted)
    suffix = "\n\n".join(part for part in ("[diff truncated]", marker_text) if part)
    available = max(0, _MAX_DIFF_CHARS - len(suffix) - 2)
    tracked = tracked_diff.strip()[:available]
    return "\n\n".join(part for part in (tracked, suffix) if part)[:_MAX_DIFF_CHARS]


def _untracked_diff_section(cwd: Path, rel_path: Path) -> str:
    path_text = rel_path.as_posix()
    path = cwd / rel_path
    if path.is_symlink():
        return f"[untracked symlink: {path_text}]"
    try:
        if not path.is_file():
            return f"[untracked non-regular file: {path_text}]"
        if path.stat().st_size > _MAX_DIFF_CHARS:
            return f"[untracked file omitted by review context size limit: {path_text}]"
        raw = path.read_bytes()
    except OSError as exc:
        return f"[untracked unreadable file: {path_text}: {exc}]"
    if b"\0" in raw:
        return f"[untracked binary file: {path_text}]"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"[untracked binary file: {path_text}]"
    lines = text.splitlines()
    additions = "\n".join(f"+{line}" for line in lines)
    return "\n".join(
        (
            f"diff --git a/{path_text} b/{path_text}",
            "new file mode 100644",
            "--- /dev/null",
            f"+++ b/{path_text}",
            f"@@ -0,0 +1,{len(lines)} @@",
            additions,
        )
    ).rstrip()


def _git_stdout(args: list[str], cwd: Path) -> str:
    return git_stdout(args, cwd) or ""
