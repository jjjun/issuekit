"""Codex exec adapter."""

from __future__ import annotations

import json
from pathlib import Path

from issuekit.agentrun.adapter import ConfigAgentAdapter
from issuekit.agentrun.config import AgentRunConfig


class CodexAdapter(ConfigAgentAdapter):
    """Adapter for the Codex exec JSONL event contract."""

    def __init__(
        self,
        agent_name: str = "codex",
        *,
        run_config: AgentRunConfig,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        super().__init__(
            agent_name,
            run_config=run_config,
            model=model,
            reasoning_effort=reasoning_effort,
        )

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
        is_error = False
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
                usage = _usage_counts(event.get("usage"))
            elif event_type == "turn.failed":
                is_error = True
                error = event.get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    failure_reason = error["message"]
            elif event_type == "error":
                is_error = True
                message = event.get("message")
                if isinstance(message, str):
                    failure_reason = message

        if final_message is not None:
            parsed["stdout"] = final_message
        elif saw_event:
            parsed["stdout"] = ""
        if session_id is not None:
            parsed["session_id"] = session_id
        parsed.update({f"usage_{name}": str(count) for name, count in usage.items()})
        if is_error:
            parsed["is_error"] = "true"
            if failure_reason is not None:
                parsed["failure_reason"] = failure_reason
        elif turn_completed:
            parsed["is_error"] = "false"
        return parsed


def _usage_counts(usage: object) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    return {
        name: count
        for name, count in usage.items()
        if isinstance(name, str) and isinstance(count, int) and not isinstance(count, bool)
    }
