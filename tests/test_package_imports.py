"""Regression coverage for package import boundaries."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "imports",
    (
        ("issuekit.workers", "issuekit.config"),
        ("issuekit.config", "issuekit.workers"),
    ),
)
def test_config_and_workers_import_in_either_order(imports: tuple[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, "-c", "; ".join(f"import {module}" for module in imports)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_agentrun_does_not_import_application_layers() -> None:
    agentrun_dir = Path(__file__).parents[1] / "issuekit" / "agentrun"
    forbidden_modules = (
        "issuekit.config",
        "issuekit.proposals",
        "issuekit.store",
        "issuekit.workflow",
    )
    violations: list[str] = []

    for path in agentrun_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = [node.module]
            else:
                continue
            for module in modules:
                if module.startswith(forbidden_modules):
                    violations.append(f"{path.relative_to(agentrun_dir)}: {module}")

    assert violations == []
