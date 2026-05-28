"""Implementation of the init command."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from issuekit.commands.generate_indexes import write_index_files
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import has_non_ascii, read_all_issues


ISSUEKIT_BLOCK_HEADER = "[tool.issuekit]"
PRE_COMMIT_GUIDANCE = """Add this hook to .pre-commit-config.yaml:

repos:
  - repo: local
    hooks:
      - id: issuekit-check-encoding
        name: issuekit check-encoding
        entry: issuekit check-encoding
        language: system
        pass_filenames: false
"""


@dataclass
class InitResult:
    written: list[str]
    skipped: list[str]
    guidance: list[str]


def run(args) -> int:
    result = init_repo(Path.cwd(), force=args.force)
    for path in result.written:
        print(f"Wrote: {path}")
    for path in result.skipped:
        print(f"Skipped existing: {path}")
    for item in result.guidance:
        print(item)
    return 0


def init_repo(cwd: Path, *, force: bool = False) -> InitResult:
    result = InitResult(written=[], skipped=[], guidance=[])
    config = load_config(cwd)
    issues_dir = config.issues_path(cwd)

    for directory in (issues_dir / "active", issues_dir / "completed", issues_dir / "indexes"):
        directory.mkdir(parents=True, exist_ok=True)

    _write_template(cwd, cwd / ".gitattributes", "gitattributes", force, result)
    _write_template(cwd, cwd / ".editorconfig", "editorconfig", force, result)
    _write_template(cwd, issues_dir / "README.md", "issues_README.md", force, result)
    _write_pre_commit(cwd, force, result)
    _handle_ascii_threshold(cwd, issues_dir, result)

    config = load_config(cwd)
    write_index_files(config.issues_path(cwd), config.recent_count)
    for name in sorted((config.issues_path(cwd) / "indexes").glob("*.md")):
        result.written.append(name.relative_to(cwd).as_posix())
    return result


def _write_template(
    cwd: Path,
    path: Path,
    template_name: str,
    force: bool,
    result: InitResult,
) -> None:
    if path.exists() and not force:
        result.skipped.append(_display_path(cwd, path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_template_text(template_name), encoding="utf-8", newline="\n")
    result.written.append(_display_path(cwd, path))


def _write_pre_commit(cwd: Path, force: bool, result: InitResult) -> None:
    path = cwd / ".pre-commit-config.yaml"
    if path.exists() and not force:
        result.skipped.append(".pre-commit-config.yaml")
        content = path.read_text(encoding="utf-8-sig", errors="ignore")
        if "issuekit check-encoding" not in content:
            result.guidance.append(PRE_COMMIT_GUIDANCE.rstrip())
        return
    path.write_text(_template_text("pre-commit-config.yaml"), encoding="utf-8", newline="\n")
    result.written.append(".pre-commit-config.yaml")


def _handle_ascii_threshold(cwd: Path, issues_dir: Path, result: InitResult) -> None:
    _, _, issues = read_all_issues(issues_dir)
    legacy_non_ascii = [issue for issue in issues if has_non_ascii(issue.content)]
    if not legacy_non_ascii:
        return

    threshold = max(issue.id or 0 for issue in issues) + 1
    block = _issuekit_block(threshold)
    pyproject = cwd / "pyproject.toml"
    if not pyproject.exists():
        result.guidance.append(f"Add this block to pyproject.toml:\n\n{block.rstrip()}")
        return

    content = pyproject.read_text(encoding="utf-8-sig")
    if ISSUEKIT_BLOCK_HEADER in content:
        if "ascii_id_threshold" not in _tool_issuekit_section(content):
            result.guidance.append(
                f"Set ascii_id_threshold = {threshold} in the existing [tool.issuekit] block."
            )
        return

    separator = "" if content.endswith("\n") else "\n"
    pyproject.write_text(f"{content}{separator}\n{block}", encoding="utf-8", newline="\n")
    result.written.append("pyproject.toml")


def _tool_issuekit_section(content: str) -> str:
    start = content.find(ISSUEKIT_BLOCK_HEADER)
    if start == -1:
        return ""
    next_section = content.find("\n[", start + len(ISSUEKIT_BLOCK_HEADER))
    return content[start:] if next_section == -1 else content[start:next_section]


def _issuekit_block(threshold: int) -> str:
    defaults = IssuekitConfig()
    return (
        "[tool.issuekit]\n"
        f"recent_count = {defaults.recent_count}\n"
        f"ascii_id_threshold = {threshold}\n"
        f"issues_dir = \"{defaults.issues_dir}\"\n"
    )


def _template_text(template_name: str) -> str:
    return resources.files("issuekit.templates").joinpath(template_name).read_text(encoding="utf-8")


def _display_path(cwd: Path, path: Path) -> str:
    try:
        return path.relative_to(cwd).as_posix()
    except ValueError:
        return path.as_posix()
