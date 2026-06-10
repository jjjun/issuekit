"""Claude Code headless adapter."""

from __future__ import annotations

from issuekit.agents.runner import ConfigAgentAdapter
from issuekit.config import IssuekitConfig


class ClaudeAdapter(ConfigAgentAdapter):
    """Adapter for the Claude Code CLI non-interactive mode.

    Verified contract (claude CLI):
    - Non-interactive print mode is ``claude -p "<prompt>"`` (alias ``--print``).
      The prompt is read from argv, so ``stdin=subprocess.DEVNULL`` is safe (same
      as the codex contract).
    - ``--permission-mode acceptEdits`` auto-accepts file edits while still
      gating bash and other actions. This is the chosen default; it is the
      closest match to codex ``--full-auto`` autonomy without removing every
      gate. ``--permission-mode bypassPermissions`` removes all gates with no
      sandbox and is left as an explicit per-repo opt-in override only.
    - Output format is selected with ``--output-format text`` (also ``json`` and
      ``stream-json``).
    - Model selection is ``--model <name>``.
    - The final answer goes to stdout; session/diagnostic logs go to stderr.
      Exit code is 0 on success and non-zero on failure.
    """

    def __init__(
        self,
        config: IssuekitConfig | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__("claude", config=config, model=model)
