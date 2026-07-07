"""PM router agent for request-to-proposal routing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Any, TextIO

from issuekit.agents.runner import AgentResult, AgentRunner, resolve_adapter
from issuekit.config import IssuekitConfig
from issuekit.core import has_non_ascii
from issuekit.dependencies import DEPENDENCY_REF_EXPECTED, DEPENDENCY_REF_PATTERN
from issuekit.gitutil import git_status_short
from issuekit.prompts import ROUTER_PROMPT, RouterParseError
from issuekit.proposals_api import api_client
from issuekit.workflow import WorkflowError


ROUTE_BLOCK_LANGUAGE = ROUTER_PROMPT.block_language
_DECISIONS = {"route", "clarify", "reject"}
_TARGET_PLACEHOLDER_PATTERN = re.compile(r"^target:(?P<index>[0-9]+)$")


@dataclass(frozen=True)
class ProjectProfile:
    project: str
    summary: str
    tags: tuple[str, ...]
    profile_md: str

    def to_prompt(self) -> str:
        tags = ", ".join(self.tags) if self.tags else "(none)"
        return "\n".join(
            [
                f"## Project: {self.project}",
                f"Summary: {self.summary or '(none)'}",
                f"Tags: {tags}",
                "",
                self.profile_md or "(no profile markdown)",
                "",
            ]
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "project": self.project,
            "summary": self.summary,
            "tags": list(self.tags),
            "profile_md": self.profile_md,
        }


@dataclass(frozen=True)
class RouteTarget:
    project: str
    title: str
    body: str
    blocking: bool = False
    depends_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "project": self.project,
            "title": self.title,
            "body": self.body,
        }
        if self.blocking:
            data["blocking"] = True
        if self.depends_on:
            data["depends_on"] = list(self.depends_on)
        return data


@dataclass(frozen=True)
class RouterDecision:
    decision: str
    targets: tuple[RouteTarget, ...] = ()
    question: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        if self.decision == "route":
            return {
                "decision": "route",
                "targets": [target.to_dict() for target in self.targets],
            }
        if self.decision == "clarify":
            return {"decision": "clarify", "question": self.question}
        return {"decision": "reject", "reason": self.reason}


def list_candidate_profiles(config: IssuekitConfig) -> tuple[ProjectProfile, ...]:
    """Fetch non-stale project profiles excluding the current PM project."""

    with api_client(config) as client:
        raw_profiles = client.list_project_profiles()
    profiles: list[ProjectProfile] = []
    for raw in raw_profiles:
        project = str(raw.get("project") or "").strip()
        if not project or project == config.project or bool(raw.get("stale", False)):
            continue
        tags = raw.get("tags") or ()
        if isinstance(tags, str):
            tag_tuple = tuple(item.strip() for item in tags.split() if item.strip())
        elif isinstance(tags, Sequence):
            tag_tuple = tuple(str(item).strip() for item in tags if str(item).strip())
        else:
            tag_tuple = ()
        profiles.append(
            ProjectProfile(
                project=project,
                summary=str(raw.get("summary") or "").strip(),
                tags=tag_tuple,
                profile_md=str(raw.get("profile_md") or "").strip(),
            )
        )
    return tuple(sorted(profiles, key=lambda profile: profile.project))


def parse_router_output(
    stdout: str,
    *,
    candidates: Sequence[ProjectProfile],
    max_targets: int,
) -> RouterDecision:
    """Parse the newest well-formed ```route``` block from agent stdout."""

    candidate_projects = {profile.project for profile in candidates}
    return _decision_from_json(
        ROUTER_PROMPT.parse_json(stdout),
        candidate_projects=candidate_projects,
        max_targets=max_targets,
    )


def run_router(
    config: IssuekitConfig,
    cwd: Path,
    *,
    request_id: int,
    request_text: str,
    qa_rounds: Sequence[Mapping[str, str]] = (),
    force_final: bool = False,
    timeout: float = 600.0,
    runner_factory=None,
    err: TextIO | None = None,
) -> RouterDecision:
    """Run the configured router agent and parse a routing decision."""

    agent = config.router.agent
    if not agent:
        raise WorkflowError("issuekit request requires [tool.issuekit.router] agent.")
    err = err or sys.stderr
    runner_factory = runner_factory or AgentRunner
    adapter = resolve_adapter(agent, config=config)
    candidates = list_candidate_profiles(config)

    run_dir = cwd / ".agent-runs"
    run_dir.mkdir(exist_ok=True)
    prompt_path = run_dir / f"pm-request-{request_id}.md"
    prompt_path.write_text(
        _render_router_prompt(
            request_text,
            qa_rounds=qa_rounds,
            candidates=candidates,
            max_targets=config.router.max_targets,
            force_final=force_final,
        ),
        encoding="utf-8",
        newline="\n",
    )
    fingerprint_before = _worktree_fingerprint(cwd)
    result = runner_factory().run(
        adapter,
        prompt_path,
        cwd,
        timeout=float(timeout),
        agent_name=agent,
        prompt_override=_prompt_pointer(prompt_path),
    )
    if result.timed_out:
        raise TimeoutError(f"Router agent timed out for request #{request_id}.")
    if result.exit_code != 0:
        raise RuntimeError(
            f"Router agent exited {result.exit_code} for request #{request_id}."
        )
    fingerprint_after = _worktree_fingerprint(cwd)
    if fingerprint_before != fingerprint_after:
        print(
            "ERROR: router run modified the worktree; ignoring its output.",
            file=err,
        )
        raise WorkflowError(f"Router agent modified the worktree for request #{request_id}.")
    return parse_router_output(
        _stdout_text(result),
        candidates=candidates,
        max_targets=config.router.max_targets,
    )


def _decision_from_json(
    raw: dict[str, object],
    *,
    candidate_projects: set[str],
    max_targets: int,
) -> RouterDecision:
    decision = raw.get("decision")
    if not isinstance(decision, str) or decision not in _DECISIONS:
        raise RouterParseError(f"Invalid route decision: {decision!r}")
    if decision == "clarify":
        question = _required_text(raw, "question", decision)
        return RouterDecision(decision="clarify", question=question)
    if decision == "reject":
        reason = _required_text(raw, "reason", decision)
        return RouterDecision(decision="reject", reason=reason)

    raw_targets = raw.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise RouterParseError("Route decision requires a non-empty targets list.")
    if len(raw_targets) > max_targets:
        raise RouterParseError(
            f"Route decision has {len(raw_targets)} targets; max_targets is {max_targets}."
        )
    targets: list[RouteTarget] = []
    for index, raw_target in enumerate(raw_targets):
        if not isinstance(raw_target, dict):
            raise RouterParseError("Each route target must be an object.")
        project = _required_text(raw_target, "project", "route target")
        if project not in candidate_projects:
            raise RouterParseError(f"Route target project has no candidate profile: {project}")
        title = _required_text(raw_target, "title", "route target")
        body = _required_text(raw_target, "body", "route target")
        blocking = bool(raw_target.get("blocking", False))
        depends_on = _depends_on_tuple(raw_target.get("depends_on"), target_index=index)
        targets.append(
            RouteTarget(
                project=project,
                title=title,
                body=body,
                blocking=blocking,
                depends_on=depends_on,
            )
        )
    return RouterDecision(decision="route", targets=tuple(targets))


def _required_text(raw: Mapping[str, object], field: str, decision: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RouterParseError(
            f"Route decision '{decision}' requires a non-empty '{field}'."
        )
    text = value.strip()
    if has_non_ascii(text):
        raise RouterParseError("Route fields must be ASCII-only.")
    return text


def _depends_on_tuple(value: object, *, target_index: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise RouterParseError("depends_on must be a string or list of strings.")

    refs: list[str] = []
    for raw_item in items:
        if not isinstance(raw_item, str) or not raw_item.strip():
            raise RouterParseError("depends_on entries must be non-empty strings.")
        item = raw_item.strip()
        placeholder = _TARGET_PLACEHOLDER_PATTERN.match(item)
        if placeholder is not None:
            referenced = int(placeholder.group("index"))
            if referenced >= target_index:
                raise RouterParseError(
                    f"{item} must reference an earlier route target."
                )
            refs.append(item)
            continue
        if not DEPENDENCY_REF_PATTERN.match(item):
            raise RouterParseError(
                f"Invalid dependency reference: {item}. "
                f"Expected {DEPENDENCY_REF_EXPECTED} or target:<earlier-index>."
            )
        refs.append(item)
    return tuple(_dedupe(refs))


def _dedupe(refs: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        deduped.append(ref)
    return tuple(deduped)


def _render_router_prompt(
    request_text: str,
    *,
    qa_rounds: Sequence[Mapping[str, str]],
    candidates: Sequence[ProjectProfile],
    max_targets: int,
    force_final: bool,
) -> str:
    profile_text = "\n".join(profile.to_prompt() for profile in candidates).strip()
    if not profile_text:
        profile_text = "(no candidate profiles)"
    qa_text = _render_qa(qa_rounds)
    final_instruction = (
        "The clarification limit has been reached. You must route or reject; "
        "do not return a clarify decision."
        if force_final
        else "If the request cannot be routed safely, ask one concrete clarification question."
    )
    return ROUTER_PROMPT.render(
        max_targets=max_targets,
        final_instruction=final_instruction,
        request_text=request_text.strip(),
        qa_text=qa_text,
        profile_text=profile_text,
    )


def _render_qa(qa_rounds: Sequence[Mapping[str, str]]) -> str:
    if not qa_rounds:
        return "(none)"
    lines: list[str] = []
    for index, item in enumerate(qa_rounds, start=1):
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        lines.append(f"{index}. Q: {question}")
        lines.append(f"   A: {answer}")
    return "\n".join(lines)


def _prompt_pointer(prompt_path: Path) -> str:
    return ROUTER_PROMPT.render_pointer(prompt_path=prompt_path)


def _worktree_fingerprint(cwd: Path) -> tuple[tuple[str, str], ...] | None:
    status = git_status_short(cwd, strip=False, untracked_files="all")
    if status is None:
        return None
    entries: list[tuple[str, str]] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[1]
        raw_path = raw_path.strip('"')
        path = Path(raw_path)
        if path.parts and path.parts[0] == ".agent-runs":
            continue
        entries.append((line[:2], path.as_posix()))
    return tuple(sorted(entries))


def _stdout_text(result: AgentResult) -> str:
    if result.parsed and "stdout" in result.parsed:
        return result.parsed["stdout"]
    return result.stdout_path.read_text(encoding="utf-8", errors="replace")
