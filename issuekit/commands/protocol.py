"""Implementation of the protocol command."""

from __future__ import annotations

import argparse

from issuekit.config import load_config
from issuekit.prompts.protocol import render_protocol


def register(subparsers: argparse._SubParsersAction) -> None:
    protocol_parser = subparsers.add_parser(
        "protocol",
        help="Print the current handoff protocol.",
    )
    protocol_parser.add_argument(
        "--agent",
        help="Print the protocol for one agent (defaults to implementer flow for unknown agents).",
    )
    protocol_parser.add_argument(
        "--role",
        choices=("author", "implementer", "pm", "reviewer", "triage"),
        help="Print the protocol for a specific role instead of the agent default.",
    )
    protocol_parser.set_defaults(func=run)


def run(args) -> int:
    config = load_config()
    print(render_protocol(args.agent, role=args.role, agent_roles=config.agent_roles), end="")
    return 0
