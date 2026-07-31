"""Run a reviewer agent and apply its structured verdict."""

from __future__ import annotations

import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from issuekit.agentrun import AgentResult, AgentRunner
from issuekit.agents.readonly import (
    prompt_from_spec,
    repository_mutation_message,
    run_readonly_evaluation,
    stdout_text,
)
from issuekit.agents.registry import resolve_adapter
from issuekit.commands.approve import approve_issue
from issuekit.config import IssuekitConfig
from issuekit.core import Issue, worker_keys_match
from issuekit.encoding import ASCII_ONLY_HINT, has_non_ascii, sanitize_to_ascii
from issuekit.gitutil import GitStatusEntry, git_status_entries, git_status_short, run_git
from issuekit.prompts import REVIEW_PROMPT, ReviewParseError, canonical_contract_token
from issuekit.store import managed_issue_store
from issuekit.workflow import WorkflowError, ensure_assigned_reviewer, request_changes

REVIEW_OUTPUT_KEYS = REVIEW_PROMPT.required_keys
_REVIEW_VERDICTS = {"approve", "request-changes"}
_MAX_DIFF_CHARS = 60000
_SUSPICIOUS_READABILITY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\bimportlib\.import_module\([^)\n]*(?:['\"][^'\"]*['\"]\s*\+)"
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
class ReviewVerdict:
    verdict: str
    verification: str
    notes: str


@dataclass(frozen=True)
class ReviewOutcome:
    issue: Issue
    result: AgentResult
    verdict: ReviewVerdict
    exit_code: int
    decided_issue: Issue | None = None


class ReviewRunParseError(ReviewParseError):
    """A review parse error that retains the completed agent run result."""

    def __init__(self, error: ReviewParseError, result: AgentResult) -> None:
        super().__init__(str(error))
        self.result = result


@dataclass(frozen=True)
class ReviewDiffContext:
    text: str
    has_changed_files: bool
    has_handoff_evidence: bool = False
    suspicious_warnings: tuple[str, ...] = ()


def run_review_and_decide(
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    timeout: float,
    model: str | None = None,
    reasoning_effort: str | None = None,
    follow: bool = False,
    abort_event: threading.Event | None = None,
    runner_factory=None,
    store=None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> ReviewOutcome:
    """Run an agent against a review-stage issue and apply its verdict."""

    out = out or sys.stdout
    err = err or sys.stderr
    issue_id = issue.id
    if issue_id is None:
        raise ValueError("Review issue is missing an id.")
    if issue.stage != "review":
        raise WorkflowError(f"Issue #{issue_id} is not at the review stage.")
    _ensure_registered_distinct_worker(issue, agent=agent, config=config)
    ensure_assigned_reviewer(issue, agent, agent)

    adapter = resolve_adapter(
        agent,
        config=config,
        model=model,
        reasoning_effort=reasoning_effort,
        role="reviewer",
    )
    diff_context = _collect_git_diff_context(cwd, issue=issue)
    if not diff_context.has_changed_files and not diff_context.has_handoff_evidence:
        raise WorkflowError(
            "No implementation diff is available for automated review; "
            "refusing to run the reviewer agent."
        )

    runner_factory = runner_factory or AgentRunner
    review_filename = f"review-issue-{issue_id}.md"
    run = run_readonly_evaluation(
        agent=agent,
        adapter=adapter,
        cwd=cwd,
        timeout=timeout,
        runner_factory=runner_factory,
        prompt=prompt_from_spec(
            REVIEW_PROMPT,
            cwd=cwd,
            filename=review_filename,
            body=_render_review_prompt(issue, diff_context=diff_context),
        ),
        label="Reviewer",
        subject=f"issue #{issue_id}",
        issue_id=issue_id,
        follow=follow,
        abort_event=abort_event,
    )
    result = run.result

    if run.repository_modified:
        print(
            repository_mutation_message(
                "ERROR: reviewer run modified repository state; "
                "not applying review verdict.",
                run,
            ),
            file=err,
        )
        if run.repository_error:
            print(f"ERROR: {run.repository_error}", file=err)
    if result.timed_out:
        return ReviewOutcome(issue=issue, result=result, verdict=_empty_verdict(), exit_code=124)
    if result.exit_code != 0:
        return ReviewOutcome(
            issue=issue,
            result=result,
            verdict=_empty_verdict(),
            exit_code=result.exit_code if result.exit_code >= 0 else 1,
        )

    if run.repository_modified:
        return ReviewOutcome(issue=issue, result=result, verdict=_empty_verdict(), exit_code=1)

    try:
        verdict = parse_review_output(stdout_text(result), err=err)
    except ReviewParseError as exc:
        raise ReviewRunParseError(exc, result) from exc
    with managed_issue_store(config, store) as active_store:
        agent_model, agent_reasoning_effort = adapter.effective_runtime()
        if not config.send_agent_runtime:
            agent_model = None
            agent_reasoning_effort = None
        if verdict.verdict == "approve":
            decided = approve_issue(
                issue_id,
                summary="Approved by reviewer agent.",
                verification=verdict.verification,
                reviewer=agent,
                config=config,
                store=active_store,
                agent_model=agent_model,
                agent_reasoning_effort=agent_reasoning_effort,
            )
            print(f"approved id={decided.id} ref={decided.ref}", file=out)
        else:
            decided = request_changes(
                issue_id,
                notes=verdict.notes,
                reviewer=agent,
                config=config,
                store=active_store,
                agent_model=agent_model,
                agent_reasoning_effort=agent_reasoning_effort,
            )
            print(
                f"requested_changes id={decided.id} ref={decided.ref} "
                f"assignee={decided.assignee} stage={decided.stage}",
                file=out,
            )
        return ReviewOutcome(
            issue=issue,
            result=result,
            verdict=verdict,
            exit_code=0,
            decided_issue=decided,
        )


def parse_review_output(stdout: str, *, err: TextIO | None = None) -> ReviewVerdict:
    """Parse the newest well-formed review block from agent stdout."""

    return _review_verdict_from_json(
        REVIEW_PROMPT.parse_json(stdout),
        err=err or sys.stderr,
    )


def _review_verdict_from_json(
    raw: dict[str, object],
    *,
    err: TextIO,
) -> ReviewVerdict:
    missing = [key for key in REVIEW_OUTPUT_KEYS if key not in raw]
    if missing:
        raise ReviewParseError(f"Review block is missing required key: {', '.join(missing)}.")

    raw_verdict = _required_string(raw["verdict"], "verdict")
    verdict = canonical_contract_token(raw_verdict, _REVIEW_VERDICTS)
    if verdict is None:
        raise ReviewParseError(f"Invalid review verdict: {raw_verdict}")
    verification = _sanitize_review_field(
        "verification",
        _required_review_text(raw["verification"], "verification").strip(),
        err=err,
    )
    notes = _sanitize_review_field(
        "notes",
        _required_review_text(raw["notes"], "notes").strip(),
        err=err,
    )
    if verdict == "approve" and not verification:
        raise ReviewParseError("Approved review verdict requires verification.")
    if verdict == "request-changes" and not notes:
        raise ReviewParseError("Request-changes review verdict requires notes.")
    _validate_ascii_review_field("verdict", verdict)
    return ReviewVerdict(verdict=verdict, verification=verification, notes=notes)


def _required_string(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise ReviewParseError(f"Review key {key} must be a string.")
    return value


def _required_review_text(value: object, key: str) -> str:
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise ReviewParseError(
                f"Review key {key} must be a string or a list of strings."
            )
        return "\n".join(value)
    return _required_string(value, key)


def _validate_ascii_review_field(key: str, value: str) -> None:
    if has_non_ascii(value):
        raise ReviewParseError(
            f"Review field {key} must be ASCII-only. {ASCII_ONLY_HINT}"
        )


def _sanitize_review_field(key: str, value: str, *, err: TextIO) -> str:
    if not has_non_ascii(value):
        return value
    sanitized = sanitize_to_ascii(value).strip()
    marker = f"[{key} sanitized from non-ASCII]"
    print(
        f"WARNING: reviewer agent field {key} contained non-ASCII text; "
        "sanitized before recording verdict.",
        file=err,
    )
    if not sanitized:
        return marker
    return f"{sanitized}\n\n{marker}"


def _ensure_registered_distinct_worker(
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
) -> None:
    reviewer_worker = config.qualified_worker_key()
    if reviewer_worker is None:
        raise WorkflowError(
            "Automated review requires a registered worker identity. Run `issuekit add` first."
        )
    if issue.worker and worker_keys_match(issue.worker, reviewer_worker):
        raise WorkflowError(
            f"Issue #{issue.id} was implemented by worker {issue.worker}; "
            "self-review by the same worker is not allowed."
        )
    if not issue.worker and issue.implementer == agent:
        no_eligible_reviewer = not any(
            configured_agent != issue.implementer
            for configured_agent, _run_config in config.agents
        )
        no_eligible_reviewer_message = (
            " no eligible reviewer via --agent: "
            f"{agent} is the implementer and no other agent is configured; "
            "use the open review pool (issuekit serve --review) or configure another reviewer."
            if no_eligible_reviewer
            else ""
        )
        raise WorkflowError(
            f"Issue #{issue.id} was implemented by {agent}; self-review is not allowed."
            f"{no_eligible_reviewer_message}"
        )


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
        issue_body=issue.body,
        implementation_context=diff,
        readability_hints=_readability_hint_section(diff_context),
        output_keys=", ".join(REVIEW_OUTPUT_KEYS),
        ascii_only_hint=ASCII_ONLY_HINT,
    )


def _collect_git_diff_context(cwd: Path, *, issue: Issue | None = None) -> ReviewDiffContext:
    status_entries = git_status_entries(cwd)
    status = git_status_short(cwd, strip=False, untracked_files="all")
    stat = _git_stdout(["--no-pager", "diff", "--stat", "HEAD", "--"], cwd) or ""
    tracked_diff = (
        _git_stdout(
            ["--no-pager", "diff", "--no-ext-diff", "--unified=80", "HEAD", "--"],
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
    "summary": "Handoff summary",
    "handoff_summary": "Handoff summary",
    "submit_summary": "Handoff summary",
    "review_summary": "Handoff summary",
    "branch": "Branch",
    "handoff_branch": "Branch",
    "submit_branch": "Branch",
    "review_branch": "Branch",
    "commit": "Commit",
    "handoff_commit": "Commit",
    "submit_commit": "Commit",
    "review_commit": "Commit",
    "verification": "Verification evidence",
    "handoff_verification": "Verification evidence",
    "review_verification": "Verification evidence",
}

_BODY_EVIDENCE_PATTERN = re.compile(
    r"^\s*(handoff summary|branch|commit|verification|"
    r"verification evidence|command evidence|commands run|checks|live host state)\s*:"
    r"(?P<value>.*)$",
    re.IGNORECASE,
)
_MARKDOWN_HEADING_PATTERN = re.compile(r"^\s*#{1,6}\s+")


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
        if paths and all(path.parts and path.parts[0] == ".agent-runs" for path in paths):
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
        if entry.status == "??"
        and not (entry.path.parts and entry.path.parts[0] == ".agent-runs")
    ]
    parts = [part for part in (tracked_diff.strip(), *untracked_sections) if part]
    combined = "\n\n".join(parts)
    if len(combined) <= _MAX_DIFF_CHARS:
        return combined

    omitted = [
        f"[untracked file omitted by review context size limit: {entry.path.as_posix()}]"
        for entry in entries
        if entry.status == "??"
        and not (entry.path.parts and entry.path.parts[0] == ".agent-runs")
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
    result = run_git(args, cwd)
    if result is None or result.returncode != 0:
        return ""
    return result.stdout


def _empty_verdict() -> ReviewVerdict:
    return ReviewVerdict(verdict="", verification="", notes="")
