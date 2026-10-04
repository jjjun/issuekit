"""Implementation of the check-encoding command."""

from __future__ import annotations

import argparse
import subprocess
import sys
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from issuekit.commands._common import add_json_flag, print_json
from issuekit.config import load_config, resolve_repository_root
from issuekit.encoding import (
    SOURCE_EXTENSIONS,
    MojibakeScanOptions,
    changed_line_numbers,
    changed_readable_paths,
    has_source_extension,
    is_encoding_excluded_path,
    print_mojibake_hit,
    scan_mojibake,
)
from issuekit.gitutil import git_status_entries, run_git

BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class ScanTargets:
    gate: bool
    exclude_patterns: tuple[str, ...]
    scan_paths: tuple[Path, ...]
    source_files: list[str]
    crlf_paths: list[str] | None
    changed_lines_by_path: dict[Path, set[int]] | None
    whole_file_paths: set[Path]


@dataclass
class ScanResults:
    bom_files: list[str]
    mojibake_files: list[str]
    mojibake_hits: list[dict[str, int | str]]
    unconfirmed_mojibake_hits: list[dict[str, int | str]]
    stray_cr_files: dict[str, list[int]]
    fixed_files: list[str]
    mojibake_failed: bool
    crlf_files: list[str]


def register(subparsers: argparse._SubParsersAction) -> None:
    check_encoding_parser = subparsers.add_parser(
        "check-encoding",
        help="Check tracked files for encoding problems.",
    )
    add_json_flag(check_encoding_parser)
    check_encoding_parser.add_argument(
        "--gate",
        action="store_true",
        help="Reproduce the submit gate's mojibake verdict for the current worktree.",
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
        type=_validate_base_revision,
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
    repo_root = resolve_repository_root(Path.cwd())
    config = load_config(repo_root)
    gate = getattr(args, "gate", False)
    if gate and _gate_incompatible_options(args):
        print(
            "check-encoding --gate cannot be combined with encoding-scan modifiers.",
            file=sys.stderr,
        )
        return 2

    targets = _select_scan_targets(args, config, repo_root)
    results = _scan_bom_and_stray_cr(args, repo_root, targets)
    _scan_mojibake(args, config, repo_root, targets, results)

    remaining_bom_files = [] if args.fix else results.bom_files
    payload = {
        "bom_files": remaining_bom_files,
        "mojibake_files": results.mojibake_files,
        "mojibake_hits": results.mojibake_hits,
        "unconfirmed_mojibake_hits": results.unconfirmed_mojibake_hits,
        "stray_cr_files": list(results.stray_cr_files),
        "crlf_files": results.crlf_files,
        "fixed": results.fixed_files,
    }
    if args.json:
        print_json(payload)

    if (
        not remaining_bom_files
        and not results.mojibake_failed
        and not results.stray_cr_files
        and not results.crlf_files
    ):
        if not args.json:
            _print_success(args, results)
        return 0

    if not args.json:
        _print_failures(args, results, targets, remaining_bom_files)
    return 1


def _select_scan_targets(args, config, repo_root: Path) -> ScanTargets:
    gate = getattr(args, "gate", False)
    exclude_patterns = (*config.check_encoding_exclude, *args.exclude)
    changed = getattr(args, "changed", False)
    changed_lines_by_path: dict[Path, set[int]] | None = None
    whole_file_paths: set[Path] = set()
    if gate:
        entries = git_status_entries(repo_root) or ()
        scan_paths = changed_readable_paths(
            repo_root,
            entries,
        )
        changed_lines_by_path = changed_line_numbers(repo_root, scan_paths)
        whole_file_paths = {
            entry.path for entry in entries if entry.status == "??"
        }
        source_files: list[str] = []
        crlf_paths: list[str] | None = []
    elif changed:
        changed_files = list_changed_files(repo_root, getattr(args, "base", None))
        scan_paths = tuple(Path(file) for file in changed_files)
        source_files = [
            file
            for file in changed_files
            if has_source_extension(file, SOURCE_EXTENSIONS)
            and not is_encoding_excluded_path(file, exclude_patterns)
        ]
        crlf_paths: list[str] | None = [
            file
            for file in changed_files
            if not is_encoding_excluded_path(file, exclude_patterns)
        ]
    else:
        tracked_files = list_tracked_files(repo_root)
        scan_paths = tuple(Path(file) for file in tracked_files)
        source_files = [
            file
            for file in tracked_files
            if has_source_extension(file, SOURCE_EXTENSIONS)
            and not is_encoding_excluded_path(file, exclude_patterns)
        ]
        crlf_paths = None
    return ScanTargets(
        gate=gate,
        exclude_patterns=exclude_patterns,
        scan_paths=tuple(scan_paths),
        source_files=source_files,
        crlf_paths=crlf_paths,
        changed_lines_by_path=changed_lines_by_path,
        whole_file_paths=whole_file_paths,
    )


def _scan_bom_and_stray_cr(
    args, repo_root: Path, targets: ScanTargets
) -> ScanResults:
    bom_files: list[str] = []
    stray_cr_files: dict[str, list[int]] = {}
    fixed_files: list[str] = []
    crlf_files = [] if args.no_crlf else [
        file
        for file in list_crlf_files(repo_root, paths=targets.crlf_paths)
        if not is_encoding_excluded_path(file, targets.exclude_patterns)
    ]

    for file in targets.source_files:
        path = repo_root / file
        try:
            content = path.read_bytes()
            has_bom = content.startswith(BOM)
            if has_bom:
                bom_files.append(file)
                if args.fix:
                    path.write_bytes(content[len(BOM) :])
                    content = content[len(BOM) :]
                    fixed_files.append(file)
            if not args.no_stray_cr:
                stray_cr_lines = _stray_carriage_return_lines(content)
                if stray_cr_lines:
                    stray_cr_files[file] = stray_cr_lines
        except OSError:
            continue

    return ScanResults(
        bom_files=bom_files,
        mojibake_files=[],
        mojibake_hits=[],
        unconfirmed_mojibake_hits=[],
        stray_cr_files=stray_cr_files,
        fixed_files=fixed_files,
        mojibake_failed=False,
        crlf_files=crlf_files,
    )


def _scan_mojibake(
    args,
    config,
    repo_root: Path,
    targets: ScanTargets,
    results: ScanResults,
) -> None:
    if not args.no_mojibake:
        failure_classes = {"confirmed"}
        if targets.gate or args.fail_on_unconfirmed:
            failure_classes.add("unconfirmed")
        scan_result = scan_mojibake(
            repo_root,
            targets.scan_paths,
            options=MojibakeScanOptions(
                failure_classes=frozenset(failure_classes),
                include_halfwidth_katakana=(
                    config.gate_halfwidth_kana
                    if targets.gate
                    else not args.no_halfwidth_kana
                ),
                source_extensions=None if targets.gate else SOURCE_EXTENSIONS,
                line_scope="changed-lines" if targets.gate else "whole-file",
                exclude_patterns=targets.exclude_patterns,
                excluded_hit_classes=frozenset({"unconfirmed"}),
            ),
            changed_lines_by_path=targets.changed_lines_by_path,
            whole_file_paths=targets.whole_file_paths,
        )
        results.mojibake_hits = list(scan_result.confirmed_hits)
        results.mojibake_failed = scan_result.failed
        results.mojibake_files = list(
            dict.fromkeys(str(hit["file"]) for hit in results.mojibake_hits)
        )
        if targets.gate or args.show_unconfirmed_mojibake or args.fail_on_unconfirmed:
            results.unconfirmed_mojibake_hits = list(scan_result.unconfirmed_hits)


def _print_success(args, results: ScanResults) -> None:
    if getattr(args, "gate", False):
        print("Encoding submit gate passed: no mojibake in changed lines.")
        return
    for file in results.fixed_files:
        print(f"Fixed BOM: {file}")
    completed_checks = ["UTF-8 BOM"]
    if not args.no_mojibake:
        completed_checks.append("likely mojibake")
    if not args.no_stray_cr:
        completed_checks.append("stray carriage returns")
    if not args.no_crlf:
        completed_checks.append("CRLF")
    checks_text = _join_checks(completed_checks)
    if results.fixed_files:
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
    _print_unconfirmed_mojibake_hits(results.unconfirmed_mojibake_hits)


def _print_failures(
    args,
    results: ScanResults,
    targets: ScanTargets,
    remaining_bom_files: list[str],
) -> None:
    for file in results.fixed_files:
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
    if results.mojibake_files:
        print(
            f"Encoding check failed: {len(results.mojibake_files)} file(s) contain likely mojibake.",
            file=sys.stderr,
        )
        for hit in results.mojibake_hits:
            print_mojibake_hit(
                hit,
                sys.stderr,
                prefix="  ",
                context_prefix="    ",
            )
            print(f"    recovers to {hit['recovered']}", file=sys.stderr)
        excluded_mojibake_files = {
            str(hit["file"])
            for hit in results.mojibake_hits
            if is_encoding_excluded_path(str(hit["file"]), targets.exclude_patterns)
        }
        if excluded_mojibake_files:
            print(
                "check_encoding_exclude matches "
                f"{len(excluded_mojibake_files)} of these path(s); exclusions "
                "suppress unconfirmed candidates only, so confirmed mojibake "
                "is still reported.",
                file=sys.stderr,
            )
        print(
            "\nTip: use the reported location and code-point context to replace mojibake with the intended UTF-8 text.",
            file=sys.stderr,
        )
    if results.stray_cr_files:
        print(
            f"Encoding check failed: {len(results.stray_cr_files)} source file(s) contain stray carriage returns.",
            file=sys.stderr,
        )
        for file, lines in results.stray_cr_files.items():
            line_numbers = ", ".join(map(str, lines))
            print(f"  {file}: line(s) {line_numbers}", file=sys.stderr)
        print(
            "\nTip: locate carriage returns with `grep -nU $'\\r' <file>`.",
            file=sys.stderr,
        )
    if results.crlf_files:
        print(
            f"Encoding check failed: {len(results.crlf_files)} tracked file(s) have CRLF or mixed line endings.",
            file=sys.stderr,
        )
        for file in results.crlf_files:
            print(f"  {file}", file=sys.stderr)
        print(
            "\nTip: normalize tracked line endings with `git add --renormalize .`.",
            file=sys.stderr,
        )
    _print_unconfirmed_mojibake_hits(
        results.unconfirmed_mojibake_hits,
        failed=targets.gate or args.fail_on_unconfirmed,
        gate=targets.gate,
    )


def _gate_incompatible_options(args) -> bool:
    return any(
        (
            args.no_mojibake,
            args.no_halfwidth_kana,
            args.show_unconfirmed_mojibake,
            args.fail_on_unconfirmed,
            args.no_crlf,
            args.no_stray_cr,
            args.fix,
            args.changed,
            args.base is not None,
            bool(args.exclude),
        )
    )


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
        changed |= _git_name_only(
            [
                "diff",
                "--name-only",
                "-z",
                "--no-ext-diff",
                "--no-textconv",
                "--end-of-options",
                base,
            ],
            cwd,
        )
    else:
        changed |= _git_name_only(
            ["diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv"],
            cwd,
        )
        changed |= _git_name_only(
            [
                "diff",
                "--name-only",
                "-z",
                "--cached",
                "--no-ext-diff",
                "--no-textconv",
            ],
            cwd,
        )
    changed |= _git_name_only(["ls-files", "--others", "--exclude-standard", "-z"], cwd)
    return sorted(changed)


def _git_name_only(args: list[str], cwd: Path) -> set[str]:
    output = _git_stdout(args, cwd)
    return {item for item in output.split("\0") if item}


def _validate_base_revision(value: str) -> str:
    if value.startswith("-"):
        raise argparse.ArgumentTypeError(
            "--base must be a git revision, not an option"
        )
    return value


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


def _stray_carriage_return_lines(content: bytes) -> list[int]:
    if content.find(b"\r") == -1:
        return []

    newline_offsets: list[int] = []
    offset = content.find(b"\n")
    while offset != -1:
        newline_offsets.append(offset)
        offset = content.find(b"\n", offset + 1)

    lines: list[int] = []
    offset = content.find(b"\r")
    while offset != -1:
        if offset + 1 == len(content) or content[offset + 1] != ord("\n"):
            lines.append(bisect_right(newline_offsets, offset) + 1)
        offset = content.find(b"\r", offset + 1)
    return lines


def _print_unconfirmed_mojibake_hits(
    hits: list[dict[str, int | str]],
    *,
    failed: bool = False,
    gate: bool = False,
) -> None:
    if not hits:
        return
    if gate:
        headline = (
            "Encoding submit gate failed: "
            f"{len(hits)} unconfirmed mojibake candidate(s)."
        )
    elif failed:
        headline = (
            "Encoding check failed: "
            f"{len(hits)} unconfirmed mojibake candidate(s) with --fail-on-unconfirmed."
        )
    else:
        headline = (
            "Encoding audit: "
            f"{len(hits)} likely mojibake candidate(s) failed CP932 reverse confirmation."
        )
    print(
        headline,
        file=sys.stderr,
    )
    for hit in hits:
        print_mojibake_hit(
            hit,
            sys.stderr,
            prefix="  ",
            context_prefix="    ",
        )


def _join_checks(checks: list[str]) -> str:
    if len(checks) == 1:
        return checks[0]
    if len(checks) == 2:
        return " or ".join(checks)
    return ", ".join(checks[:-1]) + f", or {checks[-1]}"
