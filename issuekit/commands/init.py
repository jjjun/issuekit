"""Implementation of the init command."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from issuekit.config import load_config
from issuekit.config.local import ensure_gitignore_entries
from issuekit.paths import display_path

CODEX_MCP_HEADER = "[mcp_servers.issuekit]"
HANDOFF_HEADER = "## Handoff protocol"
CLAUDE_AGENTS_IMPORT = "@AGENTS.md"
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
        help="Install encoding and handoff scaffolding in the current repository.",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh issuekit entries and issuekit-owned template files.",
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
    load_config(cwd)

    _write_template(cwd, cwd / ".gitattributes", "gitattributes", force, result)
    _write_template(cwd, cwd / ".editorconfig", "editorconfig", force, result)
    _write_local_config_ignore(cwd, result)
    _write_pre_commit(cwd, result)
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
        result.skipped.append(display_path(path, cwd))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_template_text(template_name), encoding="utf-8", newline="\n")
    result.written.append(display_path(path, cwd))


def _write_pre_commit(cwd: Path, result: InitResult) -> None:
    path = cwd / ".pre-commit-config.yaml"
    if path.exists():
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
    _write_claude_agents_import(cwd, result)


def _write_mcp_json(cwd: Path, force: bool, result: InitResult) -> None:
    path = cwd / ".mcp.json"
    if not path.exists():
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

    if "issuekit" in mcp_servers and not force:
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
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{block}\n", encoding="utf-8", newline="\n")
        result.written.append(display_path(path, cwd))
        return

    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        _add_codex_config_guidance(cwd, path, result)
        return

    servers = parsed.get("mcp_servers")
    issuekit_server = servers.get("issuekit") if isinstance(servers, dict) else None
    if issuekit_server is not None:
        table_bounds = find_codex_issuekit_table(content)
        if table_bounds is None:
            if force or not isinstance(issuekit_server, dict) or "env_vars" not in issuekit_server:
                _add_codex_config_guidance(cwd, path, result)
            else:
                result.skipped.append(display_path(path, cwd))
            return

        start, end = table_bounds
        if force:
            updated = f"{content[:start]}{block}\n\n{content[end:]}"
        elif "env_vars" not in issuekit_server:
            header_end = content.find("\n", start, end)
            if header_end == -1:
                updated = f"{content[:end]}\n{_codex_env_vars_line()}\n{content[end:]}"
            else:
                updated = (
                    f"{content[:header_end + 1]}"
                    f"{_codex_env_vars_line()}\n"
                    f"{content[header_end + 1:]}"
                )
        else:
            result.skipped.append(display_path(path, cwd))
            return

        try:
            tomllib.loads(updated)
        except tomllib.TOMLDecodeError:
            _add_codex_config_guidance(cwd, path, result)
            return
        path.write_text(updated, encoding="utf-8", newline="\n")
        result.written.append(display_path(path, cwd))
        return

    updated = append_codex_issuekit_table(content, block)
    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError:
        _add_codex_config_guidance(cwd, path, result)
        return
    path.write_text(updated, encoding="utf-8", newline="\n")
    result.written.append(display_path(path, cwd))


def find_codex_issuekit_table(content: str) -> tuple[int, int] | None:
    lines = content.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    headers = [
        index
        for index, line in enumerate(lines)
        if re.fullmatch(r"\s*\[{1,2}.*\]\s*(?:#.*)?(?:\r?\n)?", line)
    ]
    target_lines = [
        index
        for index in headers
        if re.fullmatch(r"\s*\[mcp_servers\.issuekit\]\s*(?:#.*)?(?:\r?\n)?", lines[index])
    ]
    if len(target_lines) != 1:
        return None

    target_index = target_lines[0]
    next_header = next((index for index in headers if index > target_index), len(lines))
    start = offsets[target_index]
    end = offsets[next_header] if next_header < len(lines) else len(content)
    return start, end


def append_codex_issuekit_table(content: str, block: str) -> str:
    prefix = "\n" if content.endswith("\n") else "\n\n"
    return f"{content}{prefix}{block}\n"


def _codex_env_vars_line() -> str:
    for line in _template_text("codex_config.toml").splitlines():
        if line.startswith("env_vars = "):
            return line
    raise RuntimeError("Codex issuekit template is missing env_vars.")


def _add_codex_config_guidance(cwd: Path, path: Path, result: InitResult) -> None:
    result.skipped.append(display_path(path, cwd))
    result.guidance.append(
        "Could not merge .codex/config.toml automatically. Add or refresh this "
        "issuekit server manually:\n\n"
        f"{_template_text('codex_config.toml').rstrip()}"
    )


def _write_handoff_reference(cwd: Path, path: Path, result: InitResult) -> None:
    reference = _template_text("handoff_reference.md").rstrip()
    if not path.exists():
        path.write_text(f"# {path.name}\n\n{reference}\n", encoding="utf-8", newline="\n")
        result.written.append(display_path(path, cwd))
        return

    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    if HANDOFF_HEADER in content:
        result.skipped.append(display_path(path, cwd))
        return

    prefix = "\n" if content.endswith("\n") else "\n\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{prefix}{reference}\n")
    result.written.append(display_path(path, cwd))


def _write_claude_agents_import(cwd: Path, result: InitResult) -> None:
    path = cwd / "CLAUDE.md"
    if not path.exists():
        path.write_text(f"{CLAUDE_AGENTS_IMPORT}\n", encoding="utf-8", newline="\n")
        result.written.append(display_path(path, cwd))
        return

    content = path.read_text(encoding="utf-8-sig", errors="ignore")
    if any(line.strip() == CLAUDE_AGENTS_IMPORT for line in content.splitlines()):
        result.skipped.append(display_path(path, cwd))
        return

    result.skipped.append(display_path(path, cwd))
    result.guidance.append(
        "CLAUDE.md does not import AGENTS.md. Add `@AGENTS.md` so Claude Code "
        "reads the shared repository guidance."
    )


def _template_text(template_name: str) -> str:
    return resources.files("issuekit.templates").joinpath(template_name).read_text(encoding="utf-8")
