from __future__ import annotations

from pathlib import Path

import pytest

from issuekit.agents.readonly import prompt_from_spec
from issuekit.prompts import TRIAGE_PROMPT


def test_prompt_from_spec_rejects_unknown_keyword(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'context'"):
        prompt_from_spec(
            TRIAGE_PROMPT,
            cwd=tmp_path,
            filename="triage.md",
            body="Rendered prompt.",
            context="unused",
        )
