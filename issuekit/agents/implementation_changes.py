"""Snapshot and inspect implementation changes made in an agent run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from issuekit.agents.readonly import worktree_fingerprint
from issuekit.config import IssuekitConfig
from issuekit.encoding import (
    MojibakeScanOptions,
    changed_line_numbers,
    changed_readable_paths,
    is_encoding_excluded_path,
    print_mojibake_hit,
    scan_mojibake,
)
from issuekit.gitutil import GitStatusEntry, git_root, git_status_entries, run_git
from issuekit.paths import is_readable_regular_file


@dataclass(frozen=True)
class ImplementationChangeSnapshot:
    """Agent-attributable repository changes captured after an implementation run."""

    root: Path | None
    status_entries: tuple[GitStatusEntry, ...] | None
    changed_paths: tuple[Path, ...]
    readable_paths: tuple[Path, ...]
    all_status_entries: tuple[GitStatusEntry, ...] | None = None


def mojibake_touched_hits(
    snapshot: ImplementationChangeSnapshot,
    repo: Path,
    *,
    include_halfwidth_katakana: bool,
    exclude_patterns: tuple[str, ...],
) -> tuple[list[dict[str, int | str]], list[dict[str, int | str]]]:
    paths = changed_readable_paths(
        repo,
        snapshot.status_entries or (),
        readable_paths=snapshot.readable_paths,
    )
    changed_lines_by_path = changed_line_numbers(repo, paths, git_runner=run_git)
    untracked_paths = {
        entry.path
        for entry in snapshot.status_entries or ()
        if entry.status == "??"
    }
    result = scan_mojibake(
        repo,
        paths,
        options=MojibakeScanOptions(
            failure_classes=frozenset({"confirmed", "unconfirmed"}),
            include_halfwidth_katakana=include_halfwidth_katakana,
            source_extensions=None,
            line_scope="changed-lines",
            exclude_patterns=exclude_patterns,
            # Exclusions suppress false positives from known-legitimate text;
            # confirmed reversible corruption still blocks every path.
            excluded_hit_classes=frozenset({"unconfirmed"}),
        ),
        changed_lines_by_path=changed_lines_by_path,
        whole_file_paths=untracked_paths,
    )
    return list(result.confirmed_hits), list(result.unconfirmed_hits)


def report_mojibake_gate_failure(
    confirmed: list[dict[str, int | str]],
    unconfirmed: list[dict[str, int | str]],
    config: IssuekitConfig,
    err: TextIO,
) -> None:
    print(
        "ERROR: mojibake gate blocked submit_for_review. "
        "Fix the following changed lines before submitting:",
        file=err,
    )
    for hit in confirmed:
        print_mojibake_hit(hit, err, prefix="- ", context_prefix="  ")
        print(f"  recovers to {hit['recovered']}", file=err)
    for hit in unconfirmed:
        print_mojibake_hit(hit, err, prefix="- ", context_prefix="  ")
        print("  failed CP932 reverse confirmation", file=err)
    excluded_confirmed_paths = {
        str(hit["file"])
        for hit in confirmed
        if is_encoding_excluded_path(str(hit["file"]), config.check_encoding_exclude)
    }
    if excluded_confirmed_paths:
        print(
            "check_encoding_exclude matches "
            f"{len(excluded_confirmed_paths)} of these path(s); exclusions "
            "suppress unconfirmed candidates only, so confirmed mojibake "
            "is still reported.",
            file=err,
        )
    print(
        "Reproduce this gate locally with "
        "`uv run issuekit check-encoding --gate`.",
        file=err,
    )
    if unconfirmed:
        print(
            "To allow known-legitimate unconfirmed text, add its "
            "repo-relative path to check_encoding_exclude.",
            file=err,
        )


def warn_heavy_deletions(
    snapshot: ImplementationChangeSnapshot,
    repo: Path,
    *,
    deletion_threshold: int,
    err: TextIO,
) -> None:
    if snapshot.root != repo.resolve():
        return
    entries = implementation_entries(snapshot)
    if not entries:
        return
    result = run_git(
        [
            "--no-pager",
            "diff",
            "--numstat",
            "-z",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        ],
        repo,
    )
    if result is None:
        return
    if result.returncode != 0:
        return

    for _added, deleted, paths in _numstat_records(result.stdout):
        if not any(
            entry.path in paths or entry.original_path in paths
            for entry in entries
        ):
            continue
        if not deleted.isdigit() or int(deleted) <= deletion_threshold:
            continue
        rel_path = paths[-1]
        print(
            "WARNING: heavy deletion diff detected: "
            f"{rel_path.as_posix()} deletes {deleted} lines "
            f"(threshold {deletion_threshold}).",
            file=err,
        )


def _numstat_records(output: str) -> tuple[tuple[str, str, tuple[Path, ...]], ...]:
    fields = output.split("\0")
    if fields and not fields[-1]:
        fields.pop()
    records: list[tuple[str, str, tuple[Path, ...]]] = []
    index = 0
    while index < len(fields):
        parts = fields[index].split("\t", 2)
        if len(parts) != 3:
            break
        added, deleted, raw_path = parts
        if raw_path:
            paths = (Path(raw_path),)
            index += 1
        else:
            if index + 2 >= len(fields):
                break
            paths = (Path(fields[index + 1]), Path(fields[index + 2]))
            index += 3
        records.append((added, deleted, paths))
    return tuple(records)


def implementation_change_snapshot(
    repo: Path,
    fingerprint_before: tuple[tuple[str, str, str, str], ...] | None = None,
) -> ImplementationChangeSnapshot:
    root = git_root(repo)
    all_status_entries = git_status_entries(repo) if root == repo.resolve() else None
    fingerprint_after = (
        worktree_fingerprint(repo) if all_status_entries is not None else None
    )
    entries = _attributable_entries(all_status_entries, fingerprint_before, fingerprint_after)
    changed_paths: list[Path] = []
    readable_paths: list[Path] = []
    seen_changed: set[Path] = set()
    seen_readable: set[Path] = set()
    for entry in entries or ():
        for path in (entry.path, entry.original_path):
            if path is not None and path not in seen_changed:
                seen_changed.add(path)
                changed_paths.append(path)
        current = repo / entry.path
        if entry.path not in seen_readable and is_readable_regular_file(current):
            seen_readable.add(entry.path)
            readable_paths.append(entry.path)
    return ImplementationChangeSnapshot(
        root=root,
        status_entries=entries,
        all_status_entries=all_status_entries,
        changed_paths=tuple(changed_paths),
        readable_paths=tuple(readable_paths),
    )


def _attributable_entries(
    entries: tuple[GitStatusEntry, ...] | None,
    fingerprint_before: tuple[tuple[str, str, str, str], ...] | None,
    fingerprint_after: tuple[tuple[str, str, str, str], ...] | None,
) -> tuple[GitStatusEntry, ...] | None:
    if entries is None or fingerprint_before is None or fingerprint_after is None:
        return entries
    before_by_path = {entry[1]: entry for entry in fingerprint_before}
    after_by_path = {entry[1]: entry for entry in fingerprint_after}
    return tuple(
        entry
        for entry in entries
        if (after_entry := after_by_path.get(entry.path.as_posix())) is not None
        and (
            (before_entry := before_by_path.get(entry.path.as_posix())) is None
            or before_entry[3] != after_entry[3]
        )
    )


def implementation_entries(
    snapshot: ImplementationChangeSnapshot,
) -> tuple[GitStatusEntry, ...]:
    return snapshot.status_entries or ()


def all_implementation_entries(
    snapshot: ImplementationChangeSnapshot,
) -> tuple[GitStatusEntry, ...]:
    """All status entries, regardless of which run produced them."""

    return snapshot.all_status_entries or ()
