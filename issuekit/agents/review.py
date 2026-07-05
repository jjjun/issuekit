"""Run a reviewer agent and apply its structured verdict."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
import sys
import threading
from typing import TextIO

from issuekit.agents.runner import AgentResult, AgentRunner, resolve_adapter
from issuekit.commands.approve import approve_issue
from issuekit.config import IssuekitConfig
from issuekit.core import ASCII_ONLY_HINT, Issue, has_non_ascii
from issuekit.gitutil import git_status_short, run_git
from issuekit.store import get_store
from issuekit.workflow import WorkflowError, ensure_assigned_reviewer, request_changes
from issuekit.worker_keys import worker_keys_match


REVIEW_BLOCK_LANGUAGE = "review"
REVIEW_OUTPUT_KEYS = ("verdict", "verification", "notes")
_REVIEW_BLOCK_PATTERN = re.compile(
    r"```review[ \t]*\r?\n(?P<body>.*?)\r?\n```",
    re.DOTALL,
)
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


class ReviewParseError(RuntimeError):
    """Raised when a reviewer response cannot be parsed."""


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

    adapter = resolve_adapter(agent, config=config, model=model)
    diff_context = _collect_git_diff_context(cwd, issue=issue)
    if not diff_context.has_changed_files and not diff_context.has_handoff_evidence:
        raise WorkflowError(
            "No implementation diff is available for automated review; "
            "refusing to run the reviewer agent."
        )

    run_dir = cwd / ".agent-runs"
    run_dir.mkdir(exist_ok=True)
    review_path = run_dir / f"review-issue-{issue_id}.md"
    review_path.write_text(
        _render_review_prompt(issue, cwd=cwd, diff_context=diff_context),
        encoding="utf-8",
        newline="\n",
    )
    fingerprint_before = _worktree_fingerprint(cwd)

    runner_factory = runner_factory or AgentRunner
    result = runner_factory().run(
        adapter,
        review_path,
        cwd,
        timeout=float(timeout),
        agent_name=agent,
        issue_id=issue_id,
        follow=follow,
        prompt_override=_review_prompt_pointer(review_path),
        abort_event=abort_event,
    )

    if result.timed_out:
        return ReviewOutcome(issue=issue, result=result, verdict=_empty_verdict(), exit_code=124)
    if result.exit_code != 0:
        return ReviewOutcome(
            issue=issue,
            result=result,
            verdict=_empty_verdict(),
            exit_code=result.exit_code if result.exit_code >= 0 else 1,
        )

    fingerprint_after = _worktree_fingerprint(cwd)
    if fingerprint_before != fingerprint_after:
        print(
            "ERROR: reviewer run modified the worktree; not applying review verdict.",
            file=err,
        )
        return ReviewOutcome(issue=issue, result=result, verdict=_empty_verdict(), exit_code=1)

    verdict = parse_review_output(_stdout_text(result))
    owned_store = None
    if store is None:
        owned_store = get_store(config)
        store = owned_store
    try:
        if verdict.verdict == "approve":
            decided = approve_issue(
                issue_id,
                summary="Approved by reviewer agent.",
                verification=verdict.verification,
                reviewer=agent,
                config=config,
                store=store,
            )
            print(f"approved id={decided.id} ref={decided.ref}", file=out)
        else:
            decided = request_changes(
                issue_id,
                notes=verdict.notes,
                reviewer=agent,
                config=config,
                store=store,
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
    finally:
        if owned_store is not None:
            owned_store.close()


def parse_review_output(stdout: str) -> ReviewVerdict:
    """Parse the newest well-formed review block from agent stdout."""

    blocks = [match.group("body") for match in _REVIEW_BLOCK_PATTERN.finditer(stdout)]
    if not blocks:
        raise ReviewParseError("No ```review``` block found in agent output.")

    last_json_error: ReviewParseError | None = None
    for block in reversed(blocks):
        try:
            raw = json.loads(block.strip())
        except json.JSONDecodeError as exc:
            last_json_error = ReviewParseError(f"Review block was not valid JSON: {exc.msg}.")
            continue
        if not isinstance(raw, dict):
            raise ReviewParseError("Review block JSON must be an object.")
        return _review_verdict_from_json(raw)

    if last_json_error is not None:
        raise last_json_error
    raise ReviewParseError("No well-formed ```review``` block found.")


def _review_verdict_from_json(raw: dict[str, object]) -> ReviewVerdict:
    missing = [key for key in REVIEW_OUTPUT_KEYS if key not in raw]
    if missing:
        raise ReviewParseError(f"Review block is missing required key: {', '.join(missing)}.")

    verdict = _required_string(raw["verdict"], "verdict")
    verification = _required_string(raw["verification"], "verification").strip()
    notes = _required_string(raw["notes"], "notes").strip()
    if verdict not in _REVIEW_VERDICTS:
        raise ReviewParseError(f"Invalid review verdict: {verdict}")
    if verdict == "approve" and not verification:
        raise ReviewParseError("Approved review verdict requires verification.")
    if verdict == "request-changes" and not notes:
        raise ReviewParseError("Request-changes review verdict requires notes.")
    _validate_ascii_review_field("verdict", verdict)
    _validate_ascii_review_field("verification", verification)
    _validate_ascii_review_field("notes", notes)
    return ReviewVerdict(verdict=verdict, verification=verification, notes=notes)


def _required_string(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise ReviewParseError(f"Review key {key} must be a string.")
    return value


def _validate_ascii_review_field(key: str, value: str) -> None:
    if has_non_ascii(value):
        raise ReviewParseError(
            f"Review field {key} must be ASCII-only. {ASCII_ONLY_HINT}"
        )


def _ensure_registered_distinct_worker(
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
) -> None:
    reviewer_worker = config.worker_key()
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
        raise WorkflowError(
            f"Issue #{issue.id} was implemented by {agent}; self-review is not allowed."
        )


def _render_review_prompt(
    issue: Issue,
    *,
    cwd: Path,
    diff_context: str | ReviewDiffContext | None = None,
) -> str:
    if diff_context is None:
        review_context = _collect_git_diff_context(cwd)
    elif isinstance(diff_context, ReviewDiffContext):
        review_context = diff_context
    else:
        review_context = ReviewDiffContext(
            text=diff_context,
            has_changed_files=bool(diff_context.strip()),
            suspicious_warnings=_suspicious_readability_warnings(diff_context),
        )
    diff = review_context.text
    review_target = (
        "the implementation diff"
        if review_context.has_changed_files
        else "the submitted handoff evidence"
    )
    return "\n".join(
        [
            f"# Review issue {issue.ref}",
            "",
            f"You are the reviewer. Review {review_target} against the issue.",
            "Do not edit files, commit, push, claim, submit, approve, request changes, or mutate tracker state.",
            "Review correctness, tests, readability, maintainability, and fit with surrounding style.",
            "When no local implementation diff is present, review the handoff evidence, command evidence,",
            "and any referenced live state; request changes if the evidence is insufficient to decide.",
            "Request changes for gratuitous obfuscation or unexplained style deviations even when tests pass.",
            "Examples include string-concatenated identifiers or import paths, avoidable importlib/getattr indirection,",
            "and globals()/setattr attribute injection where a plain definition works.",
            "",
            "Issue body:",
            "",
            issue.body,
            "",
            "Implementation context:",
            "",
            diff,
            "",
            _readability_hint_section(review_context),
            "",
            "Output contract:",
            "Emit exactly one fenced block and no other response text.",
            "Everything outside the block is ignored by the parser.",
            f"The JSON keys must be: {', '.join(REVIEW_OUTPUT_KEYS)}.",
            "The verdict must be approve or request-changes.",
            "For approve, verification must describe the checks you ran.",
            "For request-changes, notes must be actionable feedback for the implementer.",
            f"All JSON string values must be ASCII-only. {ASCII_ONLY_HINT}",
            "```review",
            "{",
            '  "verdict": "approve-or-request-changes",',
            '  "verification": "Command(s) run, or empty string for request-changes.",',
            '  "notes": "Short rationale or empty string."',
            "}",
            "```",
            "",
        ]
    )


def _git_diff_context(cwd: Path) -> str:
    return _collect_git_diff_context(cwd).text


def _collect_git_diff_context(cwd: Path, *, issue: Issue | None = None) -> ReviewDiffContext:
    status = git_status_short(cwd, strip=False, untracked_files="all")
    stat = _git_stdout(["--no-pager", "diff", "--stat", "HEAD", "--"], cwd) or ""
    diff = (
        _git_stdout(
            ["--no-pager", "diff", "--no-ext-diff", "--unified=80", "HEAD", "--"],
            cwd,
        )
        or ""
    )
    if diff and len(diff) > _MAX_DIFF_CHARS:
        diff = diff[:_MAX_DIFF_CHARS] + "\n\n[diff truncated]\n"
    handoff_evidence = _handoff_evidence_text(issue) if issue is not None else ""
    no_diff_note = (
        ""
        if _has_reviewable_changed_files(status)
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
        has_changed_files=_has_reviewable_changed_files(status),
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
    r"(?im)^\s*(handoff summary|branch|commit|verification|"
    r"verification evidence|command evidence|commands run|checks|live host state)\s*:",
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
    if not _BODY_EVIDENCE_PATTERN.search(body):
        return ""
    lines = [line.rstrip() for line in body.splitlines()]
    evidence_lines = [line for line in lines if _BODY_EVIDENCE_PATTERN.search(line)]
    return "\n".join(evidence_lines)


def _readability_hint_section(context: str | ReviewDiffContext) -> str:
    if isinstance(context, ReviewDiffContext):
        warnings = context.suspicious_warnings
    else:
        warnings = _suspicious_readability_warnings(context)
    if not warnings:
        return "Automated readability hints: none."
    return "\n".join(
        (
            "Automated readability hints:",
            *[f"- {warning}" for warning in warnings],
        )
    )


def _suspicious_readability_warnings(diff: str) -> tuple[str, ...]:
    warnings: list[str] = []
    for pattern, label in _SUSPICIOUS_READABILITY_PATTERNS:
        if pattern.search(diff):
            warnings.append(label)
    return tuple(warnings)


def _has_reviewable_changed_files(status: str | None) -> bool:
    if status is None:
        return False
    for line in status.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[1]
        path = Path(raw_path.strip('"'))
        if path.parts and path.parts[0] == ".agent-runs":
            continue
        return True
    return False


def _git_stdout(args: list[str], cwd: Path) -> str:
    result = run_git(args, cwd)
    if result is None or result.returncode != 0:
        return ""
    return result.stdout


def _review_prompt_pointer(review_path: Path) -> str:
    return (
        f"Read the review prompt at: {review_path} and respond with exactly one "
        "fenced review block per its instructions. Do not implement code; do not "
        "modify files; do not mutate the tracker."
    )


def _worktree_fingerprint(cwd: Path) -> tuple[tuple[str, str, str], ...] | None:
    status = git_status_short(cwd, strip=False, untracked_files="all")
    if status is None:
        return None
    entries: list[tuple[str, str, str]] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[1]
        raw_path = raw_path.strip('"')
        path = Path(raw_path)
        if path.parts and path.parts[0] == ".agent-runs":
            continue
        digest = _file_digest(cwd / path)
        entries.append((line[:2], path.as_posix(), digest))
    return tuple(sorted(entries))


def _file_digest(path: Path) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _stdout_text(result: AgentResult) -> str:
    if result.parsed and "stdout" in result.parsed:
        return result.parsed["stdout"]
    return result.stdout_path.read_text(encoding="utf-8", errors="replace")


def _empty_verdict() -> ReviewVerdict:
    return ReviewVerdict(verdict="", verification="", notes="")
