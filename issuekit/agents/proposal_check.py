"""Worker-side proposal-check evaluation flow."""

from __future__ import annotations

import re
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TextIO

from issuekit.agentrun import AgentRunner
from issuekit.agents.proposal_eval import (
    proposal_dependencies_text,
    run_readonly_proposal_evaluation,
)
from issuekit.agents.readonly import prompt_from_spec
from issuekit.agents.registry import resolve_adapter
from issuekit.config import IssuekitConfig
from issuekit.encoding import has_non_ascii
from issuekit.prompts import (
    PROPOSAL_CHECK_PROMPT,
    ProposalCheckParseError,
    canonical_contract_token,
)
from issuekit.proposals.api import ProposalError, adopt_proposal_with_append, api_client
from issuekit.workflow import WorkflowError

PROPOSAL_CHECK_VERDICTS = {"approve", "reject", "revise"}
PROPOSAL_CHECK_COMMENT_MAX = 100000
ADOPTED_ISSUE_REF_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}#[1-9][0-9]*$")

LogFn = Callable[..., None]


@dataclass(frozen=True)
class ProposalCheckDecision:
    check_id: int
    target_project: str
    proposal_id: int
    verdict: str
    comment: str
    adopted_issue_ref: str | None = None
    status: str = "answered"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "check_id": self.check_id,
            "target_project": self.target_project,
            "proposal_id": self.proposal_id,
            "verdict": self.verdict,
            "comment": self.comment,
            "status": self.status,
        }
        if self.adopted_issue_ref is not None:
            data["adopted_issue_ref"] = self.adopted_issue_ref
        if self.error is not None:
            data["error"] = self.error
        return data


def run_proposal_check_cycle(
    config: IssuekitConfig,
    cwd: Path,
    *,
    agent: str,
    timeout: float = 600.0,
    model: str | None = None,
    reasoning_effort: str | None = None,
    limit: int = 50,
    runner_factory=None,
    log: LogFn | None = None,
    err: TextIO | None = None,
    abort_event: threading.Event | None = None,
) -> list[ProposalCheckDecision]:
    """Run one worker-side proposal-check poll/evaluate/result cycle."""

    worker_keys = config.worker_lookup_keys()
    if not worker_keys:
        raise WorkflowError(
            "proposal-checks require this checkout to be registered as a worker."
        )
    if limit < 1:
        raise ValueError("limit must be greater than zero.")

    err = err or sys.stderr
    emit = log or (lambda *args, **kwargs: None)
    runner_factory = runner_factory or AgentRunner
    adapter = resolve_adapter(
        agent,
        config=config,
        model=model,
        reasoning_effort=reasoning_effort,
        role="triage",
    )

    with api_client(config) as client:
        checks = _poll_worker_checks(
            client,
            worker_keys,
            status="pending",
            limit=min(limit, 500),
            offset=0,
        )

    decisions: list[ProposalCheckDecision] = []
    for check in checks:
        if abort_event is not None and abort_event.is_set():
            break
        check_id = int(check["id"])
        try:
            decision = _process_proposal_check(
                check,
                config=config,
                agent=agent,
                adapter=adapter,
                cwd=cwd,
                timeout=timeout,
                runner_factory=runner_factory,
                err=err,
                abort_event=abort_event,
            )
            decisions.append(decision)
            if decision.status == "already_decided":
                emit("proposal_check_already_decided", check=check_id)
                continue
            emit(
                "proposal_check_decision",
                check=check_id,
                proposal=decision.proposal_id,
                verdict=decision.verdict,
                adopted_issue_ref=decision.adopted_issue_ref,
            )
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            TimeoutError,
            ProposalError,
            ProposalCheckParseError,
            WorkflowError,
        ) as exc:
            emit("proposal_check_error", check=check_id, error=str(exc))
            decisions.append(_error_decision(check, exc))
    return decisions


