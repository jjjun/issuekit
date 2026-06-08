"""Implementation of the implement command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.agents.runner import AgentRunner, resolve_adapter
from issuekit.config import load_config
from issuekit.core import read_all_issues


def run(args) -> int:
    try:
        issue_id = int(args.id)
    except ValueError:
        print(f"Invalid issue id: {args.id}", file=sys.stderr)
        return 1

    cwd = Path.cwd()
    config = load_config(cwd)
    issues_dir = config.issues_path(cwd)
    active_issues, _, _ = read_all_issues(issues_dir)
    issue = next((candidate for candidate in active_issues if candidate.id == issue_id), None)
    if issue is None:
        print(f"Active issue #{issue_id} was not found.", file=sys.stderr)
        return 1
    if issue.decode_error:
        print(
            f"Active issue #{issue_id} is not valid UTF-8: {issue.relative_path}",
            file=sys.stderr,
        )
        return 1

    try:
        adapter = resolve_adapter(args.agent, config=config, model=args.model)
        result = AgentRunner().run(
            adapter,
            issue.file_path,
            cwd,
            timeout=float(args.timeout_sec),
            agent_name=args.agent,
            issue_id=issue.id,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"issue={issue.id} file={issue.relative_path} agent={args.agent}")
    print(
        "exit_code={exit_code} timed_out={timed_out} elapsed_sec={elapsed:.2f}".format(
            exit_code=result.exit_code,
            timed_out=str(result.timed_out).lower(),
            elapsed=result.elapsed_sec,
        )
    )
    print(f"stdout_log={result.stdout_path}")
    print(f"stderr_log={result.stderr_path}")
    if result.status_path:
        print(f"status_file={result.status_path}")
    if result.parsed:
        for key, value in sorted(result.parsed.items()):
            if key in {"stdout", "stderr"} or not value:
                continue
            print(f"{key}={value}")

    print("--- git status --short ---")
    if result.status_short:
        print(result.status_short)
    elif result.status_short == "":
        print("No changes.")
    else:
        print("Unavailable.")

    if result.timed_out:
        return 124
    return result.exit_code if result.exit_code >= 0 else 1
