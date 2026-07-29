"""Implementation of the init command."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from issuekit.config import load_config
from issuekit.config.local import ensure_gitignore_entries

CODEX_MCP_HEADER = "[mcp_servers.issuekit]"
HANDOFF_HEADER = "## Handoff protocol"
PRE_COMMIT_GUIDANCE = """Add these hooks to .pre-commit-config.yaml:

repos:
  - repo: local
    hooks:
      - id: issuekit-check-encoding
        name: issuekit check-encoding
        entry: issuekit check-encoding
        language: system
        pass_filenames: false
      - id: issuekit-author-guard
        name: issuekit author-guard
        entry: issuekit author-guard check
        language: system
        pass_filenames: false
"""


def register(subparsers: argparse._SubParsersAction) -> None:
    init_parser = subparsers.add_parser(
        "init",
        help="Install docs/issues tracker templates in the current repository.",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing templated files.",
    )
    init_parser.add_argument(
        "--with-mcp",
        action="store_true",
        help="Also scaffold MCP registration and thin agent protocol references.",
    )
    init_parser.set_defaults(func=run)


@dataclass
class InitResult:
    written: list[str]
    skipped: list[str]
    guidance: list[str]


def run(args) -> int:
    result = init_repo(Path.cwd(), force=args.force, with_mcp=args.with_mcp)
    for path in result.written:
        print(f"Wrote: {path}")
    for path in result.skipped:
        print(f"Skipped existing: {path}")
    for item in result.guidance:
        print(item)
    return 0


def init_repo(cwd: Path, *, force: bool = False, with_mcp: bool = False) -> InitResult:
    result = InitResult(written=[], skipped=[], guidance=[])
    config = load_config(cwd)
    issues_dir = config.issues_path(cwd)

    _write_template(cwd, cwd / ".gitattributes", "gitattributes", force, result)
    _write_template(cwd, cwd / ".editorconfig", "editorconfig", force, result)
    _write_template(cwd, issues_dir / "README.md", "issues_README.md", force, result)
    _write_local_config_ignore(cwd, result)
    _write_pre_commit(cwd, force, result)
    if with_mcp:
        _write_mcp_scaffold(cwd, force, result)
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
        if "issuekit check-encoding" not in content or "issuekit author-guard check" not in content:
            result.guidance.append(PRE_COMMIT_GUIDANCE.rstrip())
        return
    path.write_text(_template_text("pre-commit-config.yaml"), encoding="utf-8", newline="\n")
    result.written.append(".pre-commit-config.yaml")


def _write_local_config_ignore(cwd: Path, result: InitResult) -> None:
    path = cwd / ".gitignore"
    wrote = ensure_gitignore_entries(cwd)
    if wrote:
        result.written.append(".gitignore")
    elif path.exists():
        result.skipped.append(".gitignore")


def _write_mcp_scaffold(cwd: Path, force: bool, result: InitResult) -> None:
    _write_mcp_json(cwd, force, result)
    _write_codex_config(cwd, force, result)
    _write_handoff_reference(cwd, cwd / "AGENTS.md", result)
    _write_handoff_reference(cwd, cwd / "CLAUDE.md", result)


def _write_mcp_json(cwd: Path, force: bool, result: InitResult) -> None:
    path = cwd / ".mcp.json"
    if not path.exists() or force:
        _write_template(cwd, path, "mcp.json", force, result)
        return

    template = json.loads(_template_text("mcp.json"))
    issuekit_server = template["mcpServers"]["issuekit"]
    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        _add_mcp_json_guidance(result)
        return

    if not isinstance(data, dict):
        _add_mcp_json_guidance(result)
        return

    mcp_servers = data.setdefault("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        _add_mcp_json_guidance(result)
        return

    if "issuekit" in mcp_servers:
        result.skipped.append(".mcp.json")
        return

    mcp_servers["issuekit"] = issuekit_server
    path.write_text(f"{json.dumps(data, indent=2)}\n", encoding="utf-8", newline="\n")
    result.written.append(".mcp.json")


def _add_mcp_json_guidance(result: InitResult) -> None:
    result.skipped.append(".mcp.json")
    result.guidance.append(
        "Could not merge .mcp.json automatically. Add this issuekit server manually:\n\n"
        f"{_template_text('mcp.json').rstrip()}"
    )


def _write_codex_config(cwd: Path, force: bool, result: InitResult) -> None:
    path = cwd / ".codex" / "config.toml"
    block = _template_text("codex_config.toml").rstrip()
    if not path.exists() or force:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{block}\n", encoding="utf-8", newline="\n")
        result.written.append(_display_path(cwd, path))
        return

    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    if CODEX_MCP_HEADER in content:
        result.skipped.append(_display_path(cwd, path))
        return

    prefix = "\n" if content.endswith("\n") else "\n\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{prefix}{block}\n")
    result.written.append(_display_path(cwd, path))


def _write_handoff_reference(cwd: Path, path: Path, result: InitResult) -> None:
    reference = _template_text("handoff_reference.md").rstrip()
    if not path.exists():
        path.write_text(f"# {path.name}\n\n{reference}\n", encoding="utf-8", newline="\n")
        result.written.append(_display_path(cwd, path))
        return

    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    if HANDOFF_HEADER in content:
        result.skipped.append(_display_path(cwd, path))
        return

    prefix = "\n" if content.endswith("\n") else "\n\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{prefix}{reference}\n")
    result.written.append(_display_path(cwd, path))


def _template_text(template_name: str) -> str:
    return resources.files("issuekit.templates").joinpath(template_name).read_text(encoding="utf-8")


def _display_path(cwd: Path, path: Path) -> str:
    try:
        return path.relative_to(cwd).as_posix()
    except ValueError:
        return path.as_posix()
