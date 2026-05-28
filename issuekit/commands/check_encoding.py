"""Implementation of the check-encoding command."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from issuekit.core import has_mojibake


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


def run(args) -> int:
    tracked_files = list_tracked_files(Path.cwd())
    source_files = [file for file in tracked_files if _has_source_extension(file)]
    bom_files: list[str] = []
    mojibake_files: list[str] = []
    fixed_files: list[str] = []

    for file in source_files:
        path = Path(file)
        try:
            if _starts_with_bom(path):
                bom_files.append(file)
                if args.fix:
                    _strip_bom(path)
                    fixed_files.append(file)
            if not args.no_mojibake and has_mojibake(path.read_text(encoding="utf-8-sig", errors="ignore")):
                mojibake_files.append(file)
        except OSError:
            continue

    remaining_bom_files = [] if args.fix else bom_files
    payload = {
        "bom_files": remaining_bom_files,
        "mojibake_files": mojibake_files,
        "fixed": fixed_files,
    }
    if args.json:
        print(json.dumps(payload, indent=2))

    if not remaining_bom_files and not mojibake_files:
        if not args.json:
            for file in fixed_files:
                print(f"Fixed BOM: {file}")
            if fixed_files:
                print("Encoding check passed after fixing UTF-8 BOM files.")
            else:
                print("Encoding check passed: no UTF-8 BOM or likely mojibake in tracked source files.")
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
    return 1


def list_tracked_files(cwd: Path) -> list[str]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=cwd)
    return [item for item in output.decode("utf-8").split("\0") if item]


def _has_source_extension(file: str) -> bool:
    suffix = Path(file).suffix
    return bool(suffix) and suffix[1:].lower() in SOURCE_EXTENSIONS


def _starts_with_bom(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(3) == BOM


def _strip_bom(path: Path) -> None:
    content = path.read_bytes()
    if content.startswith(BOM):
        path.write_bytes(content[len(BOM) :])
