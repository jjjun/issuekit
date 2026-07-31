"""Agent adapter construction and command-line argument handling."""

from __future__ import annotations

import json
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from issuekit.agentrun.config import AgentRunConfig


class AgentAdapter(ABC):
    """Pluggable adapter for a headless coding agent."""

    @abstractmethod
    def resolve_binary(self) -> Path:
        """Return the path to the agent executable."""

    @abstractmethod
    def build_argv(
        self,
        prompt: str,
        plan_path: Path,
        session_id: str | None = None,
        resume: bool = False,
    ) -> list[str]:
        """Build the command-line argv for the agent."""

    @abstractmethod
    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        """Parse stdout/stderr into a structured dict."""

    def supports_session_resume(self) -> bool:
        """Return True when the adapter can resume a caller-provided session."""
        return False

    def supports_session_continuation(self) -> bool:
        """Return True when the adapter can continue a session it already started."""
        return False

    def effective_runtime(self) -> tuple[str | None, str | None]:
        """Return the effective model and reasoning effort for this run."""
        return None, None

    def compose_prompt(self, prompt: str) -> str:
        """Return the prompt text a runtime must send to the agent."""
        return prompt


class ConfigAgentAdapter(AgentAdapter):
    """Adapter driven by declarative AgentRunConfig."""

    def __init__(
        self,
        agent_name: str,
        run_config: AgentRunConfig,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.run_config = run_config
        self.model = model
        self.reasoning_effort = reasoning_effort
        if (
            (self.reasoning_effort or self.run_config.reasoning_effort)
            and not self.run_config.effort_argv
        ):
            raise ValueError(
                f"Agent '{agent_name}' config sets reasoning_effort but has no effort_argv; "
                "add effort_argv to the agent configuration or remove reasoning_effort."
            )
        if self.run_config.speed is True and not self.run_config.speed_argv:
            raise ValueError(
                f"Agent '{agent_name}' config sets speed but has no speed_argv; "
                "add speed_argv to the agent configuration or remove speed."
            )

    def resolve_binary(self) -> Path:
        found = shutil.which(self.run_config.binary)
        if found:
            return Path(found)
        for p in self.run_config.known_paths:
            expanded = Path(p).expanduser()
            if expanded.exists():
                return expanded
        raise RuntimeError(
            f"{self.run_config.binary} executable not found. "
            "Tried PATH and known per-OS locations."
        )

    def build_argv(
        self,
        prompt: str,
        plan_path: Path,
        session_id: str | None = None,
        resume: bool = False,
    ) -> list[str]:
        if resume and not self.supports_session_continuation():
            raise ValueError(
                f"Agent '{self.agent_name}' cannot continue a session; "
                "add resume_flag to the agent configuration."
            )
        resolved_model, resolved_effort = self.effective_runtime()
        prompt = self.compose_prompt(prompt)
        argv = list(self.run_config.headless_argv)
        argv.append(prompt)
        if self.run_config.approval_flag:
            argv.append(self.run_config.approval_flag)
            if self.run_config.approval_value:
                argv.append(self.run_config.approval_value)
        if self.run_config.output_format_flag and self.run_config.output_format:
            argv.extend(
                [self.run_config.output_format_flag, self.run_config.output_format]
            )
        if resolved_model and self.run_config.model_flag:
            argv.extend([self.run_config.model_flag, resolved_model])
        if resolved_effort:
            argv.extend(
                entry.format(value=resolved_effort)
                for entry in self.run_config.effort_argv
            )
        if self.run_config.speed is True:
            argv.extend(self.run_config.speed_argv)
        if session_id and self.run_config.resumable:
            flag = (
                self.run_config.resume_flag
                if resume
                else self.run_config.session_flag
            )
            if flag:
                argv.extend([flag, session_id])
        return argv

    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        """Parse stdout/stderr into a structured dict."""
        parsed = {
            "stdout": stdout,
            "stderr": stderr,
        }
        if self.run_config.output_format == "json":
            parsed.update(_result_envelope_fields(stdout))
        return parsed

    def supports_session_resume(self) -> bool:
        """Return True when the declarative config has a session flag."""
        return bool(self.run_config.resumable and self.run_config.session_flag)

    def supports_session_continuation(self) -> bool:
        """Return True when the declarative config has a resume flag."""
        return bool(self.run_config.resumable and self.run_config.resume_flag)

    def effective_runtime(self) -> tuple[str | None, str | None]:
        """Return the model and reasoning effort used to build agent argv."""
        return (
            self.model or self.run_config.model,
            self.reasoning_effort or self.run_config.reasoning_effort,
        )

    def compose_prompt(self, prompt: str) -> str:
        """Return the prompt text with configured and model suffixes appended."""
        resolved_model, _ = self.effective_runtime()
        return self._append_prompt_suffixes(prompt, resolved_model)

    def _append_prompt_suffixes(self, prompt: str, resolved_model: str | None) -> str:
        parts = [prompt]
        if self.run_config.prompt_suffix:
            parts.append(self.run_config.prompt_suffix)
        if resolved_model:
            model_prompt = dict(self.run_config.model_prompts).get(resolved_model)
            if model_prompt:
                parts.append(model_prompt)
        return "\n\n".join(parts)


def _result_envelope_fields(stdout: str) -> dict[str, str]:
    """Unwrap a headless JSON result envelope into agent text and run metrics.

    Falls back to leaving stdout untouched when the agent did not emit a
    well-formed envelope, so a crash before the first byte of JSON keeps its
    original diagnostics.
    """
    try:
        envelope = json.loads(stdout)
    except ValueError:
        return {}
    if not isinstance(envelope, dict):
        return {}
    fields: dict[str, str] = {}
    text = envelope.get("result")
    if isinstance(text, str):
        fields["stdout"] = text
    session_id = envelope.get("session_id")
    if isinstance(session_id, str):
        fields["session_id"] = session_id
    cost = envelope.get("total_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        fields["cost_usd"] = str(cost)
    usage = envelope.get("usage")
    if isinstance(usage, dict):
        for name, count in usage.items():
            if isinstance(count, int) and not isinstance(count, bool):
                fields[f"usage_{name}"] = str(count)
    return fields


def build_adapter(
    agent_name: str,
    run_config: AgentRunConfig,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> AgentAdapter:
    """Build an AgentAdapter from runtime configuration."""
    adapter_class: type[ConfigAgentAdapter] = ConfigAgentAdapter
    if run_config.adapter:
        from issuekit.agentrun.adapters.registry import resolve_custom_adapter

        custom_adapter_class = resolve_custom_adapter(run_config.adapter)
        if custom_adapter_class is None:
            raise ValueError(
                f"Unknown adapter '{run_config.adapter}' for agent: {agent_name}"
            )
        adapter_class = custom_adapter_class
    return adapter_class(
        agent_name,
        run_config=run_config,
        model=model,
        reasoning_effort=reasoning_effort,
    )
