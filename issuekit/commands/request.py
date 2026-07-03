"""PM request router command."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

from issuekit.agents.runner import AgentRunner
from issuekit.agents.router import (
    RouterDecision,
    RouterParseError,
    RouteTarget,
    run_router,
)
from issuekit.commands._common import run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit import proposals_api
from issuekit.proposals import ProposalError
from issuekit.workflow import WorkflowError


STATE_FILENAME = "pm-requests.json"
_PROPOSAL_REF_PATTERN = re.compile(
    r"^(?P<project>[A-Za-z0-9_.-]+)#(?P<id>[1-9][0-9]*)$"
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
        if args.status is not None:
            if args.answer is not None or args.text is not None or args.dry_run:
                raise ValueError("--status cannot be combined with request text, --answer, or --dry-run.")
            return _run_status(cwd, config, request_id_arg=args.status, json_output=args.json)
        if args.answer is not None:
            if not args.text:
                raise ValueError("request --answer requires answer text.")
            return _run_answer(
                cwd,
                config,
                request_id=int(args.answer),
                answer_text=str(args.text),
                json_output=args.json,
                dry_run=args.dry_run,
                timeout=float(args.timeout_sec),
            )
        if not args.text:
            raise ValueError("issuekit request requires request text, --answer, or --status.")
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
    json_output: bool,
    dry_run: bool,
    timeout: float,
) -> int:
    _require_router_config(config)
    state = _load_state(cwd)
    record = state.get(str(request_id))
    if not isinstance(record, dict):
        raise ValueError(f"PM request {request_id} was not found.")
    question = str(record.get("pending_question") or "").strip()
    if not question:
        raise ValueError(f"PM request {request_id} has no pending clarification.")
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
        _print_payload(payload, json_output=json_output)
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
        _print_payload(payload, json_output=json_output)
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
        _print_payload(payload, json_output=json_output)
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
    _print_payload(payload, json_output=json_output)
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
            refs_by_index[index] = stored_ref
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
        refs_by_index[index] = proposal_ref
        updated = _target_state(target)
        updated.update(
            {
                "proposal_ref": proposal_ref,
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
        _print_status_record(item)
    return 0


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


def _print_payload(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2))
        return
    request_id = payload["request_id"]
    decision = payload["decision"]
    if decision == "clarify":
        print(f"Request {request_id} needs clarification: {payload['question']}")
        print(f"Answer with: issuekit request --answer {request_id} \"<answer>\"")
    elif decision == "reject":
        print(f"Request {request_id} rejected: {payload['reason']}")
    else:
        print(f"Request {request_id} routed.")
        for index, target in enumerate(payload.get("targets", [])):
            print(
                f"target[{index}] {target.get('project')} "
                f"proposal={target.get('proposal_ref')} title={target.get('title')}"
            )


def _print_status_record(item: dict[str, Any]) -> None:
    print(f"Request {item['request_id']}: {item.get('decision') or 'pending'}")
    if item.get("pending_question"):
        print(f"  clarification: {item['pending_question']}")
    if item.get("reason"):
        print(f"  reason: {item['reason']}")
    for target in item.get("targets", []):
        adopted = target.get("adopted_issue_ref") or "-"
        print(
            f"  {target.get('proposal_ref', '-')}\t"
            f"{target.get('status', 'unsent')}\t"
            f"{adopted}\t{target.get('title', '')}"
        )


def _require_router_config(config: IssuekitConfig) -> None:
    if not config.router.agent:
        raise WorkflowError("issuekit request requires [tool.issuekit.router] agent.")


def _state_path(cwd: Path) -> Path:
    return cwd / ".agent-runs" / STATE_FILENAME


def _load_state(cwd: Path) -> dict[str, dict[str, Any]]:
    path = _state_path(cwd)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    state: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if not str(key).isdigit() or not isinstance(value, dict):
            continue
        state[str(int(key))] = dict(value)
    return state


def _save_state(cwd: Path, state: dict[str, dict[str, Any]]) -> None:
    path = _state_path(cwd)
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
