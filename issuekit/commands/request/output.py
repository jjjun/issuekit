"""Output helpers for the PM request command."""

from __future__ import annotations

from typing import Any

from issuekit.commands._common import print_json


def print_payload(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print_json(payload)
        return
    request_id = payload["request_id"]
    decision = payload["decision"]
    if decision == "clarify":
        print(f"Request {request_id} needs clarification: {payload['question']}")
        print(f"Answer with: issuekit request --answer {request_id} \"<answer>\"")
    elif decision == "reject":
        print(f"Request {request_id} rejected: {payload['reason']}")
    elif decision == "answer":
        print(
            f"Request {request_id} answered target {payload['target_project']}: "
            f"{payload.get('proposal_ref', '(dry-run)')} supersedes={payload['supersedes']}"
        )
    elif decision == "link":
        print(
            f"Request {request_id} linked target {payload['target_project']}: "
            f"{payload['proposal_ref']}"
        )
    else:
        print(f"Request {request_id} routed.")
        for index, target in enumerate(payload.get("targets", [])):
            print(
                f"target[{index}] {target.get('project')} "
                f"proposal={target.get('proposal_ref')} title={target.get('title')}"
            )


def print_status_record(item: dict[str, Any]) -> None:
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
