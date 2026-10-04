import json
from dataclasses import dataclass
from pathlib import Path

from issuekit.agentrun import AgentPrompt, AgentResult
from tests.git_helpers import init_git_repo
from tests.issue_helpers import api_issue


class FakeRunner:
    """Returns pre-seeded agent stdout blocks in call order."""

    def __init__(self, outputs: list[str]) -> None:
        self._outputs = list(outputs)
        self.calls: list[dict] = []

    def run(self, adapter, prompt: AgentPrompt, repo, **kwargs) -> AgentResult:
        self.calls.append({"prompt": prompt, "repo": repo, **kwargs})
        text = self._outputs.pop(0) if self._outputs else ""
        return AgentResult(
            exit_code=0,
            stdout_path=Path("out.log"),
            agent_log_path=Path("agent.log"),
            elapsed_sec=0.1,
            timed_out=False,
            parsed={"stdout": text},
        )


@dataclass(frozen=True)
class FakeResult:
    exit_code: int = 0
    stdout_path: Path = Path("out.log")
    agent_log_path: Path = Path("agent.log")
    elapsed_sec: float = 1.25
    timed_out: bool = False
    parsed: dict[str, str] | None = None
    status_short: str | None = ""
    status_path: Path | None = Path("status.json")
    report_path: Path | None = None


def create_reviewable_diff(path: Path) -> None:
    (path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    (path / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    init_git_repo(path, message="baseline", autocrlf=True)
    (path / "code.py").write_text("value = 2\n", encoding="utf-8", newline="\n")


def implementing_issue(
    issue_id: int, worker: str, *, assignee: str = "claude"
) -> dict:
    return api_issue(
        issue_id,
        f"Task {issue_id}",
        status="in_progress",
        stage="implementing",
        assignee=assignee,
        implementer=assignee,
        worker=worker,
    )


def fenced_block(language: str, payload: dict) -> str:
    return f"```{language}\n{json.dumps(payload)}\n```\n"
