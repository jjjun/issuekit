"""Codex exec adapter."""

from __future__ import annotations

import json
import subprocess
from functools import cache
from pathlib import Path

from issuekit.agentrun.adapter import ConfigAgentAdapter
from issuekit.agentrun.parsed import encode_usage, int_counts


class CodexAdapter(ConfigAgentAdapter):
    """Adapter for the Codex exec JSONL event contract."""

    def resolve_binary(self) -> Path:
        binary = super().resolve_binary()
        sandbox_mode = self._sandbox_mode()
        if sandbox_mode is not None:
            diagnostic = _probe_sandbox(str(binary), sandbox_mode)
            if diagnostic is not None:
                raise RuntimeError(
                    f"Codex sandbox preflight failed for mode '{sandbox_mode}'; "
                    "the agent was not launched. Probe stderr: "
                    f"{diagnostic}\nAllow unprivileged user namespaces for "
                    "bubblewrap, for example with an AppArmor profile for "
                    "/usr/bin/bwrap containing 'userns,' or by setting "
                    "kernel.apparmor_restrict_unprivileged_userns=0, then rerun. "
                    "To opt out for this role, configure "
                    "[agents.codex.roles.<role>] approval_argv = [...]."
                )
        return binary

    def _sandbox_mode(self) -> str | None:
        if self.run_config.runtime != "exec":
            return None

        approval_argv = self.effective_approval_argv()

        for index, argument in enumerate(approval_argv):
            if argument in ("--sandbox", "-s"):
                if index + 1 < len(approval_argv):
                    return approval_argv[index + 1]
            elif argument.startswith("--sandbox="):
                return argument.partition("=")[2]
        return None

    def build_argv(
        self,
        prompt: str,
        plan_path: Path,
        session_id: str | None = None,
        resume: bool = False,
    ) -> list[str]:
        argv = super().build_argv(
            prompt,
            plan_path,
            session_id=session_id,
            resume=resume,
        )
        argv.append("--json")
        return argv

    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        parsed = super().parse_output(stdout, stderr)
        session_id: str | None = None
        final_message: str | None = None
        usage: dict[str, int] = {}
        saw_event = False
        turn_completed = False
        turn_failed = False
        error_event_pending = False
        failure_reason: str | None = None

        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                continue

            saw_event = True
            event_type = event["type"]
            if event_type == "thread.started":
                thread_id = event.get("thread_id")
                if isinstance(thread_id, str):
                    session_id = thread_id
            elif event_type == "item.completed":
                item = event.get("item")
                if (
                    isinstance(item, dict)
                    and item.get("type") == "agent_message"
                    and isinstance(item.get("text"), str)
                ):
                    final_message = item["text"]
            elif event_type == "turn.completed":
                turn_completed = True
                error_event_pending = False
                if not turn_failed:
                    failure_reason = None
                usage = int_counts(event.get("usage"))
            elif event_type == "turn.failed":
                turn_failed = True
                error = event.get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    failure_reason = error["message"]
            elif event_type == "error":
                error_event_pending = True
                message = event.get("message")
                if isinstance(message, str):
                    failure_reason = message

        if final_message is not None:
            parsed["stdout"] = final_message
        elif saw_event:
            parsed["stdout"] = ""
        if session_id is not None:
            parsed["session_id"] = session_id
        parsed.update(encode_usage(usage))
        if turn_failed or error_event_pending:
            parsed["is_error"] = "true"
            if failure_reason is not None:
                parsed["failure_reason"] = failure_reason
        elif turn_completed:
            parsed["is_error"] = "false"
        return parsed


@cache
def _probe_sandbox(binary: str, mode: str) -> str | None:
    """Return a diagnostic when Codex cannot execute a command in this sandbox."""
    try:
        result = subprocess.run(
            [
                binary,
                "sandbox",
                "-c",
                f"sandbox_mode={json.dumps(mode)}",
                "--",
                "true",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        diagnostic = exc.stderr or exc.stdout
        if isinstance(diagnostic, bytes):
            diagnostic = diagnostic.decode(errors="replace")
        return (diagnostic or str(exc)).strip()
    except OSError as exc:
        return str(exc)

    if result.returncode == 0:
        return None
    return (
        result.stderr.strip()
        or result.stdout.strip()
        or f"probe exited with status {result.returncode} without diagnostic output"
    )
