"""Serve CLI registration, validation, and orchestration."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from issuekit.agents.run_claimed import preflight_agent
from issuekit.commands._common import (
    add_agent_option,
    add_guard_override_flags,
    add_model_options,
    add_timeout_option,
)
from issuekit.commands._heartbeat import warn_if_staleness_not_wider
from issuekit.config import IssuekitConfig, load_config
from issuekit.errors import WorkflowError
from issuekit.issues.orphans import DEFAULT_STALE_AFTER_SEC
from issuekit.workflow import require_implementer

from . import implement, loop, proposal_checks, review, runtime


def register(subparsers: argparse._SubParsersAction) -> None:
    serve_parser = subparsers.add_parser(
        "serve",
        help="Poll for eligible issues and run this checkout's worker agent.",
    )
    add_agent_option(serve_parser, help="Configured agent name to run.")
    add_model_options(
        serve_parser,
        model_help=(
            "Optional model name applied to every agent launched by this serve loop; "
            "use per-agent config for mixed-agent model selection."
        ),
        effort_help=(
            "Optional reasoning effort applied to every agent launched by this serve "
            "loop; use per-agent config for mixed-agent effort selection."
        ),
    )
    serve_parser.add_argument(
        "--interval",
        type=float,
        default=15.0,
        help="Idle poll interval in seconds.",
    )
    serve_parser.add_argument(
        "--heartbeat-interval",
        type=float,
        help=(
            "Worker heartbeat interval in seconds; overrides "
            "worker_heartbeat_interval_sec."
        ),
    )
    serve_parser.add_argument(
        "--max-heartbeat-failures",
        type=int,
        default=0,
        help="Stop gracefully after this many consecutive heartbeat failures (default: 0, unlimited).",
    )
    serve_parser.add_argument(
        "--max-run-failures",
        type=int,
        default=3,
        help="Stop after this many consecutive failed runs (default: 3, 0: unlimited).",
    )
    serve_parser.add_argument(
        "--priority",
        choices=("high", "medium", "low"),
        help="Priority filter for claim-next.",
    )
    serve_parser.add_argument(
        "--once",
        action="store_true",
        help="Attempt at most one claim and then exit.",
    )
    serve_parser.add_argument(
        "--triage",
        action="store_true",
        help="Auto-adopt matching incoming proposals before each claim attempt.",
    )
    serve_mode = serve_parser.add_mutually_exclusive_group()
    serve_mode.add_argument(
        "--review",
        action="store_true",
        help="Poll the review pool and run this checkout's reviewer agent.",
    )
    serve_mode.add_argument(
        "--proposal-checks",
        action="store_true",
        help="Poll pending proposal checks addressed to this worker.",
    )
    serve_parser.add_argument(
        "--proposal-check-limit",
        type=int,
        default=50,
        help="Maximum proposal checks to evaluate per polling cycle.",
    )
    serve_parser.add_argument(
        "--max-issues",
        type=int,
        help="Exit after this many successful submissions.",
    )
    add_timeout_option(
        serve_parser,
        default=1800.0,
        help="Hard timeout for each agent run in seconds.",
    )
    add_guard_override_flags(
        serve_parser,
        author_session=False,
    )
    serve_parser.set_defaults(func=run)

class ServeMode(StrEnum):
    IMPLEMENT = "implement"
    REVIEW = "review"
    PROPOSAL_CHECKS = "proposal_checks"


_MODE_OPTIONS = {
    ServeMode.REVIEW: "--review",
    ServeMode.PROPOSAL_CHECKS: "--proposal-checks",
}


_MODE_REJECTED_OPTIONS = {
    ServeMode.IMPLEMENT: {},
    ServeMode.REVIEW: {
        "triage": "--triage",
        "priority": "--priority",
    },
    ServeMode.PROPOSAL_CHECKS: {
        "triage": "--triage",
        "priority": "--priority",
    },
}


def _resolve_mode(args) -> ServeMode:
    if args.review:
        return ServeMode.REVIEW
    if args.proposal_checks:
        return ServeMode.PROPOSAL_CHECKS
    return ServeMode.IMPLEMENT


def _mode_option_conflict(args, mode: ServeMode) -> str | None:
    mode_option = _MODE_OPTIONS.get(mode)
    for attribute, option in _MODE_REJECTED_OPTIONS[mode].items():
        if getattr(args, attribute):
            return f"{mode_option} cannot be combined with {option}."
    return None


@dataclass(frozen=True)
class ServeOptions:
    mode: ServeMode
    agent: str
    interval: float
    heartbeat_interval: float
    max_heartbeat_failures: int
    max_run_failures: int
    max_issues: int | None
    proposal_check_limit: int
    timeout_sec: float
    once: bool
    triage: bool
    priority: Literal["high", "medium", "low"] | None
    allow_any_branch: bool
    no_sync: bool
    model: str | None
    reasoning_effort: str | None


def run(args) -> int:
    cwd = Path.cwd()
    try:
        config = load_config(cwd)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        options = _validate(args, config)
    except (ValueError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        adapter = _preflight(options, config, cwd)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Agent preflight failed: {exc}", file=sys.stderr)
        return 1
    return _run_with_runtime(options, config, cwd, adapter)


def _validate(args, config: IssuekitConfig) -> ServeOptions:
    agent = require_implementer(args.agent, config, flag="--agent")
    if config.worker is None:
        raise ValueError(
            "This checkout is not registered as an issuekit worker. "
            "Run `issuekit add` first."
        )
    if not config.api_url:
        raise ValueError(
            "This checkout is registered as an issuekit worker, but api_url is not configured."
        )
    if args.interval < 0:
        raise ValueError("--interval must be non-negative.")
    heartbeat_interval = (
        args.heartbeat_interval
        if args.heartbeat_interval is not None
        else config.worker_heartbeat_interval_sec
    )
    if heartbeat_interval <= 0:
        raise ValueError("--heartbeat-interval must be greater than zero.")
    if args.max_heartbeat_failures < 0:
        raise ValueError("--max-heartbeat-failures must be non-negative.")
    if args.max_run_failures < 0:
        raise ValueError("--max-run-failures must be non-negative.")
    warn_if_staleness_not_wider(DEFAULT_STALE_AFTER_SEC, heartbeat_interval)
    if args.max_issues is not None and args.max_issues < 1:
        raise ValueError("--max-issues must be greater than zero.")
    mode = _resolve_mode(args)
    conflict = _mode_option_conflict(args, mode)
    if conflict is not None:
        raise ValueError(conflict)
    if args.proposal_check_limit < 1:
        raise ValueError("--proposal-check-limit must be greater than zero.")
    return ServeOptions(
        mode=mode,
        agent=agent,
        interval=args.interval,
        heartbeat_interval=heartbeat_interval,
        max_heartbeat_failures=args.max_heartbeat_failures,
        max_run_failures=args.max_run_failures,
        max_issues=args.max_issues,
        proposal_check_limit=args.proposal_check_limit,
        timeout_sec=args.timeout_sec,
        once=args.once,
        triage=args.triage,
        priority=args.priority,
        allow_any_branch=args.allow_any_branch,
        no_sync=args.no_sync,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )


def _preflight(options: ServeOptions, config: IssuekitConfig, cwd: Path):
    role = {
        ServeMode.IMPLEMENT: "implementer",
        ServeMode.REVIEW: "reviewer",
        ServeMode.PROPOSAL_CHECKS: "triage",
    }[options.mode]
    return preflight_agent(
        options.agent,
        config=config,
        model=options.model,
        reasoning_effort=options.reasoning_effort,
        role=role,
    )


def _run_with_runtime(options, config, cwd: Path, adapter) -> int:
    try:
        run_dir = runtime.prepare_run_dir(cwd)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    lock_path = run_dir / "serve.lock"
    log_path = run_dir / "serve.log"
    controller = loop.ShutdownController.create()
    controller.on_signal = lambda signum, count: loop.log_event(
        sys.stderr, None, "signal", signum=signum, count=count
    )

    try:
        with runtime.serve_lock(lock_path), runtime._signal_handlers(controller):
            with runtime._worker_heartbeat(
                config,
                cwd,
                log_path,
                options.heartbeat_interval,
                controller,
                max_failures=options.max_heartbeat_failures,
            ):
                if options.mode is ServeMode.PROPOSAL_CHECKS:
                    return proposal_checks._serve_proposal_checks_loop(
                        options,
                        agent=options.agent,
                        config=config,
                        cwd=cwd,
                        log_path=log_path,
                        controller=controller,
                    )
                if options.mode is ServeMode.REVIEW:
                    return review.run_review_loop(
                        options,
                        agent=options.agent,
                        config=config,
                        cwd=cwd,
                        log_path=log_path,
                        controller=controller,
                    )
                return implement.run_implement_loop(
                    options,
                    agent=options.agent,
                    config=config,
                    cwd=cwd,
                    log_path=log_path,
                    controller=controller,
                    adapter=adapter,
                )
    except runtime.ServeLockError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
