"""Routing flows for the PM request command."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import issuekit.proposals.api as proposals_api
from issuekit.agentrun import AgentRunner
from issuekit.agents.router import RouterDecision, RouteTarget, run_router
from issuekit.commands.request.output import print_payload
from issuekit.commands.request.state import (
    PROPOSAL_REF_PATTERN,
    find_or_create_request,
    load_state,
    now,
    qa_rounds,
    resolve_depends_on,
    save_state,
    state_targets,
    target_state,
)
from issuekit.config import IssuekitConfig
from issuekit.proposals import ProposalError
from issuekit.workflow import WorkflowError


def run_link(
    cwd: Path,
    config: IssuekitConfig,
    *,
    request_id: int,
    target_project: str,
    proposal_ref: str,
    json_output: bool,
) -> int:
    state = load_state(cwd)
    record = state.get(str(request_id))
    if not isinstance(record, dict):
        raise ValueError(f"PM request {request_id} was not found.")

    ref = proposal_ref.strip()
    match = PROPOSAL_REF_PATTERN.match(ref)
    if match is None:
        raise ValueError(f"Invalid proposal ref: {proposal_ref}. Expected project#id.")
    if match.group("project") != target_project:
        raise ValueError(
            f"Proposal ref {ref} targets {match.group('project')}, not {target_project}."
        )
    proposal_id = int(match.group("id"))

    targets = state_targets(record)
    matching_targets = [
        (index, target)
        for index, target in enumerate(targets)
        if str(target.get("project") or "") == target_project
    ]
    if not matching_targets:
        raise ValueError(
            f"PM request {request_id} has no target for project {target_project}."
        )
    unsent_targets = [
        (index, target)
        for index, target in matching_targets
        if not str(target.get("proposal_ref") or "").strip()
    ]
    if not unsent_targets:
        raise ValueError(
            f"PM request {request_id} target {target_project} is already sent or linked."
        )
    if len(unsent_targets) > 1:
        raise ValueError(
            f"PM request {request_id} has multiple unsent targets for project {target_project}."
        )

    try:
        with proposals_api.api_client(config, project=target_project) as client:
            proposal = client.get_proposal(proposal_id)
    except WorkflowError as exc:
        if exc.code in {"not_found", "http_404"}:
            raise ValueError(f"Proposal {ref} was not found in {target_project}.") from exc
        raise

    target_index, target = unsent_targets[0]
    updated = dict(target)
    updated.update(
        {
            "proposal_ref": ref,
            "proposal_id": proposal_id,
            "linked_at": now(),
            "status": str(proposal.get("status") or "linked"),
        }
    )
    targets[target_index] = updated
    record["targets"] = targets
    record["updated_at"] = now()
    state[str(request_id)] = record
    save_state(cwd, state)

    payload = {
        "request_id": request_id,
        "decision": "link",
        "target_project": target_project,
        "proposal_ref": ref,
    }
    print_payload(payload, json_output=json_output)
    return 0


def run_new_request(
    cwd: Path,
    config: IssuekitConfig,
    *,
    request_text: str,
    json_output: bool,
    dry_run: bool,
    timeout: float,
    model: str | None,
    reasoning_effort: str | None,
) -> int:
    require_router_config(config)
    state = load_state(cwd)
    request_id, record = find_or_create_request(state, request_text)
    decision = run_router(
        config,
        cwd,
        request_id=request_id,
        request_text=request_text,
        qa_rounds=qa_rounds(record),
        force_final=len(qa_rounds(record)) >= config.router.max_clarify_rounds,
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
        force_reject_clarify=len(qa_rounds(record)) >= config.router.max_clarify_rounds,
    )


def handle_decision(
    cwd: Path,
    config: IssuekitConfig,
    state: dict[str, dict[str, Any]],
    request_id: int,
    record: dict[str, Any],
    decision: RouterDecision,
    *,
    json_output: bool,
    dry_run: bool,
    force_reject_clarify: bool = False,
) -> int:
    if dry_run:
        payload = {"request_id": request_id, **decision.to_dict()}
        print_payload(payload, json_output=json_output)
        return 0

    if decision.decision == "clarify" and force_reject_clarify:
        decision = RouterDecision(
            decision="reject",
            reason=(
                "Clarification limit reached and the router still requested "
                "clarification."
            ),
        )

    if decision.decision == "clarify":
        record["decision"] = "clarify"
        record["pending_question"] = decision.question
        record["updated_at"] = now()
        state[str(request_id)] = record
        save_state(cwd, state)
        payload = {
            "request_id": request_id,
            "decision": "clarify",
            "question": decision.question,
        }
        print_payload(payload, json_output=json_output)
        return 0

    if decision.decision == "reject":
        record["decision"] = "reject"
        record["reason"] = decision.reason
        record.pop("pending_question", None)
        record["updated_at"] = now()
        state[str(request_id)] = record
        save_state(cwd, state)
        payload = {
            "request_id": request_id,
            "decision": "reject",
            "reason": decision.reason,
        }
        print_payload(payload, json_output=json_output)
        return 0

    sent_targets = send_route_targets(
        cwd,
        config,
        state,
        request_id,
        record,
        decision.targets,
    )
    payload = {
        "request_id": request_id,
        "decision": "route",
        "targets": sent_targets,
    }
    print_payload(payload, json_output=json_output)
    return 0


def send_route_targets(
    cwd: Path,
    config: IssuekitConfig,
    state: dict[str, dict[str, Any]],
    request_id: int,
    record: dict[str, Any],
    targets: tuple[RouteTarget, ...],
) -> list[dict[str, Any]]:
    record["decision"] = "route"
    record.pop("pending_question", None)
    existing_targets = state_targets(record)
    while len(existing_targets) < len(targets):
        target = targets[len(existing_targets)]
        existing_targets.append(target_state(target))
    record["targets"] = existing_targets
    record["updated_at"] = now()
    state[str(request_id)] = record
    save_state(cwd, state)

    refs_by_index: dict[int, str] = {}
    output: list[dict[str, Any]] = []
    for index, target in enumerate(targets):
        stored = existing_targets[index]
        stored_ref = str(stored.get("proposal_ref") or "").strip()
        if stored_ref:
            refs_by_index[index] = str(stored.get("dependency_ref") or stored_ref)
            output.append(dict(stored))
            continue
        resolved_depends_on = resolve_depends_on(target.depends_on, refs_by_index)
        proposal = proposals_api.build_proposal(
            cwd,
            to=target.project,
            title=target.title,
            body=target.body,
            body_file=None,
            from_issue=None,
            reply=None,
            blocking=target.blocking,
            depends_on=resolved_depends_on,
        )
        sent = proposals_api.send_proposal(config, proposal)
        if sent.get("payload_mismatch"):
            save_state(cwd, state)
            raise ProposalError(str(sent.get("warning") or "Proposal payload mismatch."))
        proposal_ref = f"{target.project}#{sent.get('id')}"
        dependency_ref = str(sent.get("dependency_ref") or proposal_ref)
        refs_by_index[index] = dependency_ref
        updated = target_state(target)
        updated.update(
            {
                "proposal_ref": proposal_ref,
                "dependency_ref": dependency_ref,
                "proposal_id": sent.get("id"),
                "sent_at": now(),
            }
        )
        existing_targets[index] = updated
        record["targets"] = existing_targets
        record["updated_at"] = now()
        save_state(cwd, state)
        output.append(dict(updated))
    return output


def require_router_config(config: IssuekitConfig) -> None:
    if not config.router.agent:
        raise WorkflowError("issuekit request requires [tool.issuekit.router] agent.")
