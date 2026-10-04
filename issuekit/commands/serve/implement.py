"""Issue implementation polling and triage for the serve command."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from issuekit.agentrun.adapter import AgentAdapter
from issuekit.agents.handoff import review_feedback_prompt
from issuekit.agents.implementer_flow import release_claim_after_run_error
from issuekit.agents.run_claimed import run_and_submit
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.errors import AGENT_RUN_ERRORS, WorkflowError
from issuekit.store import get_store
from issuekit.workflow import claim_next

from . import triage
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


@dataclass(frozen=True)
class IssueRunResult:
    status: str
    exit_code: int
    reviewed_issue: Issue | None = None
    recreate_store: bool = False


def run_claimed_issue(
    args,
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    log_path: Path,
    controller: ShutdownController,
    backoff: float,
    adapter: AgentAdapter | None = None,
    store=None,
) -> IssueRunResult:
    try:
        outcome = run_and_submit(
            issue,
            agent=agent,
            config=config,
            cwd=cwd,
            timeout=float(args.timeout_sec),
            model=getattr(args, "model", None),
            reasoning_effort=getattr(args, "reasoning_effort", None),
            adapter=adapter,
            prompt_suffix=review_feedback_prompt(issue.body),
            abort_event=controller.abort_event,
            store=store,
            out=sys.stderr,
            err=sys.stderr,
            allow_any_branch=getattr(args, "allow_any_branch", False),
        )
    except AGENT_RUN_ERRORS as exc:
        log_event(
            sys.stderr, log_path, "run_error", issue=issue.id, error=str(exc), backoff=backoff
        )
        return IssueRunResult("error", 1, recreate_store=should_recreate_store(exc))

    if outcome.exit_code != 0 or outcome.reviewed_issue is None:
        log_event(
            sys.stderr,
            log_path,
            "run_failed",
            issue=issue.id,
            exit_code=outcome.exit_code,
            backoff=backoff,
        )
        return IssueRunResult("failed", outcome.exit_code)

    return IssueRunResult("submitted", 0, reviewed_issue=outcome.reviewed_issue)

def recover_orphaned_issues(config: IssuekitConfig, *, store) -> list[Issue]:
    """Find this checkout's active claims for startup recovery and poll retries."""

    if config.worker is None or config.worker_key() is None or store is None:
        return []
    return store.find_implementing_for_workers(config.worker_lookup_keys())


