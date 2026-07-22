"""Implementation of the check-encoding command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from issuekit.config import load_config
from issuekit.core import (
    _confirmed_mojibake_hits,
    find_encoding_artifacts,
    is_encoding_excluded_path,
)
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
        "--no-halfwidth-kana",
        action="store_true",
        help="Allow half-width katakana in likely mojibake text scanning.",
    )
    check_encoding_parser.add_argument(
        "--show-unconfirmed-mojibake",
        action="store_true",
        help="Report likely mojibake candidates that fail CP932 reverse confirmation.",
    )
    check_encoding_parser.add_argument(
        "--fail-on-unconfirmed",
        action="store_true",
        help=(
            "Fail on unconfirmed mojibake candidates and report their locations. "
            "Legitimate Japanese can also match; exclude paths containing it."
        ),
    )
    check_encoding_parser.add_argument(
        "--no-crlf",
        action="store_true",
        help="Disable CRLF line-ending scanning.",
    )
    check_encoding_parser.add_argument(
        "--no-stray-cr",
        action="store_true",
        help="Disable stray carriage-return scanning in tracked source files.",
    )
    check_encoding_parser.add_argument(
        "--fix",
        action="store_true",
        help="Strip leading UTF-8 BOM bytes from tracked source files.",
    )
    check_encoding_parser.add_argument(
        "--changed",
        action="store_true",
        help=(
            "Scan only files changed in the working tree (or relative to --base) "
            "instead of every tracked file. Cuts the fixed per-run cost on "
            "no-change or small-change runs; use a full scan (the default) in CI."
        ),
    )
    check_encoding_parser.add_argument(
        "--base",
        help=(
            "With --changed, scan files that differ from this git ref (e.g. "
            "origin/main) instead of only uncommitted working-tree changes."
        ),
    )
    check_encoding_parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Exclude repo-relative paths matching this POSIX glob pattern.",
    )
    check_encoding_parser.set_defaults(func=run)


def run(args) -> int:
    cwd = Path.cwd()
    exclude_patterns = (*load_config(cwd).check_encoding_exclude, *args.exclude)
    changed = getattr(args, "changed", False)
    if changed:
        changed_files = list_changed_files(cwd, getattr(args, "base", None))
        source_files = [
            file
            for file in changed_files
            if _has_source_extension(file)
            and not _is_issue_file(file)
            and not is_encoding_excluded_path(file, exclude_patterns)
        ]
        crlf_paths: list[str] | None = [
            file
            for file in changed_files
            if not is_encoding_excluded_path(file, exclude_patterns)
        ]
    else:
        tracked_files = list_tracked_files(cwd)
        source_files = [
            file
            for file in tracked_files
            if _has_source_extension(file)
            and not _is_issue_file(file)
            and not is_encoding_excluded_path(file, exclude_patterns)
        ]
        crlf_paths = None

    bom_files: list[str] = []
    mojibake_files: list[str] = []
    mojibake_hits: list[dict[str, int | str]] = []
    unconfirmed_mojibake_hits: list[dict[str, int | str]] = []
    stray_cr_files: dict[str, list[int]] = {}
    fixed_files: list[str] = []
    crlf_files = [] if args.no_crlf else [
        file
        for file in list_crlf_files(cwd, paths=crlf_paths)
        if not is_encoding_excluded_path(file, exclude_patterns)
    ]

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
            if not args.no_mojibake:
                text = content.decode("utf-8", errors="ignore")
                artifacts = find_encoding_artifacts(
                    text,
                    include_halfwidth_katakana=not args.no_halfwidth_kana,
                )
                confirmed_hits, unconfirmed_hits = _confirmed_mojibake_hits(
                    file,
                    text,
                    artifacts,
                )
                if confirmed_hits:
                    mojibake_files.append(file)
                    mojibake_hits.extend(confirmed_hits)
                if args.show_unconfirmed_mojibake or args.fail_on_unconfirmed:
                    unconfirmed_mojibake_hits.extend(unconfirmed_hits)
            if not args.no_stray_cr:
                stray_cr_lines = _stray_carriage_return_lines(content)
                if stray_cr_lines:
                    stray_cr_files[file] = stray_cr_lines
        except OSError:
            continue

    remaining_bom_files = [] if args.fix else bom_files
    payload = {
        "bom_files": remaining_bom_files,
        "mojibake_files": mojibake_files,
        "mojibake_hits": mojibake_hits,
        "unconfirmed_mojibake_hits": unconfirmed_mojibake_hits,
        "stray_cr_files": list(stray_cr_files),
        "crlf_files": crlf_files,
        "fixed": fixed_files,
    }
    if args.json:
        print(json.dumps(payload, indent=2))

    if (
        not remaining_bom_files
        and not mojibake_files
        and (not args.fail_on_unconfirmed or not unconfirmed_mojibake_hits)
        and not stray_cr_files
        and not crlf_files
    ):
        if not args.json:
            for file in fixed_files:
                print(f"Fixed BOM: {file}")
            completed_checks = ["UTF-8 BOM"]
            if not args.no_mojibake:
                completed_checks.append("likely mojibake")
            if not args.no_stray_cr:
                completed_checks.append("stray carriage returns")
            if not args.no_crlf:
                completed_checks.append("CRLF")
            checks_text = _join_checks(completed_checks)
            if fixed_files:
                remaining_checks = completed_checks[1:]
                if remaining_checks:
                    print(
                        "Encoding check passed after fixing UTF-8 BOM files; "
                        f"no {_join_checks(remaining_checks)} found."
                    )
                else:
                    print("Encoding check passed after fixing UTF-8 BOM files.")
            else:
                print(f"Encoding check passed: no {checks_text} in tracked files.")
            _print_unconfirmed_mojibake_hits(unconfirmed_mojibake_hits)
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
            for hit in mojibake_hits:
                print(
                    f"  {hit['file']}:{hit['line']}:{hit['column']}: {hit['code_point']}",
                    file=sys.stderr,
                )
                print(f"    {hit['context']}", file=sys.stderr)
                print(f"    recovers to {hit['recovered']}", file=sys.stderr)
            print(
                "\nTip: use the reported location and code-point context to replace mojibake with the intended UTF-8 text.",
                file=sys.stderr,
            )
        if stray_cr_files:
            print(
                f"Encoding check failed: {len(stray_cr_files)} source file(s) contain stray carriage returns.",
                file=sys.stderr,
            )
            for file, lines in stray_cr_files.items():
                line_numbers = ", ".join(map(str, lines))
                print(f"  {file}: line(s) {line_numbers}", file=sys.stderr)
            print(
                "\nTip: locate carriage returns with `grep -nU $'\\r' <file>`.",
                file=sys.stderr,
            )
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
        _print_unconfirmed_mojibake_hits(
            unconfirmed_mojibake_hits,
            failed=args.fail_on_unconfirmed,
        )
    return 1


def list_tracked_files(cwd: Path) -> list[str]:
    output = _git_stdout(["ls-files", "-z"], cwd)
    return [item for item in output.split("\0") if item]


def list_changed_files(cwd: Path, base: str | None = None) -> list[str]:
    """Return source-tree paths that changed, for incremental scanning.

    With ``base`` set, this is everything differing from that ref (committed,
    staged, and unstaged). Otherwise it is uncommitted working-tree changes
    only. Untracked, non-ignored files are always included so newly added files
    are still scanned.
    """
    changed: set[str] = set()
    if base:
        changed |= _git_name_only(["diff", "--name-only", "-z", base], cwd)
    else:
        changed |= _git_name_only(["diff", "--name-only", "-z"], cwd)
        changed |= _git_name_only(["diff", "--name-only", "-z", "--cached"], cwd)
    changed |= _git_name_only(["ls-files", "--others", "--exclude-standard", "-z"], cwd)
    return sorted(changed)


def _git_name_only(args: list[str], cwd: Path) -> set[str]:
    output = _git_stdout(args, cwd)
    return {item for item in output.split("\0") if item}


def list_crlf_files(cwd: Path, paths: list[str] | None = None) -> list[str]:
    if paths is not None and not paths:
        return []
    ls_files_args = ["ls-files", "--eol", "-z"]
    if paths is not None:
        ls_files_args += ["--", *paths]
    output = _git_stdout(ls_files_args, cwd)
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
    try:
        result = run_git(args, cwd, strict=True)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise RuntimeError(
            "git command failed before producing a result "
            f"({type(exc).__name__}: {exc}; argv={_format_git_argv(args)})"
        ) from exc
    if result is None:
        raise RuntimeError(
            "git command failed before producing a result "
            f"(argv={_format_git_argv(args)})"
        )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            ["git", *args],
            output=result.stdout,
            stderr=result.stderr,
        )
    return result.stdout


def _format_git_argv(args: list[str]) -> str:
    argv = ["git", *args]
    total_length = sum(len(arg) for arg in argv)
    if len(argv) <= 8 and total_length <= 512:
        return repr(argv)
    preview = [arg if len(arg) <= 120 else f"{arg[:117]}..." for arg in argv[:6]]
    return (
        f"{preview!r} ... "
        f"({len(argv)} arguments, {total_length} characters total)"
    )


def _has_source_extension(file: str) -> bool:
    suffix = Path(file).suffix
    return bool(suffix) and suffix[1:].lower() in SOURCE_EXTENSIONS


def _is_issue_file(file: str) -> bool:
    return Path(file).as_posix().startswith("docs/issues/")


def _stray_carriage_return_lines(content: bytes) -> list[int]:
    if b"\r" not in content:
        return []
    return [
        content.count(b"\n", 0, index) + 1
        for index, byte in enumerate(content)
        if byte == ord("\r")
        and (index + 1 == len(content) or content[index + 1] != ord("\n"))
    ]


def _print_unconfirmed_mojibake_hits(
    hits: list[dict[str, int | str]],
    *,
    failed: bool = False,
) -> None:
    if not hits:
        return
    headline = (
        "Encoding check failed: "
        f"{len(hits)} unconfirmed mojibake candidate(s) with --fail-on-unconfirmed."
        if failed
        else (
            "Encoding audit: "
            f"{len(hits)} likely mojibake candidate(s) failed CP932 reverse confirmation."
        )
    )
    print(
        headline,
        file=sys.stderr,
    )
    for hit in hits:
        print(
            f"  {hit['file']}:{hit['line']}:{hit['column']}: {hit['code_point']}",
            file=sys.stderr,
        )
        print(f"    {hit['context']}", file=sys.stderr)


def _code_point_context(text: str, index: int, character: str) -> str:
    before = text[max(0, index - 5) : index]
    after = text[index + 1 : index + 6]
    return " ".join(
        [
            "...",
            *(_code_point(value) for value in before),
            f"[{_code_point(character)}]",
            *(_code_point(value) for value in after),
            "...",
        ]
    )


def _code_point(character: str) -> str:
    return f"U+{ord(character):04X}"


def _join_checks(checks: list[str]) -> str:
    if len(checks) == 1:
        return checks[0]
    if len(checks) == 2:
        return " or ".join(checks)
    return ", ".join(checks[:-1]) + f", or {checks[-1]}"
