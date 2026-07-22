"""Regression coverage for package import boundaries."""

import subprocess
import sys

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
