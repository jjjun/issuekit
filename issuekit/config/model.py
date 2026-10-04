"""Configuration data models and built-in defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from issuekit.agentrun.config import AgentRunConfig
from issuekit.core import (
    WORKFLOW_TOKEN_MAX_LEN,
    qualified_worker_key,
    worker_key,
)
from issuekit.urls import api_url_origin as api_url_origin
from issuekit.worker_constants import WORKER_HEARTBEAT_INTERVAL_SEC

_IMPLEMENTER_PROMPT_SUFFIX = (
    "Make minimal, additive diffs. Do not reformat, re-quote, "
    "re-order imports, or rewrite/translate comments on lines "
    "unrelated to your change.\n"
    "Never alter existing non-ASCII (e.g. Japanese) text that the "
    "task does not ask you to change. When the task asks you to "
    "correct such text, rewrite it in place in the same language "
    "unless the task asks for a translation, keep the file encoding "
    "(UTF-8, no BOM, LF), and do not keep the replaced text in a "
    "comment. Preserve existing comments byte-for-byte unless the "
    "task is specifically to change them. After editing, verify "
    "you introduced no mojibake.\n"
    "When a task says 'add X alongside Y, do not change Y,' the diff "
    "must touch only the added region; if you cannot, stop and report "
    "instead of reformatting."
)

# Repo-level worker metadata length limits agreed with the mine-py backend
# (negotiation thread 18): role stays short, description allows a sentence or two.
WORKER_ROLE_MAX_LEN = 80

WORKER_DESCRIPTION_MAX_LEN = 500

REPO_DESCRIPTION_MAX_LEN = 500

# Project-level capability profile limits (mine-py#172). The long-form profile
# lives in a committed markdown file; summary/tags are short optional metadata.
DEFAULT_PROFILE_FILE = "ISSUEKIT.md"

PROFILE_SUMMARY_MAX_LEN = 500

PROFILE_TAG_MAX_LEN = WORKFLOW_TOKEN_MAX_LEN

PROFILE_TAGS_MAX = 20

AGENT_ROLES = frozenset({"author", "implementer", "pm", "reviewer", "triage"})

ROLE_OVERLAY_ROLES = frozenset({"implementer", "reviewer", "router", "triage", "negotiation"})

READ_ONLY_ROLES = frozenset({"reviewer", "router", "triage", "negotiation"})

_CLAUDE_READ_ONLY_APPROVAL_ARGV = (
    "--permission-mode",
    "dontAsk",
    "--allowedTools",
    "Read,Grep,Glob,Bash(git status:*),Bash(git diff:*),Bash(git log:*),"
    "Bash(git show:*),Bash(git ls-files:*)",
    "--strict-mcp-config",
)

BUILTIN_ROLE_LAUNCH_POLICIES = {
    "codex": {
        "triage": ("--sandbox", "read-only", "-c", "mcp_servers={}"),
        "router": ("--sandbox", "read-only", "-c", "mcp_servers={}"),
        "negotiation": ("--sandbox", "read-only", "-c", "mcp_servers={}"),
        "reviewer": ("--sandbox", "workspace-write", "-c", "mcp_servers={}"),
    },
    "claude": dict.fromkeys(READ_ONLY_ROLES, _CLAUDE_READ_ONLY_APPROVAL_ARGV),
}


@dataclass(frozen=True)
class AgentPolicy:
    """Issuekit submission policy applied to an agent's implementation."""

    mojibake_gate: bool = False
    diff_shape_warn_deletions: int | None = None


@dataclass(frozen=True)
class RoleOverlay:
    """Model and launch settings that apply when an agent runs in one role."""

    model: str | None = None
    reasoning_effort: str | None = None
    approval_argv: tuple[str, ...] | None = None


@dataclass(frozen=True)
class WorkerIdentity:
    machine_id: str
    repo_id: str
    worker_id: str

    @property
    def worker_name(self) -> str:
        return self.worker_id


@dataclass(frozen=True)
class TriagePolicy:
    """Target-owned policy for automatic inbox proposal adoption."""

    auto_adopt: bool = False
    hold_auto_adopted: bool = True
    trusted_origins: tuple[str, ...] = ()
    default_priority: str = "medium"
    require_blocking: bool = False
    max_adoptions_per_cycle: int = 5
    author_agent: str = ""


@dataclass(frozen=True)
class RouterPolicy:
    """PM router policy for request-to-proposal routing."""

    agent: str = ""
    max_targets: int = 3
    max_clarify_rounds: int = 2