def list_worker_proposal_checks(
    config: IssuekitConfig,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List proposal checks addressed to this registered checkout."""

    worker_keys = config.worker_lookup_keys()
    if not worker_keys:
        raise WorkflowError(
            "proposal-checks require this checkout to be registered as a worker."
        )
    if limit < 1:
        raise ValueError("limit must be greater than zero.")
    if offset < 0:
        raise ValueError("offset must be zero or greater.")

    with api_client(config) as client:
        if status is not None:
            return _poll_worker_checks(
                client,
                worker_keys,
                status=status,
                limit=min(limit, 500),
                offset=offset,
            )
        checks = _list_worker_checks(client, worker_keys)
    return checks[offset : offset + limit]


def _poll_worker_checks(
    client,
    worker_keys: tuple[str, ...],
    *,
    status: str,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    streams: list[list[dict[str, Any]]] = []
    needed = offset + limit
    for target_worker in worker_keys:
        stream: list[dict[str, Any]] = []
        worker_offset = 0
        while len(stream) < needed:
            page_limit = min(needed - len(stream), 500)
            page = client.poll_proposal_checks(
                target_worker=target_worker,
                status=status,
                limit=page_limit,
                offset=worker_offset,
            )
            stream.extend(page)
            if len(page) < page_limit:
                break
            worker_offset += len(page)
        streams.append(stream)
    return _merge_worker_check_streams(streams)[offset : offset + limit]


def _list_worker_checks(client, worker_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    streams = [
        client.list_proposal_checks(target_worker=target_worker, status=None)
        for target_worker in worker_keys
    ]
    return _merge_worker_check_streams(streams)


def _merge_worker_check_streams(
    streams: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}
    for stream in streams:
        for check in stream:
            by_id.setdefault(int(check["id"]), check)
    return [by_id[check_id] for check_id in sorted(by_id)]


def _process_proposal_check(
    check: Mapping[str, Any],
    *,
    config: IssuekitConfig,
    agent: str,
    adapter: object,
    cwd: Path,
    timeout: float,
    runner_factory,
    err: TextIO,
    abort_event: threading.Event | None,
) -> ProposalCheckDecision:
    check_id = int(check["id"])
    target_project = str(check["target_project"])
    proposal_id = int(check["proposal_id"])
    with api_client(config, project=target_project) as client:
        proposal = client.get_proposal(proposal_id)
    parsed = _evaluate_check(
        check,
        proposal,
        agent=agent,
        adapter=adapter,
        cwd=cwd,
        timeout=timeout,
        runner_factory=runner_factory,
        err=err,
        abort_event=abort_event,
    )
    adopted_issue_ref = None
    if parsed["verdict"] == "approve":
        adopted_issue_ref = _recorded_adopted_issue_ref(proposal, target_project)
        if adopted_issue_ref is None:
            outcome = adopt_proposal_with_append(
                replace(config, project=target_project),
                proposal_id,
                priority=config.triage.default_priority,
                append_text=parsed.get("spec_markdown") or None,
            )
            adopted_issue_ref = _valid_adopted_issue_ref(outcome.get("issue_ref"))
    try:
        result = _post_result(
            config,
            check_id=check_id,
            target_project=target_project,
            verdict=parsed["verdict"],
            comment=parsed["comment"],
            adopted_issue_ref=adopted_issue_ref,
        )
    except WorkflowError as exc:
        if exc.code != "already_decided":
            raise
        return ProposalCheckDecision(
            check_id=check_id,
            target_project=target_project,
            proposal_id=proposal_id,
            verdict="",
            comment="",
            status="already_decided",
        )
    return ProposalCheckDecision(
        check_id=check_id,
        target_project=target_project,
        proposal_id=proposal_id,
        verdict=parsed["verdict"],
        comment=parsed["comment"],
        adopted_issue_ref=adopted_issue_ref,
        status=str(result.get("status", "answered")),
    )


def _error_decision(
    check: Mapping[str, Any],
    error: Exception,
) -> ProposalCheckDecision:
    return ProposalCheckDecision(
        check_id=int(check["id"]),
        target_project=str(check["target_project"]),
        proposal_id=int(check["proposal_id"]),
        verdict="error",
        comment="",
        status="error",
        error=str(error),
    )


def parse_proposal_check_output(stdout: str) -> dict[str, str]:
    raw = PROPOSAL_CHECK_PROMPT.parse_json(stdout)
    raw_verdict = raw.get("verdict")
    if canonical_contract_token(raw_verdict, ("ok",)) == "ok":
        raw_verdict = "approve"
    verdict = canonical_contract_token(raw_verdict, PROPOSAL_CHECK_VERDICTS)
    if verdict is None:
        raise ProposalCheckParseError(
            f"Invalid proposal-check verdict: {raw_verdict!r}"
        )
    comment = raw.get("comment")
    if not isinstance(comment, str) or not comment.strip():
        raise ProposalCheckParseError("Proposal-check verdict requires a non-empty comment.")
    comment = comment.strip()
    if len(comment) > PROPOSAL_CHECK_COMMENT_MAX:
        raise ProposalCheckParseError(
            f"Proposal-check comment must be at most {PROPOSAL_CHECK_COMMENT_MAX} characters."
        )
    spec_markdown = raw.get("spec_markdown")
    if spec_markdown is not None and not isinstance(spec_markdown, str):
        raise ProposalCheckParseError("Proposal-check spec_markdown must be a string.")
    spec = str(spec_markdown or "").strip()
    if has_non_ascii("\n".join((verdict, comment, spec))):
        raise ProposalCheckParseError("Proposal-check fields must be ASCII-only.")
    parsed = {"verdict": verdict, "comment": comment}
    if spec:
        parsed["spec_markdown"] = spec
    return parsed


def _evaluate_check(
    check: Mapping[str, Any],
    proposal: Mapping[str, Any],
    *,
    agent: str,
    adapter: object,
    cwd: Path,
    timeout: float,
    runner_factory,
    err: TextIO,
    abort_event: threading.Event | None = None,
) -> dict[str, str]:
    check_id = int(check["id"])
    proposal_id = int(check["proposal_id"])
    stdout = run_readonly_proposal_evaluation(
        proposal,
        agent=agent,
        adapter=adapter,
        cwd=cwd,
        timeout=timeout,
        runner_factory=runner_factory,
        err=err,
        prompt=prompt_from_spec(
            PROPOSAL_CHECK_PROMPT,
            cwd=cwd,
            filename=f"proposal-check-{check_id}.md",
            body=_render_check_prompt(check, proposal),
        ),
        label="Proposal-check",
        mutation_log_message=(
            "ERROR: proposal-check run modified repository state; ignoring its output."
        ),
        abort_event=abort_event,
    )
    try:
        return parse_proposal_check_output(stdout)
    except ProposalCheckParseError as exc:
        raise ProposalCheckParseError(
            f"Could not parse proposal-check output for proposal #{proposal_id}: {exc}"
        ) from exc


def _post_result(
    config: IssuekitConfig,
    *,
    check_id: int,
    target_project: str,
    verdict: str,
    comment: str,
    adopted_issue_ref: str | None,
) -> dict[str, Any]:
    with api_client(config, project=target_project) as client:
        return client.post_proposal_check_result(
            check_id,
            project=target_project,
            verdict=verdict,
            comment=comment,
            adopted_issue_ref=adopted_issue_ref,
        )


def _valid_adopted_issue_ref(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if not ADOPTED_ISSUE_REF_PATTERN.match(value):
        return None
    return value


def _recorded_adopted_issue_ref(
    proposal: Mapping[str, Any],
    target_project: str,
) -> str | None:
    if proposal.get("status") != "adopted":
        return None
    try:
        issue_number = int(proposal.get("adopted_issue_number"))
    except (TypeError, ValueError):
        return None
    return _valid_adopted_issue_ref(f"{target_project}#{issue_number}")


def _render_check_prompt(
    check: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> str:
    return PROPOSAL_CHECK_PROMPT.render(
        check_id=check["id"],
        target_project=check.get("target_project", ""),
        proposal_id=proposal.get("id", ""),
        title=proposal.get("title", ""),
        origin=proposal.get("origin", ""),
        blocking=bool(proposal.get("blocking", False)),
        depends_on=proposal_dependencies_text(proposal),
        proposal_body=proposal.get("body", ""),
    )
