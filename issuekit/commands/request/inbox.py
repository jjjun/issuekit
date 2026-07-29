"""Inbox and status flows for the PM request command."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import issuekit.proposals.api as proposals_api
from issuekit.commands._common import print_json
from issuekit.commands.request.output import print_status_record
from issuekit.commands.request.state import (
    PROPOSAL_REF_PATTERN,
    load_state,
    state_targets,
    status_record,
)
from issuekit.config import IssuekitConfig

REPLY_TITLE_PATTERN = re.compile(
    r"^Re:\s*(?P<project>[A-Za-z0-9_.-]+)#(?P<id>[1-9][0-9]*):\s*(?P<title>.*)$"
)


def run_inbox(
    cwd: Path,
    config: IssuekitConfig,
    *,
    json_output: bool,
) -> int:
    state = load_state(cwd)
    payload = inbox_questions(config, state)
    if json_output:
        print_json(payload)
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


def run_status(
    cwd: Path,
    config: IssuekitConfig,
    *,
    request_id_arg: str,
    json_output: bool,
) -> int:
    state = load_state(cwd)
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
        status_record(config, request_id=request_id, record=record)
        for request_id, record in records
    ]
    if json_output:
        print_json(payload)
        return 0
    if not payload:
        print("No PM requests recorded.")
        return 0
    for item in payload:
        print_status_record(item)
    return 0


def inbox_questions(
    config: IssuekitConfig,
    state: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    by_ref = targets_by_proposal_ref(state)
    with proposals_api.api_client(config) as client:
        proposals = client.list_proposals(status="pending")
    for proposal in proposals:
        title = str(proposal.get("title") or "")
        match = REPLY_TITLE_PATTERN.match(title)
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


def matched_inbox_questions(
    config: IssuekitConfig,
    state: dict[str, dict[str, Any]],
    *,
    request_id: int,
) -> list[dict[str, Any]]:
    return [
        item
        for item in inbox_questions(config, state)["matched"]
        if int(item["request_id"]) == request_id
    ]


def targets_by_proposal_ref(
    state: dict[str, dict[str, Any]],
) -> dict[str, tuple[int, int]]:
    by_ref: dict[str, tuple[int, int]] = {}
    for raw_request_id, record in state.items():
        if not isinstance(record, dict):
            continue
        request_id = int(raw_request_id)
        for target_index, target in enumerate(state_targets(record)):
            ref = str(target.get("proposal_ref") or "").strip()
            if PROPOSAL_REF_PATTERN.match(ref):
                by_ref[ref] = (request_id, target_index)
    return by_ref


def ambiguous_answer_message(
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