@dataclass(frozen=True)
class IssuekitConfig:
    api_url: str = ""
    trusted_api_origins: tuple[str, ...] = ()
    allow_insecure_api_url: bool = False
    project: str = "issuekit"
    api_timeout: float = 30.0
    assignees: tuple[str, ...] = ("codex", "claude", "kimi")
    stages: tuple[str, ...] = (
        "planned",
        "todo",
        "implementing",
        "review",
        "changes_requested",
        "done",
    )
    default_implementer: str = ""
    work_branch: str = ""
    gate_halfwidth_kana: bool = True
    check_encoding_exclude: tuple[str, ...] = ()
    claim_sync: bool = True
    claim_sync_interval_sec: float = 60.0
    worker_heartbeat_interval_sec: float = WORKER_HEARTBEAT_INTERVAL_SEC
    worker: WorkerIdentity | None = None
    worker_role: str = ""
    worker_description: str = ""
    worker_accept_directed: bool = False
    repo_description: str = ""
    repo_metadata: dict[str, str] = field(default_factory=dict)
    worker_metadata: dict[str, str] = field(default_factory=dict)
    profile_file: str = DEFAULT_PROFILE_FILE
    profile_summary: str = ""
    profile_tags: tuple[str, ...] = ()
    triage: TriagePolicy = field(default_factory=TriagePolicy)
    router: RouterPolicy = field(default_factory=RouterPolicy)
    agent_roles: dict[str, str] = field(default_factory=dict)
    disabled_agents: tuple[str, ...] = ()
    agent_role_overlays: tuple[tuple[str, tuple[tuple[str, RoleOverlay], ...]], ...] = ()
    machine_config_path: Path | None = None
    repo_config_source: str = field(default="none", compare=False)
    api_url_source: str = field(default="none", compare=False)
    api_url_trusted_by: str = field(default="none", compare=False)
    agents: tuple[tuple[str, AgentRunConfig], ...] = (
        (
            "kimi",
            AgentRunConfig(
                binary="kimi",
                adapter="kimi",
                known_paths=("~/.kimi-code/bin/kimi", "~/.kimi-code/bin/kimi.exe"),
                headless_argv=("-p",),
                output_format_flag="--output-format",
                output_format="text",
                model_flag="-m",
            ),
        ),
        (
            "codex",
            AgentRunConfig(
                binary="codex",
                adapter="codex",
                known_paths=(
                    "~/.codex/.sandbox-bin/codex",
                    "~/.codex/.sandbox-bin/codex.exe",
                ),
                headless_argv=("exec",),
                approval_flag="--dangerously-bypass-approvals-and-sandbox",
                model_flag="--model",
                effort_argv=("-c", "model_reasoning_effort={value}"),
                speed_argv=("-c", "service_tier=fast"),
                prompt_suffix=_IMPLEMENTER_PROMPT_SUFFIX,
            ),
        ),
        (
            "claude",
            AgentRunConfig(
                binary="claude",
                known_paths=(
                    "~/.claude/local/claude",
                    "~/.claude/local/claude.exe",
                    "~/.local/bin/claude",
                    "~/.local/bin/claude.exe",
                ),
                headless_argv=("-p",),
                resumable=True,
                session_flag="--session-id",
                resume_flag="--resume",
                approval_flag="--permission-mode",
                approval_value="bypassPermissions",
                output_format_flag="--output-format",
                output_format="json",
                model_flag="--model",
                effort_argv=("--effort", "{value}"),
                speed_argv=("--settings", '{"fastMode": true}'),
                prompt_suffix=_IMPLEMENTER_PROMPT_SUFFIX,
            ),
        ),
    )
    agent_policies: tuple[tuple[str, AgentPolicy], ...] = (
        ("codex", AgentPolicy(mojibake_gate=True, diff_shape_warn_deletions=40)),
        ("claude", AgentPolicy(mojibake_gate=True, diff_shape_warn_deletions=40)),
    )

    def worker_key(self) -> str | None:
        if self.worker is None:
            return None
        return worker_key(self.worker.repo_id, self.worker.worker_name)

    def qualified_worker_key(self) -> str | None:
        if self.worker is None:
            return None
        return qualified_worker_key(
            self.worker.machine_id,
            self.worker.repo_id,
            self.worker.worker_name,
        )

    def worker_lookup_keys(self) -> tuple[str, ...]:
        qualified = self.qualified_worker_key()
        current = self.worker_key()
        return tuple(key for key in (qualified, current) if key)
