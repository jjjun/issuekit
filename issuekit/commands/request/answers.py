"""Answer flows for the PM request command."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from issuekit.agentrun import AgentRunner
from issuekit.agents.router import run_router
from issuekit.commands.request.inbox import ambiguous_answer_message, matched_inbox_questions
from issuekit.commands.request.output import print_payload
from issuekit.commands.request.routing import handle_decision, require_router_config
from issuekit.commands.request.state import (
    SentTarget,
    amended_origin,
    compose_amended_body,
    load_state,
    now,
    qa_rounds,
    refs_by_target_index,
    resolve_depends_on,
    save_state,
    send_target_proposal,
    sent_target_update,
    state_targets,
    target_clarifications,
    target_depends_on,
)
from issuekit.config import IssuekitConfig
from issuekit.proposals import ProposalError
from issuekit.proposals.client import api_client


def run_answer(
    cwd: Path,
    config: IssuekitConfig,
    *,
    request_id: int,
    answer_text: str,
    target_project: str | None,
    json_output: bool,
    dry_run: bool,
    timeout: float,
    model: str | None,
    reasoning_effort: str | None,
) -> int:
    state = load_state(cwd)
    record = state.get(str(request_id))
    if not isinstance(record, dict):
        raise ValueError(f"PM request {request_id} was not found.")
    question = str(record.get("pending_question") or "").strip()
    targets = state_targets(record)
    if question and not targets:
        if target_project:
            raise ValueError(
                f"PM request {request_id} has a pre-routing clarification; do not pass --target."
            )
        return run_pre_routing_answer(
            cwd,
            config,
            state,
            record,
            request_id=request_id,
            question=question,
            answer_text=answer_text,
            json_output=json_output,
            dry_run=dry_run,
            timeout=timeout,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    pending_questions = matched_inbox_questions(config, state, request_id=request_id)
    if target_project:
        pending_questions = [
            item for item in pending_questions if item["target_project"] == target_project
        ]
        if not pending_questions:
            raise ValueError(
                f"PM request {request_id} has no pending clarification for target {target_project}."
            )
    if len(pending_questions) > 1:
        raise ValueError(ambiguous_answer_message(request_id, pending_questions))
    if len(pending_questions) == 1:
        return run_target_reply_answer(
            cwd,
            config,
            state,
            record,
            request_id=request_id,
            answer_text=answer_text,
            pending_question=pending_questions[0],
            json_output=json_output,
            dry_run=dry_run,
        )
    if question:
        raise ValueError(
            f"PM request {request_id} has both a pre-routing clarification and sent targets; "
            "resolve the request state before answering."
        )
    raise ValueError(f"PM request {request_id} has no pending clarification.")


def run_pre_routing_answer(
    cwd: Path,
    config: IssuekitConfig,
    state: dict[str, dict[str, Any]],
    record: dict[str, Any],
    *,
    request_id: int,
    question: str,
    answer_text: str,
    json_output: bool,
    dry_run: bool,
    timeout: float,
    model: str | None,
    reasoning_effort: str | None,
) -> int:
    require_router_config(config)
    qa = qa_rounds(record)
    qa.append({"question": question, "answer": answer_text.strip()})
    if not dry_run:
        record["qa"] = qa
        record.pop("pending_question", None)
        record["updated_at"] = now()
        save_state(cwd, state)
    decision = run_router(
        config,
        cwd,
        request_id=request_id,
        request_text=str(record.get("original_text") or ""),
        qa_rounds=qa,
        force_final=len(qa) >= config.router.max_clarify_rounds,
        timeout=timeout,
        model=model,
        reasoning_effort=reasoning_effort,
        runner_factory=AgentRunner,
        err=sys.stderr,
    )
    return handle_decision(
        cwd,
        config,
        state,
        request_id,
        record,
        decision,
        json_output=json_output,
        dry_run=dry_run,
        force_reject_clarify=len(qa) >= config.router.max_clarify_rounds,
    )


def run_target_reply_answer(
    cwd: Path,
    config: IssuekitConfig,
    state: dict[str, dict[str, Any]],
    record: dict[str, Any],
    *,
    request_id: int,
    answer_text: str,
    pending_question: dict[str, Any],
    json_output: bool,
    dry_run: bool,
) -> int:
    if dry_run:
        return _print_target_reply_dry_run(
            request_id,
            pending_question,
            json_output=json_output,
        )

    targets = state_targets(record)
    target_index = int(pending_question["target_index"])
    target = targets[target_index]
    previous_ref, clarifications, amended_body, depends_on = _prepare_target_reply(
        target,
        pending_question,
        request_id=request_id,
        answer_text=answer_text,
        targets=targets,
    )
    sent_target = _send_target_reply_proposal(
        cwd,
        config,
        target,
        amended_body,
        depends_on,
        request_id=request_id,
        previous_ref=previous_ref,
        round_count=len(clarifications),
    )
    return _finish_target_reply_answer(
        cwd,
        config,
        state,
        record,
        targets,
        target_index,
        target,
        sent_target,
        clarifications,
        request_id=request_id,
        previous_ref=previous_ref,
        pending_question=pending_question,
        json_output=json_output,
    )


def _finish_target_reply_answer(
    cwd: Path,
    config: IssuekitConfig,
    state: dict[str, dict[str, Any]],
    record: dict[str, Any],
    targets: list[dict[str, Any]],
    target_index: int,
    target: dict[str, Any],
    sent_target: SentTarget,
    clarifications: list[dict[str, str]],
    *,
    request_id: int,
    previous_ref: str,
    pending_question: dict[str, Any],
    json_output: bool,
) -> int:
    proposal_ref = sent_target.proposal_ref
    dependency_ref = sent_target.dependency_ref
    updated = sent_target_update(target, sent_target)
    updated["clarifications"] = clarifications
    targets[target_index] = updated
    record["targets"] = targets
    record["updated_at"] = now()
    state[str(request_id)] = record
    save_state(cwd, state)

    with api_client(config) as client:
        client.discard_proposal(int(pending_question["reply_proposal_id"]))

    payload = {
        "request_id": request_id,
        "decision": "answer",
        "target_project": updated.get("project"),
        "proposal_ref": proposal_ref,
        "dependency_ref": dependency_ref,
        "supersedes": previous_ref,
    }
    print_payload(payload, json_output=json_output)
    return 0


def _print_target_reply_dry_run(
    request_id: int,
    pending_question: dict[str, Any],
    *,
    json_output: bool,
) -> int:
    payload = {
        "request_id": request_id,
        "decision": "answer",
        "target_project": pending_question["target_project"],
        "supersedes": pending_question["proposal_ref"],
    }
    print_payload(payload, json_output=json_output)
    return 0


def _prepare_target_reply(
    target: dict[str, Any],
    pending_question: dict[str, Any],
    *,
    request_id: int,
    answer_text: str,
    targets: list[dict[str, Any]],
) -> tuple[str, list[dict[str, str]], str, tuple[str, ...]]:
    previous_ref = str(target.get("proposal_ref") or "").strip()
    if previous_ref != pending_question["proposal_ref"]:
        raise ValueError(
            f"Pending clarification targets {pending_question['proposal_ref']}, "
            f"but request {request_id} now records {previous_ref or 'no proposal'}."
        )
    clarifications = target_clarifications(target)
    clarifications.append(
        {
            "question": str(pending_question.get("question") or "").strip(),
            "answer": answer_text.strip(),
        }
    )
    amended_body = compose_amended_body(
        str(target.get("body") or "").strip(),
        clarifications,
        supersedes=previous_ref,
    )
    depends_on = tuple(
        resolve_depends_on(
            target_depends_on(target),
            refs_by_target_index(targets),
        )
    )
    return previous_ref, clarifications, amended_body, depends_on


def _send_target_reply_proposal(
    cwd: Path,
    config: IssuekitConfig,
    target: dict[str, Any],
    amended_body: str,
    depends_on: tuple[str, ...],
    *,
    request_id: int,
    previous_ref: str,
    round_count: int,
) -> SentTarget:
    sent_target = send_target_proposal(
        cwd,
        config,
        project=str(target["project"]),
        title=str(target.get("title") or ""),
        body=amended_body,
        blocking=bool(target.get("blocking", False)),
        depends_on=depends_on,
        origin=amended_origin(
            config,
            cwd,
            request_id=request_id,
            target_project=str(target["project"]),
            previous_ref=previous_ref,
            round_count=round_count,
        ),
    )
    if sent_target.sent.get("payload_mismatch"):
        raise ProposalError(
            str(sent_target.sent.get("warning") or "Proposal payload mismatch.")
        )
    return sent_target
