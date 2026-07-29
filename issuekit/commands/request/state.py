"""State persistence helpers for the PM request command."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import issuekit.proposals.api as proposals_api
from issuekit.agents.router import RouteTarget
from issuekit.config import IssuekitConfig
from issuekit.gitutil import git_short_head
from issuekit.proposals import ProposalError

STATE_FILENAME = "pm-requests.json"
PROPOSAL_REF_PATTERN = re.compile(
    r"^(?P<project>[A-Za-z0-9_.-]+)#(?P<id>[1-9][0-9]*)$"
)
TARGET_PLACEHOLDER_PATTERN = re.compile(r"^target:(?P<index>[0-9]+)$")


def state_path(cwd: Path) -> Path:
    return cwd / ".agent-runs" / STATE_FILENAME


def load_state(cwd: Path) -> dict[str, dict[str, Any]]:
    path = state_path(cwd)
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


def save_state(cwd: Path, state: dict[str, dict[str, Any]]) -> None:
    path = state_path(cwd)
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def status_record(
    config: IssuekitConfig,
    *,
    request_id: int,
    record: dict[str, Any],
) -> dict[str, Any]:
    targets = state_targets(record)
    by_project: dict[str, dict[int, dict[str, Any]]] = {}
    for target in targets:
        ref = str(target.get("proposal_ref") or "")
        match = PROPOSAL_REF_PATTERN.match(ref)
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


def find_or_create_request(
    state: dict[str, dict[str, Any]],
    request_text: str,
) -> tuple[int, dict[str, Any]]:
    for key, record in sorted(state.items(), key=lambda item: int(item[0])):
        if not isinstance(record, dict):
            continue
        if str(record.get("original_text") or "") != request_text:
            continue
        if not is_complete(record):
            return int(key), record
    request_id = max((int(key) for key in state if str(key).isdigit()), default=0) + 1
    record = {
        "id": request_id,
        "original_text": request_text,
        "qa": [],
        "targets": [],
        "decision": "",
        "created_at": now(),
        "updated_at": now(),
    }
    state[str(request_id)] = record
    return request_id, record


def is_complete(record: dict[str, Any]) -> bool:
    if record.get("decision") == "reject":
        return True
    if record.get("decision") != "route":
        return False
    targets = state_targets(record)
    return bool(targets) and all(str(target.get("proposal_ref") or "") for target in targets)


def target_state(target: RouteTarget) -> dict[str, Any]:
    data = target.to_dict()
    data.setdefault("blocking", False)
    data.setdefault("depends_on", [])
    return data


def resolve_depends_on(depends_on: tuple[str, ...], refs_by_index: dict[int, str]) -> list[str]:
    resolved: list[str] = []
    for ref in depends_on:
        match = TARGET_PLACEHOLDER_PATTERN.match(ref)
        if match is None:
            resolved.append(ref)
            continue
        index = int(match.group("index"))
        if index not in refs_by_index:
            raise ProposalError(f"{ref} could not be resolved to a sent proposal.")
        resolved.append(refs_by_index[index])
    return resolved


def refs_by_target_index(targets: list[dict[str, Any]]) -> dict[int, str]:
    refs: dict[int, str] = {}
    for index, target in enumerate(targets):
        ref = str(target.get("dependency_ref") or target.get("proposal_ref") or "").strip()
        if ref:
            refs[index] = ref
    return refs


def target_clarifications(target: dict[str, Any]) -> list[dict[str, str]]:
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


def target_depends_on(target: dict[str, Any]) -> tuple[str, ...]:
    raw = target.get("depends_on") or []
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list | tuple):
        return tuple(str(item) for item in raw if str(item).strip())
    return ()


def compose_amended_body(
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


def amended_origin(
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


def qa_rounds(record: dict[str, Any]) -> list[dict[str, str]]:
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


def state_targets(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = record.get("targets") or []
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]
