"""Routing flows for the PM request command."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import issuekit.proposals.send as proposals_send
from issuekit.agentrun import AgentRunner
from issuekit.agents.router import RouterDecision, RouteTarget, run_router
from issuekit.commands.request.output import print_payload
from issuekit.commands.request.state import (
    PROPOSAL_REF_PATTERN,
    TARGET_PLACEHOLDER_PATTERN,
    find_or_create_request,
    load_state,
    now,
    qa_rounds,
    resolve_depends_on,
    routed_origin,
    save_state,
    state_targets,
    target_depends_on,
    target_state,
)
from issuekit.config import IssuekitConfig
from issuekit.errors import WorkflowError
from issuekit.proposals import ProposalError
from issuekit.proposals.build import build_proposal
from issuekit.proposals.client import api_client


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
        with api_client(config, project=target_project) as client:
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
    existing_targets = state_targets(record)
    existing_indexes: dict[str, int] = {}
    for index, stored in enumerate(existing_targets):
        project = str(stored.get("project") or "")
        if project in existing_indexes:
            raise ProposalError(
                f"PM request {request_id} has multiple saved targets for project {project}."
            )
        existing_indexes[project] = index

    target_indexes: dict[int, int] = {}
    seen_projects: set[str] = set()
    next_index = len(existing_targets)
    for index, target in enumerate(targets):
        if target.project in seen_projects:
            raise ProposalError(
                f"Route decision contains duplicate project target: {target.project}."
            )
        seen_projects.add(target.project)
        if target.project in existing_indexes:
            target_indexes[index] = existing_indexes[target.project]
        else:
            target_indexes[index] = next_index
            existing_indexes[target.project] = next_index
            next_index += 1

    saved_projects = {str(target.get("project") or "") for target in existing_targets}
    for target in targets:
        if target.project in saved_projects:
            continue
        stable_depends_on = tuple(
            _saved_target_reference(ref, target_indexes) for ref in target.depends_on
        )
        existing_targets.append(target_state(replace(target, depends_on=stable_depends_on)))

    record["decision"] = "route"
    record.pop("pending_question", None)
    record["targets"] = existing_targets
    record["updated_at"] = now()
    state[str(request_id)] = record
    save_state(cwd, state)

    refs_by_index: dict[int, str] = {}
    for index, stored in enumerate(existing_targets):
        stored_ref = str(stored.get("proposal_ref") or "").strip()
        if stored_ref:
            refs_by_index[index] = str(stored.get("dependency_ref") or stored_ref)

    pending_indexes = [
        index
        for index, stored in enumerate(existing_targets)
        if not str(stored.get("proposal_ref") or "").strip()
    ]
    while pending_indexes:
        ready_indexes = [
            index
            for index in pending_indexes
            if _target_dependencies_ready(
                target_depends_on(existing_targets[index]), refs_by_index
            )
        ]
        if not ready_indexes:
            raise ProposalError("Saved route targets have unresolved target dependencies.")

        for index in ready_indexes:
            stored = existing_targets[index]
            target = RouteTarget(
                project=str(stored.get("project") or ""),
                title=str(stored.get("title") or ""),
                body=str(stored.get("body") or ""),
                blocking=bool(stored.get("blocking", False)),
                depends_on=target_depends_on(stored),
            )
            resolved_depends_on = resolve_depends_on(target.depends_on, refs_by_index)
            project = target.project
            proposal = build_proposal(
                cwd,
                to=project,
                title=target.title,
                body=target.body,
                body_file=None,
                from_issue=None,
                reply=None,
                blocking=target.blocking,
                depends_on=resolved_depends_on,
            )
            proposal = replace(
                proposal,
                origin=routed_origin(
                    config,
                    cwd,
                    request_id=request_id,
                    target_index=index,
                    target_project=project,
                ),
            )
            sent = proposals_send.send_proposal(config, proposal)
            if sent.get("payload_mismatch"):
                save_state(cwd, state)
                raise ProposalError(
                    _routed_payload_mismatch_message(request_id, project, sent)
                )
            proposal_ref = f"{project}#{sent.get('id')}"
            dependency_ref = str(sent.get("dependency_ref") or proposal_ref)
            refs_by_index[index] = dependency_ref
            updated = dict(stored)
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
            pending_indexes.remove(index)

    return [dict(target) for target in existing_targets]


def _saved_target_reference(ref: str, target_indexes: dict[int, int]) -> str:
    match = TARGET_PLACEHOLDER_PATTERN.match(ref)
    if match is None:
        return ref
    index = int(match.group("index"))
    return f"target:{target_indexes[index]}"


def _target_dependencies_ready(
    depends_on: tuple[str, ...], refs_by_index: dict[int, str]
) -> bool:
    for ref in depends_on:
        match = TARGET_PLACEHOLDER_PATTERN.match(ref)
        if match is not None and int(match.group("index")) not in refs_by_index:
            return False
    return True


def _routed_payload_mismatch_message(
    request_id: int,
    project: str,
    sent: dict[str, Any],
) -> str:
    proposal_id = sent.get("id")
    fields = ", ".join(sent.get("payload_mismatch_fields") or ()) or "payload"
    return (
        f"Proposal was not sent: {project} already has pending proposal #{proposal_id} "
        f"from PM request {request_id} with different {fields}. If that proposal is the "
        f"one this request should use, record it with `issuekit request --link "
        f"{request_id} --target {project} {project}#{proposal_id}`; otherwise withdraw "
        f"it with `issuekit discard {proposal_id} --to {project}` and rerun the request."
    )


def require_router_config(config: IssuekitConfig) -> None:
    if not config.router.agent:
        raise WorkflowError("issuekit request requires [tool.issuekit.router] agent.")
