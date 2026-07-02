"""Implementation of the check-encoding command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from issuekit.core import has_mojibake
from issuekit.gitutil import run_git


SOURCE_EXTENSIONS = {
    "ts",
    "tsx",
    "js",
    "jsx",
    "mjs",
    "cjs",
    "json",
    "md",
    "mdx",
    "css",
    "scss",
    "html",
    "yml",
    "yaml",
    "py",
    "toml",
    "cfg",
    "ini",
    "txt",
}
BOM = b"\xef\xbb\xbf"


def register(subparsers: argparse._SubParsersAction) -> None:
    check_encoding_parser = subparsers.add_parser(
        "check-encoding",
        help="Check tracked files for encoding problems.",
    )
    check_encoding_parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output.",
    )
    check_encoding_parser.add_argument(
        "--no-mojibake",
        action="store_true",
        help="Disable likely mojibake text scanning.",
    )
    check_encoding_parser.add_argument(
        "--no-crlf",
        action="store_true",
        help="Disable CRLF line-ending scanning.",
    )
    check_encoding_parser.add_argument(
        "--fix",
        action="store_true",
        help="Strip leading UTF-8 BOM bytes from tracked source files.",
    )
    check_encoding_parser.set_defaults(func=run)


def run(args) -> int:
    tracked_files = list_tracked_files(Path.cwd())
    source_files = [
        file for file in tracked_files if _has_source_extension(file) and not _is_issue_file(file)
    ]
    bom_files: list[str] = []
    mojibake_files: list[str] = []
    fixed_files: list[str] = []
    crlf_files = [] if args.no_crlf else list_crlf_files(Path.cwd())

    for file in source_files:
        path = Path(file)
        try:
            content = path.read_bytes()
            has_bom = content.startswith(BOM)
            if has_bom:
                bom_files.append(file)
                if args.fix:
                    path.write_bytes(content[len(BOM) :])
                    content = content[len(BOM) :]
                    fixed_files.append(file)
            if not args.no_mojibake and has_mojibake(content.decode("utf-8", errors="ignore")):
                mojibake_files.append(file)
        except OSError:
            continue

    remaining_bom_files = [] if args.fix else bom_files
    payload = {
        "bom_files": remaining_bom_files,
        "mojibake_files": mojibake_files,
        "crlf_files": crlf_files,
        "fixed": fixed_files,
    }
    if args.json:
        print(json.dumps(payload, indent=2))

    if not remaining_bom_files and not mojibake_files and not crlf_files:
        if not args.json:
            for file in fixed_files:
                print(f"Fixed BOM: {file}")
            if fixed_files:
                print("Encoding check passed after fixing UTF-8 BOM files; no mojibake or CRLF found.")
            else:
                print("Encoding check passed: no UTF-8 BOM, likely mojibake, or CRLF in tracked files.")
        return 0

    if not args.json:
        for file in fixed_files:
            print(f"Fixed BOM: {file}")
        if remaining_bom_files:
            print(
                f"Encoding check failed: {len(remaining_bom_files)} file(s) start with a UTF-8 BOM.",
                file=sys.stderr,
            )
            print("Re-save these files as UTF-8 without a BOM:", file=sys.stderr)
            for file in remaining_bom_files:
                print(f"  {file}", file=sys.stderr)
            print(
                "\nTip: a BOM is invisible to ripgrep; verify with `head -c 3 <file> | xxd`.",
                file=sys.stderr,
            )
        if mojibake_files:
            print(
                f"Encoding check failed: {len(mojibake_files)} file(s) contain likely mojibake.",
                file=sys.stderr,
            )
            for file in mojibake_files:
                print(f"  {file}", file=sys.stderr)
        if crlf_files:
            print(
                f"Encoding check failed: {len(crlf_files)} tracked file(s) have CRLF or mixed line endings.",
                file=sys.stderr,
            )
            for file in crlf_files:
                print(f"  {file}", file=sys.stderr)
            print(
                "\nTip: normalize tracked line endings with `git add --renormalize .`.",
                file=sys.stderr,
            )
    return 1


def list_tracked_files(cwd: Path) -> list[str]:
    output = _git_stdout(["ls-files", "-z"], cwd)
    return [item for item in output.split("\0") if item]


def list_crlf_files(cwd: Path) -> list[str]:
    output = _git_stdout(["ls-files", "--eol", "-z"], cwd)
    crlf_files: list[str] = []
    for record in output.split("\0"):
        if not record:
            continue
        metadata, separator, path = record.partition("\t")
        if not separator:
            continue
        tokens = metadata.split()
        if not tokens or not tokens[0].startswith("i/"):
            continue
        index_eol = tokens[0][len("i/") :]
        if (
            index_eol in {"crlf", "mixed"}
            and "eol=crlf" not in metadata
            and "attr/-text" not in metadata
        ):
            crlf_files.append(path)
    return crlf_files


def _git_stdout(args: list[str], cwd: Path) -> str:
    result = run_git(args, cwd)
    if result is None:
        raise RuntimeError("git command failed before producing a result")
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            ["git", *args],
            output=result.stdout,
            stderr=result.stderr,
        )
    return result.stdout


def _has_source_extension(file: str) -> bool:
    suffix = Path(file).suffix
    return bool(suffix) and suffix[1:].lower() in SOURCE_EXTENSIONS


def _is_issue_file(file: str) -> bool:
    return Path(file).as_posix().startswith("docs/issues/")