class _ImplementPoller:
    def __init__(
        self,
        args,
        *,
        agent: str,
        config: IssuekitConfig,
        cwd: Path,
        log_path: Path,
        controller: ShutdownController,
        adapter: AgentAdapter | None,
        store,
    ) -> None:
        self.args = args
        self.agent = agent
        self.config = config
        self.cwd = cwd
        self.log_path = log_path
        self.controller = controller
        self.adapter = adapter
        self.store = store
        self.pending_holds: dict[int, tuple[str, str]] = {}
        self.attempted_issue_ids: set[int] = set()
        self.last_failed_issue_id: int | None = None

    def poll(self, _attempt: int, backoff_seconds: float) -> PollResult:
        hold_result = triage.retry_pending_holds(
            self.pending_holds,
            config=self.config,
            log_path=self.log_path,
            backoff_seconds=backoff_seconds,
        )
        if hold_result is not None:
            return hold_result

        binary_result = self._check_binary(backoff_seconds)
        if binary_result is not None:
            return binary_result

        triage_result = self._triage_step(backoff_seconds)
        if triage_result is not None:
            return triage_result

        issue = self._claim(backoff_seconds)
        if isinstance(issue, PollResult):
            return issue
        if issue is None:
            return PollResult(status="idle")
        return self._run_claimed(issue, backoff_seconds)

    def _check_binary(self, backoff_seconds: float) -> PollResult | None:
        if self.adapter is None:
            return None
        try:
            self.adapter.resolve_binary()
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            log_event(
                sys.stderr,
                self.log_path,
                "preflight_error",
                error=str(exc),
                backoff=backoff_seconds,
            )
            return PollResult(status="error", exit_code=1)
        return None

    def _triage_step(self, backoff_seconds: float) -> PollResult | None:
        return triage.run_triage_step(self, backoff_seconds)

    def _claim(self, backoff_seconds: float) -> Issue | None | PollResult:
        try:
            implementing_issues = recover_orphaned_issues(
                self.config,
                store=self.store,
            )
        except (RuntimeError, TimeoutError, ValueError) as exc:
            log_event(sys.stderr, self.log_path, "recovery_error", error=str(exc))
            return PollResult(
                status="error",
                exit_code=1,
                recreate_store=should_recreate_store(exc),
            )

        if implementing_issues:
            issue = implementing_issues[0]
            event = "retrying" if issue.id in self.attempted_issue_ids else "recovered"
            log_event(sys.stderr, self.log_path, event, issue=issue.id, agent=self.agent)
        else:
            self.last_failed_issue_id = None
            try:
                issue = claim_next(
                    self.agent,
                    priority=self.args.priority,
                    config=self.config,
                    store=self.store,
                    cwd=self.cwd,
                    allow_any_branch=getattr(self.args, "allow_any_branch", False),
                    no_sync=getattr(self.args, "no_sync", False),
                )
            except (TimeoutError, WorkflowError, ValueError) as exc:
                log_event(
                    sys.stderr,
                    self.log_path,
                    "claim_error",
                    error=str(exc),
                    backoff=backoff_seconds,
                )
                return PollResult(
                    status="error",
                    exit_code=1,
                    recreate_store=should_recreate_store(exc),
                )

        if issue is None:
            return None
        if issue.id is not None:
            self.attempted_issue_ids.add(issue.id)
        if not implementing_issues:
            log_event(sys.stderr, self.log_path, "claimed", issue=issue.id, agent=self.agent)
        return issue

    def _run_claimed(self, issue: Issue, backoff_seconds: float) -> PollResult:
        result = run_claimed_issue(
            self.args,
            issue,
            agent=self.agent,
            config=self.config,
            cwd=self.cwd,
            log_path=self.log_path,
            controller=self.controller,
            backoff=backoff_seconds,
            adapter=self.adapter,
            store=self.store,
        )
        if result.status == "error":
            self.last_failed_issue_id = issue.id
            return PollResult("error", 1, result.recreate_store, issue_id=issue.id)
        if result.status == "failed":
            self.last_failed_issue_id = issue.id
            return PollResult(
                "failed",
                result.exit_code,
                result.recreate_store,
                issue_id=issue.id,
            )
        self.last_failed_issue_id = None
        return PollResult(
            "success",
            recreate_store=result.recreate_store,
            value=result.reviewed_issue,
        )

    def recreate_store(self) -> None:
        self.store = recreate_store(self.store, self.config)


def run_implement_loop(
    args,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    log_path: Path,
    controller: ShutdownController,
    adapter: AgentAdapter | None = None,
) -> int:
    store = get_store(config) if config.api_url else None
    poller = _ImplementPoller(
        args,
        agent=agent,
        config=config,
        cwd=cwd,
        log_path=log_path,
        controller=controller,
        adapter=adapter,
        store=store,
    )

    def on_failure_limit(result: PollResult, failures: int) -> None:
        issue_id = result.issue_id or poller.last_failed_issue_id
        fields: dict[str, object] = {}
        if issue_id is not None:
            fields["issue"] = issue_id
        fields["failures"] = failures
        log_event(sys.stderr, log_path, "run_failure_limit", **fields)
        if issue_id is not None:
            release_claim_after_run_error(
                issue_id,
                config=config,
                store=poller.store,
                err=sys.stderr,
                reason=f"serve stopped after {failures} consecutive failed runs",
            )

    try:
        return run_poll_loop(
            controller,
            Backoff(),
            poll=poller.poll,
            on_idle=lambda attempt: log_event(
                sys.stderr, log_path, "idle", attempt=attempt
            ),
            on_success=lambda poll_result, count: _log_submitted(
                log_path, poll_result.value, count
            ),
            on_stopped=lambda: log_event(sys.stderr, log_path, "stopped"),
            once=args.once,
            interval=float(args.interval),
            max_count=args.max_issues,
            max_consecutive_failures=getattr(args, "max_run_failures", 3),
            on_failure_limit=on_failure_limit,
            recreate_store=poller.recreate_store,
            abort_failed_exit_code=0,
        )
    finally:
        close_store(poller.store)


def _log_submitted(log_path: Path, reviewed_issue: Issue | None, submitted_count: int) -> None:
    if reviewed_issue is None:
        return
    log_event(
        sys.stderr,
        log_path,
        "submitted",
        issue=reviewed_issue.id,
        assignee=reviewed_issue.assignee,
        stage=reviewed_issue.stage,
        count=submitted_count,
    )
