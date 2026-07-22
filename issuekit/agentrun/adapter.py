"""Agent adapter construction and command-line argument handling."""

from __future__ import annotations

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
    ) -> list[str]:
        """Build the command-line argv for the agent."""

    @abstractmethod
    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        """Parse stdout/stderr into a structured dict."""

    def supports_session_resume(self) -> bool:
        """Return True when the adapter can resume a caller-provided session."""
        return False


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
                f"Agent '{agent_name}' config sets reasoning_effort but has no effort_argv."
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
    ) -> list[str]:
        resolved_model = self.model or self.run_config.model
        resolved_effort = self.reasoning_effort or self.run_config.reasoning_effort
        prompt = self._append_prompt_suffixes(prompt, resolved_model)
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
        if (
            session_id
            and self.run_config.resumable
            and self.run_config.session_flag
        ):
            argv.extend([self.run_config.session_flag, session_id])
        return argv

    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        """Parse stdout/stderr into a structured dict."""
        return {
            "stdout": stdout,
            "stderr": stderr,
        }

    def supports_session_resume(self) -> bool:
        """Return True when the declarative config has a session flag."""
        return bool(self.run_config.resumable and self.run_config.session_flag)

    def _append_prompt_suffixes(self, prompt: str, resolved_model: str | None) -> str:
        parts = [prompt]
        if self.run_config.prompt_suffix:
            parts.append(self.run_config.prompt_suffix)
        if resolved_model:
            model_prompt = dict(self.run_config.model_prompts).get(resolved_model)
            if model_prompt:
                parts.append(model_prompt)
        return "\n\n".join(parts)


def build_adapter(
    agent_name: str,
    run_config: AgentRunConfig,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> AgentAdapter:
    """Build an AgentAdapter from runtime configuration."""
    if run_config.adapter:
        from issuekit.agentrun.adapters.registry import resolve_custom_adapter

        adapter_class = resolve_custom_adapter(run_config.adapter)
        if adapter_class is None:
            raise ValueError(
                f"Unknown adapter '{run_config.adapter}' for agent: {agent_name}"
            )
        return adapter_class(
            agent_name,
            run_config=run_config,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    return ConfigAgentAdapter(
        agent_name,
        run_config=run_config,
        model=model,
        reasoning_effort=reasoning_effort,
    )
