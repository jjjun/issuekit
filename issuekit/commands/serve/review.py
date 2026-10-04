"""Review polling and issue review runs for the serve command."""

from __future__ import annotations

import sys
from pathlib import Path

from issuekit.agents.review import run_review_and_decide
from issuekit.agents.review_output import ReviewRunParseError
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.errors import AGENT_RUN_ERRORS, WorkflowError
from issuekit.store import get_store
from issuekit.workflow import next_review

from .implement import IssueRunResult
from .loop import (
    Backoff,
    PollResult,
    ShutdownController,
    close_store,
    log_event,
    recreate_store,
    run_poll_loop,
    should_recreate_store,
)


def run_review_issue(
    args,
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    log_path: Path,
    controller: ShutdownController,
    backoff: float,
    store=None,
) -> IssueRunResult:
    try:
        outcome = run_review_and_decide(
            issue,
            agent=agent,
            config=config,
            cwd=cwd,
            timeout=float(args.timeout_sec),
            model=getattr(args, "model", None),
            reasoning_effort=getattr(args, "reasoning_effort", None),
            abort_event=controller.abort_event,
            store=store,
            out=sys.stderr,
            err=sys.stderr,
        )
    except ReviewRunParseError as exc:
        log_event(
            sys.stderr,
            log_path,
            "review_decision_discarded",
            issue=issue.id,
            error=str(exc),
            remedy="rerun_review",
            backoff=backoff,
        )
        return IssueRunResult("error", 1)
    except AGENT_RUN_ERRORS as exc:
        log_event(
            sys.stderr,
            log_path,
            "review_error",
            issue=issue.id,
            error=str(exc),
            backoff=backoff,
        )
        return IssueRunResult("error", 1, recreate_store=should_recreate_store(exc))

    if outcome.exit_code != 0 or outcome.decided_issue is None:
        log_event(
            sys.stderr,
            log_path,
            "review_failed",
            issue=issue.id,
            exit_code=outcome.exit_code,
            backoff=backoff,
        )
        return IssueRunResult("failed", outcome.exit_code)

    return IssueRunResult("reviewed", 0, reviewed_issue=outcome.decided_issue)


def run_review_loop(
    args,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    log_path: Path,
    controller: ShutdownController,
) -> int:
    return _serve_review_loop(
        args,
        agent=agent,
        config=config,
        cwd=cwd,
        log_path=log_path,
        controller=controller,
        store=get_store(config) if config.api_url else None,
    )


def _serve_review_loop(
    args,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    log_path: Path,
    controller: ShutdownController,
    store,
) -> int:
    backoff = Backoff()
    failed_review_ids: set[int] = set()
    try:
        def poll(attempt: int, backoff_seconds: float):
            try:
                issue = next_review(
                    agent,
                    config=config,
                    store=store,
                    include_open=True,
                    exclude_ids=failed_review_ids,
                )
            except (TimeoutError, WorkflowError, ValueError) as exc:
                log_event(sys.stderr, log_path, "review_poll_error", error=str(exc), backoff=backoff_seconds)
                return PollResult(
                    "error", 1, recreate_store=should_recreate_store(exc)
                )

            for issue_id in sorted(failed_review_ids):
                log_event(
                    sys.stderr,
                    log_path,
                    "review_skipped",
                    issue=issue_id,
                    reason="previous_failure",
                )

            if issue is None:
                return PollResult("idle")

            log_event(sys.stderr, log_path, "reviewing", issue=issue.id, agent=agent)
            result = run_review_issue(
                args,
                issue,
                agent=agent,
                config=config,
                cwd=cwd,
                log_path=log_path,
                controller=controller,
                backoff=backoff_seconds,
                store=store,
            )
            if result.status == "error":
                if issue.id is not None:
                    failed_review_ids.add(issue.id)
                return PollResult(
                    "error", 1, result.recreate_store, issue_id=issue.id
                )
            if result.status == "failed":
                if issue.id is not None:
                    failed_review_ids.add(issue.id)
                return PollResult(
                    "failed",
                    result.exit_code,
                    result.recreate_store,
                    issue_id=issue.id,
                )
            return PollResult("success", value=result.reviewed_issue)

        def recreate() -> None:
            nonlocal store
            store = recreate_store(store, config)

        def on_failure_limit(result: PollResult, failures: int) -> None:
            fields: dict[str, object] = {}
            if result.issue_id is not None:
                fields["issue"] = result.issue_id
            fields["failures"] = failures
            log_event(
                sys.stderr,
                log_path,
                "review_failure_limit",
                **fields,
            )

        return run_poll_loop(
            controller,
            backoff,
            poll=poll,
            on_idle=lambda attempt: log_event(
                sys.stderr, log_path, "review_idle", attempt=attempt
            ),
            on_success=lambda result, count: _log_reviewed(log_path, result.value, count),
            on_stopped=lambda: log_event(sys.stderr, log_path, "stopped"),
            once=args.once,
            interval=float(args.interval),
            max_count=args.max_issues,
            max_consecutive_failures=getattr(args, "max_run_failures", 3),
            on_failure_limit=on_failure_limit,
            recreate_store=recreate,
            abort_failed_exit_code=0,
        )
    finally:
        close_store(store)

def _log_reviewed(log_path: Path, reviewed_issue: Issue | None, decided_count: int) -> None:
    if reviewed_issue is None:
        return
    log_event(
        sys.stderr,
        log_path,
        "reviewed",
        issue=reviewed_issue.id,
        assignee=reviewed_issue.assignee,
        stage=reviewed_issue.stage,
        status=reviewed_issue.issue_status,
        count=decided_count,
    )
