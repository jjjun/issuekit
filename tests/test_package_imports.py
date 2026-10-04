"""Regression coverage for package import boundaries."""

import ast
import subprocess
import sys
from importlib.util import find_spec
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


def test_all_package_modules_import_in_a_fresh_interpreter() -> None:
    package_dir = Path(__file__).parents[1] / "issuekit"
    module_names = []
    include_mcp = find_spec("mcp") is not None

    for path in sorted(package_dir.rglob("*.py")):
        parts = path.relative_to(package_dir).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and parts[0] == "mcp" and not include_mcp:
            continue
        module_names.append("issuekit" + ("." + ".".join(parts) if parts else ""))

    script = (
        "import importlib\n"
        f"for module in {module_names!r}:\n"
        "    importlib.import_module(module)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        cwd=package_dir.parent,
    )

    assert result.returncode == 0, result.stderr


def test_api_import_does_not_import_workflow() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\nimport issuekit.api\n"
            "assert 'issuekit.workflow' not in sys.modules\n",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[1],
    )

    assert result.returncode == 0, result.stderr


def test_errors_module_imports_only_standard_library_and_separation_guard() -> None:
    errors_path = Path(__file__).parents[1] / "issuekit" / "errors.py"
    tree = ast.parse(errors_path.read_text(encoding="utf-8"), filename=str(errors_path))
    issuekit_imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            issuekit_imports.extend(
                alias.name for alias in node.names if alias.name.startswith("issuekit")
            )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith("issuekit"):
                issuekit_imports.append(node.module)

    assert issuekit_imports == ["issuekit.guards.separation"]


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
