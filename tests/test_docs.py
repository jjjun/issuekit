"""Regression coverage for documentation module references."""

import re
from pathlib import Path


_FENCED_CODE_BLOCK = re.compile(r"^```[^\n]*\n.*?^```[^\n]*$", re.MULTILINE | re.DOTALL)
_BACKTICKED_TOKEN = re.compile(r"`+([^`\n]+)`+")
_DOCUMENTATION_PATHS = (
    "README.md",
    "ISSUEKIT.md",
    "AGENTS.md",
    "CLAUDE.md",
    "issuekit/agentrun/README.md",
)
_NON_MODULE_REFERENCES = {
    "issuekit.git",
    "issuekit.issuekit",
    "issuekit.local.toml",
    "issuekit.repo_metadata",
    "issuekit.toml",
    "issuekit.worker_metadata",
    "issuekit.workspace.toml",
}


def _documentation_files(root: Path) -> tuple[Path, ...]:
    return tuple(root / path for path in _DOCUMENTATION_PATHS) + tuple(
        sorted((root / "issuekit/templates").glob("*.md"))
    )


def _module_reference_path(token: str, package_root: Path) -> Path | None:
    if any(character.isspace() for character in token):
        return None
    if token in _NON_MODULE_REFERENCES or token == "issuekit":
        return None

    if token.startswith("issuekit."):
        relative_path = token.replace(".", "/")
    elif token.startswith("issuekit/"):
        relative_path = token.rstrip("/")
    elif "/" in token:
        relative_path = token.rstrip("/")
        first_component = relative_path.split("/", 1)[0]
        if not (package_root / first_component).is_dir():
            return None
    else:
        return None

    if "." in relative_path.rsplit("/", 1)[-1] and not relative_path.endswith(".py"):
        return None

    if relative_path.endswith(".py"):
        relative_path = relative_path.removesuffix(".py")

    if token.startswith("issuekit"):
        path = package_root.parent / relative_path
    else:
        path = package_root / relative_path
    if path == package_root:
        return None
    return path


def _reference_exists(path: Path) -> bool:
    return path.is_dir() or path.with_suffix(".py").is_file()


def _invalid_module_references(
    documentation_files: tuple[Path, ...], package_root: Path
) -> list[str]:
    errors = []
    for documentation_file in documentation_files:
        contents = _FENCED_CODE_BLOCK.sub(
            "", documentation_file.read_text(encoding="utf-8")
        )
        for token in _BACKTICKED_TOKEN.findall(contents):
            path = _module_reference_path(token, package_root)
            if path is not None and not _reference_exists(path):
                errors.append(f"{documentation_file}: `{token}` does not exist")
    return errors


def test_documented_issuekit_module_references_exist() -> None:
    root = Path(__file__).parents[1]

    assert _invalid_module_references(_documentation_files(root), root / "issuekit") == []


def test_documented_issuekit_module_references_reject_missing_modules(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    documentation_file = tmp_path / "ISSUEKIT.md"
    documentation_file.write_text(
        (root / "ISSUEKIT.md").read_text(encoding="utf-8")
        + "\n```\nSee `issuekit.fenced_example`.\n```\n"
        + "See `issuekit.client_resources`.\n",
        encoding="utf-8",
    )

    assert _invalid_module_references((documentation_file,), root / "issuekit") == [
        f"{documentation_file}: `issuekit.client_resources` does not exist"
    ]


def test_tests_workflow_is_manual_only() -> None:
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    trigger_start = workflow.index("on:\n") + len("on:\n")
    trigger_end = workflow.index("\npermissions:", trigger_start)

    assert workflow[trigger_start:trigger_end] == "  workflow_dispatch:\n"
