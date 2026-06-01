"""Implementation of the protocol command."""

from __future__ import annotations

from issuekit.protocol import render_protocol


def run(args) -> int:
    print(render_protocol(args.agent), end="")
    return 0
