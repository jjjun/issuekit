"""Shared repository mojibake scanning."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from issuekit.encoding.detect import (
    confirmed_mojibake_hits,
    find_encoding_artifacts,
    is_encoding_excluded_path,
    line_number_at,
    newline_offsets,
)
from issuekit.gitutil import GitResult, GitStatusEntry, run_git

HitClass = Literal["confirmed", "unconfirmed"]
LineScope = Literal["whole-file", "changed-lines"]
SOURCE_EXTENSIONS = frozenset(
    {
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
)
BINARY_SNIFF_BYTES = 8_000


@dataclass(frozen=True)
class MojibakeScanOptions:
    """Named policy choices for one mojibake scan."""

    failure_classes: frozenset[HitClass]
    include_halfwidth_katakana: bool
    source_extensions: frozenset[str] | None
    line_scope: LineScope
    exclude_patterns: tuple[str, ...]
    excluded_hit_classes: frozenset[HitClass]


@dataclass(frozen=True)
class MojibakeScanResult:
    """Mojibake hits and the configured verdict."""

    confirmed_hits: tuple[dict[str, int | str], ...]
    unconfirmed_hits: tuple[dict[str, int | str], ...]
    failure_classes: frozenset[HitClass]

    @property
    def failed(self) -> bool:
        return (
            "confirmed" in self.failure_classes
            and bool(self.confirmed_hits)
        ) or (
            "unconfirmed" in self.failure_classes
            and bool(self.unconfirmed_hits)
        )


def scan_mojibake(
    repo: Path,
    paths: Sequence[Path],
    *,
    options: MojibakeScanOptions,
    changed_lines_by_path: Mapping[Path, Collection[int]] | None = None,
    whole_file_paths: Collection[Path] = (),
) -> MojibakeScanResult:
    """Scan caller-selected repository paths under an explicit policy."""

    confirmed_hits: list[dict[str, int | str]] = []
    unconfirmed_hits: list[dict[str, int | str]] = []
    whole_file_path_set = set(whole_file_paths)
    for rel_path in paths:
        file = rel_path.as_posix()
        if options.source_extensions is not None and not _has_source_extension(
            file, options.source_extensions
        ):
            continue
        excluded = is_encoding_excluded_path(file, options.exclude_patterns)
        if excluded and options.excluded_hit_classes == {"confirmed", "unconfirmed"}:
            continue
        try:
            content = (repo / rel_path).read_bytes()
            if (
                not _has_source_extension(file, SOURCE_EXTENSIONS)
                and b"\0" in content[:BINARY_SNIFF_BYTES]
            ):
                continue
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            if not excluded or "confirmed" not in options.excluded_hit_classes:
                confirmed_hits.append(_invalid_utf8_hit(file))
            continue
        except OSError:
            continue

        offsets = newline_offsets(text)
        artifacts = find_encoding_artifacts(
            text,
            include_halfwidth_katakana=options.include_halfwidth_katakana,
        )
        if (
            options.line_scope == "changed-lines"
            and changed_lines_by_path is not None
            and rel_path not in whole_file_path_set
        ):
            changed_lines = changed_lines_by_path.get(rel_path, ())
            artifacts = [
                (index, character)
                for index, character in artifacts
                if line_number_at(offsets, index) in changed_lines
            ]
        confirmed, unconfirmed = confirmed_mojibake_hits(
            file,
            text,
            artifacts,
            offsets=offsets,
        )
        if not excluded or "confirmed" not in options.excluded_hit_classes:
            confirmed_hits.extend(confirmed)
        if not excluded or "unconfirmed" not in options.excluded_hit_classes:
            unconfirmed_hits.extend(unconfirmed)

    return MojibakeScanResult(
        confirmed_hits=tuple(confirmed_hits),
        unconfirmed_hits=tuple(unconfirmed_hits),
        failure_classes=options.failure_classes,
    )


def changed_readable_paths(
    repo: Path,
    status_entries: Sequence[GitStatusEntry],
    *,
    excluded_root: Path,
    readable_paths: Collection[Path] | None = None,
) -> tuple[Path, ...]:
    """Select readable changed paths outside a submit-gate metadata tree."""

    readable_path_set = set(readable_paths) if readable_paths is not None else None
    paths: list[Path] = []
    for entry in status_entries:
        if not any(
            not _is_under_root(repo / path, excluded_root)
            for path in (entry.path, entry.original_path)
            if path is not None
        ):
            continue
        path = entry.path
        if readable_path_set is not None:
            if path not in readable_path_set:
                continue
        elif not _is_readable_regular_file(repo / path):
            continue
        paths.append(path)
    return tuple(paths)


def changed_line_numbers(
    repo: Path,
    rel_paths: tuple[Path, ...],
    *,
    git_runner: Callable[..., GitResult | None] = run_git,
) -> dict[Path, set[int]] | None:
    """Return added line numbers, or None when a full-file fallback is required."""

    if not rel_paths:
        return {}
    result = git_runner(
        [
            "-c",
            "core.quotepath=false",
            "--no-pager",
            "diff",
            "--unified=0",
            "HEAD",
            "--",
            *(rel_path.as_posix() for rel_path in rel_paths),
        ],
        repo,
    )
    if result is None or result.returncode != 0:
        return None
    return added_line_numbers(result.stdout)


def added_line_numbers(diff: str) -> dict[Path, set[int]]:
    """Parse added line numbers from a zero-context git diff."""

    changed_lines: dict[Path, set[int]] = {}
    rel_path: Path | None = None
    line_number: int | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            rel_path = None
            line_number = None
        elif line.startswith("+++ /dev/null"):
            rel_path = None
        elif line.startswith("+++ b/"):
            rel_path = Path(line[6:])
            changed_lines.setdefault(rel_path, set())
        elif line.startswith("@@"):
            plus_range = line.split(" ")[2]
            start = plus_range[1:].split(",", 1)[0]
            line_number = int(start)
        elif rel_path is not None and line_number is not None and line.startswith("+"):
            changed_lines[rel_path].add(line_number)
            line_number += 1
        elif line_number is not None and not line.startswith("-"):
            line_number += 1
    return changed_lines


def _invalid_utf8_hit(file: str) -> dict[str, int | str]:
    return {
        "file": file,
        "line": 1,
        "column": 1,
        "code_point": "invalid UTF-8",
        "context": "unable to decode file as UTF-8",
        "recovered": "not applicable",
    }


def _has_source_extension(file: str, source_extensions: Collection[str]) -> bool:
    suffix = Path(file).suffix
    return bool(suffix) and suffix[1:].lower() in source_extensions


def _is_under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _is_readable_regular_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("rb") as stream:
            stream.read(0)
    except OSError:
        return False
    return True
