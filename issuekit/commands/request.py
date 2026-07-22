"""PM request router command."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import re
import sys
from typing import Any

from issuekit.agentrun import AgentRunner
from issuekit.agents.router import (
    RouterDecision,
    RouterParseError,
    RouteTarget,
    run_router,
)
from issuekit.commands._common import run_command
from issuekit.commands.request_output import print_payload, print_status_record
from issuekit.commands.request_state import (
    STATE_FILENAME,
    _load_state,
    _now,
    _save_state,
    _state_path,
)
from issuekit.config import IssuekitConfig, load_config
from issuekit.gitutil import git_short_head
import issuekit.proposals.api as proposals_api
from issuekit.proposals import ProposalError
from issuekit.workflow import WorkflowError


_PROPOSAL_REF_PATTERN = re.compile(
    r"^(?P<project>[A-Za-z0-9_.-]+)#(?P<id>[1-9][0-9]*)$"
)
_REPLY_TITLE_PATTERN = re.compile(
    r"^Re:\s*(?P<project>[A-Za-z0-9_.-]+)#(?P<id>[1-9][0-9]*):\s*(?P<title>.*)$"
)
_TARGET_PLACEHOLDER_PATTERN = re.compile(r"^target:(?P<index>[0-9]+)$")


def register(subparsers: argparse._SubParsersAction) -> None:
    request_parser = subparsers.add_parser(
        "request",
        help="Route a PM request to owning project proposal inboxes.",
    )
    request_parser.add_argument("text", nargs="?", help="Request text or clarification answer.")
    request_parser.add_argument(
        "--answer",
        type=int,
        metavar="REQUEST_ID",
        help="Answer a pending clarification for a recorded request.",
    )
    request_parser.add_argument(
        "--status",
        nargs="?",
        const="all",
        metavar="REQUEST_ID",
        help="Show routed proposal status for one request or all requests.",
    )
    request_parser.add_argument(
        "--inbox",
        action="store_true",
        help="Show pending target clarification replies in the PM project inbox.",
    )
    request_parser.add_argument(
        "--target",
        help="Target project whose pending clarification reply is being answered.",
    )
    request_parser.add_argument(
        "--link",
        type=int,
        metavar="REQUEST_ID",
        help="Link an existing proposal ref to an unsent routed request target.",
    )
    request_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    request_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the parsed router decision without sending proposals.",
    )
    request_parser.add_argument(
        "--timeout-sec",
        type=float,
        default=600.0,
        help="Hard timeout for the router agent run in seconds.",
    )
    request_parser.set_defaults(func=run)


def run(args) -> int:
    cwd = Path.cwd()
    config = load_config(cwd)

    def action() -> int:
        if args.inbox:
            if (
                args.status is not None
                or args.answer is not None
                or args.link is not None
                or args.text is not None
                or args.dry_run
            ):
                raise ValueError("--inbox cannot be combined with request text, --answer, --status, or --dry-run.")
            return _run_inbox(cwd, config, json_output=args.json)
        if args.status is not None:
            if (
                args.answer is not None
                or args.link is not None
                or args.text is not None
                or args.dry_run
                or args.target
            ):
                raise ValueError("--status cannot be combined with request text, --answer, --link, --target, or --dry-run.")
            return _run_status(cwd, config, request_id_arg=args.status, json_output=args.json)
        if args.link is not None:
            if args.answer is not None or args.inbox or args.dry_run:
                raise ValueError("--link cannot be combined with --answer, --inbox, or --dry-run.")
            if not args.target:
                raise ValueError("request --link requires --target.")
            if not args.text:
                raise ValueError("request --link requires a proposal ref.")
            return _run_link(
                cwd,
                config,
                request_id=int(args.link),
                target_project=str(args.target),
                proposal_ref=str(args.text),
                json_output=args.json,
            )
        if args.answer is not None:
            if not args.text:
                raise ValueError("request --answer requires answer text.")
            return _run_answer(
                cwd,
                config,
                request_id=int(args.answer),
                answer_text=str(args.text),
                target_project=args.target,
                json_output=args.json,
                dry_run=args.dry_run,
                timeout=float(args.timeout_sec),
            )
        if args.target:
            raise ValueError("--target can only be used with --answer or --link.")
        if not args.text:
            raise ValueError("issuekit request requires request text, --answer, --inbox, --link, or --status.")
        return _run_new_request(
            cwd,
            config,
            request_text=str(args.text),
            json_output=args.json,
            dry_run=args.dry_run,
            timeout=float(args.timeout_sec),
        )

    return run_command(
        action,
        errors=(
            FileNotFoundError,
            RuntimeError,
            ValueError,
            TimeoutError,
            WorkflowError,
            ProposalError,
            RouterParseError,
        ),
    )


def _run_link(
    cwd: Path,
    config: IssuekitConfig,
    *,
    request_id: int,
    target_project: str,
    proposal_ref: str,
    json_output: bool,
) -> int:
    state = _load_state(cwd)
    record = state.get(str(request_id))
    if not isinstance(record, dict):
        raise ValueError(f"PM request {request_id} was not found.")

    ref = proposal_ref.strip()
    match = _PROPOSAL_REF_PATTERN.match(ref)
    if match is None:
        raise ValueError(f"Invalid proposal ref: {proposal_ref}. Expected project#id.")
    if match.group("project") != target_project:
        raise ValueError(
            f"Proposal ref {ref} targets {match.group('project')}, not {target_project}."
        )
    proposal_id = int(match.group("id"))

    targets = _state_targets(record)
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
            "linked_at": _now(),
            "status": str(proposal.get("status") or "linked"),
        }
    )
    targets[target_index] = updated
    record["targets"] = targets
    record["updated_at"] = _now()
    state[str(request_id)] = record
    _save_state(cwd, state)

    payload = {
        "request_id": request_id,
        "decision": "link",
        "target_project": target_project,
        "proposal_ref": ref,
    }
    print_payload(payload, json_output=json_output)
    return 0


def _run_new_request(
    cwd: Path,
    config: IssuekitConfig,
    *,
    request_text: str,
    json_output: bool,
    dry_run: bool,
    timeout: float,
) -> int:
    _require_router_config(config)
    state = _load_state(cwd)
    request_id, record = _find_or_create_request(state, request_text)
    decision = run_router(
        config,
        cwd,
        request_id=request_id,
        request_text=request_text,
        qa_rounds=_qa_rounds(record),
        force_final=len(_qa_rounds(record)) >= config.router.max_clarify_rounds,
        timeout=timeout,
        runner_factory=AgentRunner,
        err=sys.stderr,
    )
    return _handle_decision(
        cwd,
        config,
        state,
        request_id,
        record,
        decision,
        json_output=json_output,
        dry_run=dry_run,
        force_reject_clarify=len(_qa_rounds(record)) >= config.router.max_clarify_rounds,
    )


def _run_answer(
    cwd: Path,
    config: IssuekitConfig,
    *,
    request_id: int,
    answer_text: str,
    target_project: str | None,
    json_output: bool,
    dry_run: bool,
    timeout: float,
) -> int:
    state = _load_state(cwd)
    record = state.get(str(request_id))
    if not isinstance(record, dict):
        raise ValueError(f"PM request {request_id} was not found.")
    question = str(record.get("pending_question") or "").strip()
    targets = _state_targets(record)
    if question and not targets:
        if target_project:
            raise ValueError(
                f"PM request {request_id} has a pre-routing clarification; do not pass --target."
            )
        return _run_pre_routing_answer(
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
        )
    pending_questions = _matched_inbox_questions(config, state, request_id=request_id)
    if target_project:
        pending_questions = [
            item for item in pending_questions if item["target_project"] == target_project
        ]
        if not pending_questions:
            raise ValueError(
                f"PM request {request_id} has no pending clarification for target {target_project}."
            )
    if len(pending_questions) > 1:
        raise ValueError(_ambiguous_answer_message(request_id, pending_questions))
    if len(pending_questions) == 1:
        return _run_target_reply_answer(
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


def _run_pre_routing_answer(
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
) -> int:
    _require_router_config(config)
    qa = _qa_rounds(record)
    qa.append({"question": question, "answer": answer_text.strip()})
    if not dry_run:
        record["qa"] = qa
        record.pop("pending_question", None)
        record["updated_at"] = _now()
        _save_state(cwd, state)
    decision = run_router(
        config,
        cwd,
        request_id=request_id,
        request_text=str(record.get("original_text") or ""),
        qa_rounds=qa,
        force_final=len(qa) >= config.router.max_clarify_rounds,
        timeout=timeout,
        runner_factory=AgentRunner,
        err=sys.stderr,
    )
    return _handle_decision(
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


def _run_target_reply_answer(
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
        payload = {
            "request_id": request_id,
            "decision": "answer",
            "target_project": pending_question["target_project"],
            "supersedes": pending_question["proposal_ref"],
        }
        print_payload(payload, json_output=json_output)
        return 0

    targets = _state_targets(record)
    target_index = int(pending_question["target_index"])
    target = targets[target_index]
    previous_ref = str(target.get("proposal_ref") or "").strip()
    if previous_ref != pending_question["proposal_ref"]:
        raise ValueError(
            f"Pending clarification targets {pending_question['proposal_ref']}, "
            f"but request {request_id} now records {previous_ref or 'no proposal'}."
        )

    clarifications = _target_clarifications(target)
    clarifications.append(
        {
            "question": str(pending_question.get("question") or "").strip(),
            "answer": answer_text.strip(),
        }
    )
    amended_body = _compose_amended_body(
        str(target.get("body") or "").strip(),
        clarifications,
        supersedes=previous_ref,
    )
    resolved_depends_on = _resolve_depends_on(
        _target_depends_on(target),
        _refs_by_target_index(targets),
    )
    proposal = proposals_api.build_proposal(
        cwd,
        to=str(target["project"]),
        title=str(target.get("title") or ""),
        body=amended_body,
        body_file=None,
        from_issue=None,
        reply=None,
        blocking=bool(target.get("blocking", False)),
        depends_on=resolved_depends_on,
    )
    proposal = replace(
        proposal,
        origin=_amended_origin(
            config,
            cwd,
            request_id=request_id,
            target_project=str(target["project"]),
            previous_ref=previous_ref,
            round_count=len(clarifications),
        ),
    )
    sent = proposals_api.send_proposal(config, proposal)
    if sent.get("payload_mismatch"):
        raise ProposalError(str(sent.get("warning") or "Proposal payload mismatch."))

    proposal_ref = f"{target['project']}#{sent.get('id')}"
    dependency_ref = str(sent.get("dependency_ref") or proposal_ref)
    updated = dict(target)
    updated.update(
        {
            "proposal_ref": proposal_ref,
            "dependency_ref": dependency_ref,
            "proposal_id": sent.get("id"),
            "sent_at": _now(),
            "clarifications": clarifications,
        }
    )
    targets[target_index] = updated
    record["targets"] = targets
    record["updated_at"] = _now()
    state[str(request_id)] = record
    _save_state(cwd, state)

    with proposals_api.api_client(config) as client:
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


def _handle_decision(
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
        record["updated_at"] = _now()
        state[str(request_id)] = record
        _save_state(cwd, state)
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
        record["updated_at"] = _now()
        state[str(request_id)] = record
        _save_state(cwd, state)
        payload = {
            "request_id": request_id,
            "decision": "reject",
            "reason": decision.reason,
        }
        print_payload(payload, json_output=json_output)
        return 0

    sent_targets = _send_route_targets(
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


def _send_route_targets(
    cwd: Path,
    config: IssuekitConfig,
    state: dict[str, dict[str, Any]],
    request_id: int,
    record: dict[str, Any],
    targets: tuple[RouteTarget, ...],
) -> list[dict[str, Any]]:
    record["decision"] = "route"
    record.pop("pending_question", None)
    existing_targets = _state_targets(record)
    while len(existing_targets) < len(targets):
        target = targets[len(existing_targets)]
        existing_targets.append(_target_state(target))
    record["targets"] = existing_targets
    record["updated_at"] = _now()
    state[str(request_id)] = record
    _save_state(cwd, state)

    refs_by_index: dict[int, str] = {}
    output: list[dict[str, Any]] = []
    for index, target in enumerate(targets):
        stored = existing_targets[index]
        stored_ref = str(stored.get("proposal_ref") or "").strip()
        if stored_ref:
            refs_by_index[index] = str(stored.get("dependency_ref") or stored_ref)
            output.append(dict(stored))
            continue
        resolved_depends_on = _resolve_depends_on(target.depends_on, refs_by_index)
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
            _save_state(cwd, state)
            raise ProposalError(str(sent.get("warning") or "Proposal payload mismatch."))
        proposal_ref = f"{target.project}#{sent.get('id')}"
        dependency_ref = str(sent.get("dependency_ref") or proposal_ref)
        refs_by_index[index] = dependency_ref
        updated = _target_state(target)
        updated.update(
            {
                "proposal_ref": proposal_ref,
                "dependency_ref": dependency_ref,
                "proposal_id": sent.get("id"),
                "sent_at": _now(),
            }
        )
        existing_targets[index] = updated
        record["targets"] = existing_targets
        record["updated_at"] = _now()
        _save_state(cwd, state)
        output.append(dict(updated))
    return output


def _run_inbox(
    cwd: Path,
    config: IssuekitConfig,
    *,
    json_output: bool,
) -> int:
    state = _load_state(cwd)
    payload = _inbox_questions(config, state)
    if json_output:
        print(json.dumps(payload, indent=2))
        return 0
    matched = payload["matched"]
    unmatched = payload["unmatched"]
    if not matched and not unmatched:
        print("No pending PM clarification replies.")
        return 0
    for item in matched:
        print(
            f"Request {item['request_id']} target={item['target_project']} "
            f"proposal={item['proposal_ref']} reply=pm#{item['reply_proposal_id']}"
        )
        print(f"  {item['question']}")
    for item in unmatched:
        print(
            f"Unmatched reply proposal pm#{item['reply_proposal_id']} "
            f"for {item['proposal_ref']}: {item['title']}"
        )
    return 0


def _run_status(
    cwd: Path,
    config: IssuekitConfig,
    *,
    request_id_arg: str,
    json_output: bool,
) -> int:
    state = _load_state(cwd)
    if request_id_arg == "all":
        records = [
            (int(key), value)
            for key, value in sorted(state.items(), key=lambda item: int(item[0]))
            if isinstance(value, dict)
        ]
    else:
        request_id = int(request_id_arg)
        record = state.get(str(request_id))
        if not isinstance(record, dict):
            raise ValueError(f"PM request {request_id} was not found.")
        records = [(request_id, record)]
    payload = [
        _status_record(config, request_id=request_id, record=record)
        for request_id, record in records
    ]
    if json_output:
        print(json.dumps(payload, indent=2))
        return 0
    if not payload:
        print("No PM requests recorded.")
        return 0
    for item in payload:
        print_status_record(item)
    return 0


def _inbox_questions(
    config: IssuekitConfig,
    state: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    by_ref = _targets_by_proposal_ref(state)
    with proposals_api.api_client(config) as client:
        proposals = client.list_proposals(status="pending")
    for proposal in proposals:
        title = str(proposal.get("title") or "")
        match = _REPLY_TITLE_PATTERN.match(title)
        if match is None:
            continue
        proposal_ref = f"{match.group('project')}#{match.group('id')}"
        item = {
            "reply_proposal_id": proposal.get("id"),
            "proposal_ref": proposal_ref,
            "target_project": match.group("project"),
            "title": title,
            "original_title": match.group("title").strip(),
            "question": str(proposal.get("body") or "").strip(),
        }
        target_match = by_ref.get(proposal_ref)
        if target_match is None:
            unmatched.append(item)
            continue
        request_id, target_index = target_match
        item.update({"request_id": request_id, "target_index": target_index})
        matched.append(item)
    matched.sort(key=lambda item: (int(item["request_id"]), str(item["target_project"])))
    unmatched.sort(key=lambda item: int(item.get("reply_proposal_id") or 0))
    return {"matched": matched, "unmatched": unmatched}


def _matched_inbox_questions(
    config: IssuekitConfig,
    state: dict[str, dict[str, Any]],
    *,
    request_id: int,
) -> list[dict[str, Any]]:
    return [
        item
        for item in _inbox_questions(config, state)["matched"]
        if int(item["request_id"]) == request_id
    ]


def _targets_by_proposal_ref(
    state: dict[str, dict[str, Any]],
) -> dict[str, tuple[int, int]]:
    by_ref: dict[str, tuple[int, int]] = {}
    for raw_request_id, record in state.items():
        if not isinstance(record, dict):
            continue
        request_id = int(raw_request_id)
        for target_index, target in enumerate(_state_targets(record)):
            ref = str(target.get("proposal_ref") or "").strip()
            if _PROPOSAL_REF_PATTERN.match(ref):
                by_ref[ref] = (request_id, target_index)
    return by_ref


def _ambiguous_answer_message(
    request_id: int,
    pending_questions: list[dict[str, Any]],
) -> str:
    lines = [
        f"PM request {request_id} has multiple pending target clarifications; pass --target."
    ]
    for item in pending_questions:
        lines.append(
            f"- {item['target_project']}: {item['question']} "
            f"({item['proposal_ref']}, reply pm#{item['reply_proposal_id']})"
        )
    return "\n".join(lines)


def _status_record(
    config: IssuekitConfig,
    *,
    request_id: int,
    record: dict[str, Any],
) -> dict[str, Any]:
    targets = _state_targets(record)
    by_project: dict[str, dict[int, dict[str, Any]]] = {}
    for target in targets:
        ref = str(target.get("proposal_ref") or "")
        match = _PROPOSAL_REF_PATTERN.match(ref)
        if match is None:
            continue
        project = match.group("project")
        proposal_id = int(match.group("id"))
        if project not in by_project:
            by_project[project] = {}
            for proposal in proposals_api.list_outgoing_proposals(config, to=project):
                try:
                    outgoing_id = int(proposal.get("id"))
                except (TypeError, ValueError):
                    continue
                by_project[project][outgoing_id] = proposal
        target["status"] = by_project[project].get(proposal_id, {}).get("status", "unknown")
        adopted = by_project[project].get(proposal_id, {}).get("adopted_issue_number")
        if adopted:
            target["adopted_issue_ref"] = f"{project}#{adopted}"
    return {
        "request_id": request_id,
        "original_text": record.get("original_text", ""),
        "decision": record.get("decision", ""),
        "pending_question": record.get("pending_question", ""),
        "reason": record.get("reason", ""),
        "targets": targets,
    }


def _find_or_create_request(
    state: dict[str, dict[str, Any]],
    request_text: str,
) -> tuple[int, dict[str, Any]]:
    for key, record in sorted(state.items(), key=lambda item: int(item[0])):
        if not isinstance(record, dict):
            continue
        if str(record.get("original_text") or "") != request_text:
            continue
        if not _is_complete(record):
            return int(key), record
    request_id = max((int(key) for key in state if str(key).isdigit()), default=0) + 1
    record = {
        "id": request_id,
        "original_text": request_text,
        "qa": [],
        "targets": [],
        "decision": "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    state[str(request_id)] = record
    return request_id, record


def _is_complete(record: dict[str, Any]) -> bool:
    if record.get("decision") == "reject":
        return True
    if record.get("decision") != "route":
        return False
    targets = _state_targets(record)
    return bool(targets) and all(str(target.get("proposal_ref") or "") for target in targets)


def _target_state(target: RouteTarget) -> dict[str, Any]:
    data = target.to_dict()
    data.setdefault("blocking", False)
    data.setdefault("depends_on", [])
    return data


def _resolve_depends_on(depends_on: tuple[str, ...], refs_by_index: dict[int, str]) -> list[str]:
    resolved: list[str] = []
    for ref in depends_on:
        match = _TARGET_PLACEHOLDER_PATTERN.match(ref)
        if match is None:
            resolved.append(ref)
            continue
        index = int(match.group("index"))
        if index not in refs_by_index:
            raise ProposalError(f"{ref} could not be resolved to a sent proposal.")
        resolved.append(refs_by_index[index])
    return resolved


def _refs_by_target_index(targets: list[dict[str, Any]]) -> dict[int, str]:
    refs: dict[int, str] = {}
    for index, target in enumerate(targets):
        ref = str(target.get("dependency_ref") or target.get("proposal_ref") or "").strip()
        if ref:
            refs[index] = ref
    return refs


def _target_clarifications(target: dict[str, Any]) -> list[dict[str, str]]:
    raw = target.get("clarifications") or []
    if not isinstance(raw, list):
        return []
    clarifications: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if question and answer:
            clarifications.append({"question": question, "answer": answer})
    return clarifications


def _target_depends_on(target: dict[str, Any]) -> tuple[str, ...]:
    raw = target.get("depends_on") or []
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list | tuple):
        return tuple(str(item) for item in raw if str(item).strip())
    return ()


def _compose_amended_body(
    original_body: str,
    clarifications: list[dict[str, str]],
    *,
    supersedes: str,
) -> str:
    sections = [original_body.strip()]
    if clarifications:
        lines = ["## Clarifications"]
        for index, item in enumerate(clarifications, start=1):
            lines.extend(
                [
                    "",
                    f"### Round {index}",
                    "",
                    "Question:",
                    "",
                    item["question"],
                    "",
                    "Answer:",
                    "",
                    item["answer"],
                ]
            )
        sections.append("\n".join(lines).strip())
    sections.append(f"Supersedes: {supersedes}")
    return "\n\n".join(section for section in sections if section).strip()


def _amended_origin(
    config: IssuekitConfig,
    cwd: Path,
    *,
    request_id: int,
    target_project: str,
    previous_ref: str,
    round_count: int,
) -> str:
    previous_id = previous_ref.split("#", 1)[1]
    commit = git_short_head(cwd) or "unknown"
    return (
        f"{config.project}#request-{request_id}-{target_project}-"
        f"{previous_id}-round-{round_count}@{commit}"
    )


def _qa_rounds(record: dict[str, Any]) -> list[dict[str, str]]:
    raw = record.get("qa") or []
    if not isinstance(raw, list):
        return []
    rounds: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if question and answer:
            rounds.append({"question": question, "answer": answer})
    return rounds


def _state_targets(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("targets") or []
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _require_router_config(config: IssuekitConfig) -> None:
    if not config.router.agent:
        raise WorkflowError("issuekit request requires [tool.issuekit.router] agent.")
