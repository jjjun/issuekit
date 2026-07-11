"""Worker-side proposal-check evaluation flow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
import re
import sys
import threading
from typing import Any, TextIO

from issuekit.agents.proposal_eval import (
    run_readonly_proposal_evaluation,
)
from issuekit.agents.runner import AgentRunner, resolve_adapter
from issuekit.config import IssuekitConfig
from issuekit.core import has_non_ascii
from issuekit.prompts import PROPOSAL_CHECK_PROMPT, ProposalCheckParseError
from issuekit.proposals_api import ProposalError, adopt_proposal_with_append, api_client
from issuekit.workflow import WorkflowError


PROPOSAL_CHECK_BLOCK_LANGUAGE = PROPOSAL_CHECK_PROMPT.block_language
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
    limit: int = 50,
    runner_factory=None,
    log: LogFn | None = None,
    out: TextIO | None = None,
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

    out = out or sys.stdout
    err = err or sys.stderr
    emit = log or (lambda *args, **kwargs: None)
    runner_factory = runner_factory or AgentRunner
    adapter = resolve_adapter(agent, config=config, model=model)

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
        check_id = int(check["id"])
        target_project = str(check["target_project"])
        proposal_id = int(check["proposal_id"])
        try:
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
            append_text = parsed.get("spec_markdown") or None
            if parsed["verdict"] == "approve":
                outcome = adopt_proposal_with_append(
                    replace(config, project=target_project),
                    proposal_id,
                    priority=config.triage.default_priority,
                    append_text=append_text,
                )
                adopted_issue_ref = _valid_adopted_issue_ref(
                    outcome.get("issue_ref")
                )
            result = _post_result(
                config,
                check_id=check_id,
                target_project=target_project,
                verdict=parsed["verdict"],
                comment=parsed["comment"],
                adopted_issue_ref=adopted_issue_ref,
            )
            decisions.append(
                ProposalCheckDecision(
                    check_id=check_id,
                    target_project=target_project,
                    proposal_id=proposal_id,
                    verdict=parsed["verdict"],
                    comment=parsed["comment"],
                    adopted_issue_ref=adopted_issue_ref,
                    status=str(result.get("status", "answered")),
                )
            )
            emit(
                "proposal_check_decision",
                check=check_id,
                proposal=proposal_id,
                verdict=parsed["verdict"],
                adopted_issue_ref=adopted_issue_ref,
            )
        except WorkflowError as exc:
            if exc.code == "already_decided":
                decisions.append(
                    ProposalCheckDecision(
                        check_id=check_id,
                        target_project=target_project,
                        proposal_id=proposal_id,
                        verdict="",
                        comment="",
                        status="already_decided",
                    )
                )
                emit("proposal_check_already_decided", check=check_id)
                continue
            emit("proposal_check_error", check=check_id, error=str(exc))
            decisions.append(
                ProposalCheckDecision(
                    check_id=check_id,
                    target_project=target_project,
                    proposal_id=proposal_id,
                    verdict="error",
                    comment="",
                    status="error",
                    error=str(exc),
                )
            )
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            TimeoutError,
            ProposalError,
            ProposalCheckParseError,
        ) as exc:
            emit("proposal_check_error", check=check_id, error=str(exc))
            decisions.append(
                ProposalCheckDecision(
                    check_id=check_id,
                    target_project=target_project,
                    proposal_id=proposal_id,
                    verdict="error",
                    comment="",
                    status="error",
                    error=str(exc),
                )
            )
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
    seen: set[int] = set()
    checks: list[dict[str, Any]] = []
    for target_worker in worker_keys:
        for check in client.poll_proposal_checks(
            target_worker=target_worker,
            status=status,
            limit=limit,
            offset=offset,
        ):
            check_id = int(check["id"])
            if check_id in seen:
                continue
            seen.add(check_id)
            checks.append(check)
    return checks[:limit]


def _list_worker_checks(client, worker_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    checks: list[dict[str, Any]] = []
    for target_worker in worker_keys:
        for check in client.list_proposal_checks(target_worker=target_worker, status=None):
            check_id = int(check["id"])
            if check_id in seen:
                continue
            seen.add(check_id)
            checks.append(check)
    return checks


def parse_proposal_check_output(stdout: str) -> dict[str, str]:
    raw = PROPOSAL_CHECK_PROMPT.parse_json(stdout)
    verdict = raw.get("verdict")
    if verdict == "ok":
        verdict = "approve"
    if not isinstance(verdict, str) or verdict not in PROPOSAL_CHECK_VERDICTS:
        raise ProposalCheckParseError(f"Invalid proposal-check verdict: {verdict!r}")
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
    prompt_path = cwd / ".agent-runs" / f"proposal-check-{check_id}.md"
    stdout = run_readonly_proposal_evaluation(
        proposal,
        agent=agent,
        adapter=adapter,
        cwd=cwd,
        timeout=timeout,
        runner_factory=runner_factory,
        err=err,
        prompt_filename=prompt_path.name,
        prompt_text=_render_check_prompt(check, proposal),
        prompt_override=_prompt_pointer(prompt_path),
        label="Proposal-check",
        mutation_log_message=(
            "ERROR: proposal-check run modified the worktree; ignoring its output."
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


def _render_check_prompt(
    check: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> str:
    depends_on = proposal.get("depends_on") or []
    if isinstance(depends_on, (list, tuple)):
        depends_text = ", ".join(str(ref) for ref in depends_on) or "(none)"
    else:
        depends_text = str(depends_on)
    return PROPOSAL_CHECK_PROMPT.render(
        check_id=check["id"],
        target_project=check.get("target_project", ""),
        proposal_id=proposal.get("id", ""),
        title=proposal.get("title", ""),
        origin=proposal.get("origin", ""),
        blocking=bool(proposal.get("blocking", False)),
        depends_on=depends_text,
        proposal_body=proposal.get("body", ""),
    )


def _prompt_pointer(prompt_path: Path) -> str:
    return PROPOSAL_CHECK_PROMPT.render_pointer(prompt_path=prompt_path)
