from pathlib import Path

import issuekit.agents.run_claimed as run_claimed

from issuekit import encoding
from issuekit.commands.check_encoding import _stray_carriage_return_lines
from issuekit.gitutil import GitResult


def test_encoding_artifact_detection() -> None:
    assert encoding.find_encoding_artifacts("\u0080")
    assert encoding.find_encoding_artifacts("\u8389")
    assert encoding.find_encoding_artifacts("\u8711")
    assert encoding.find_encoding_artifacts("\u8700")
    assert encoding.find_encoding_artifacts("\ue000")
    assert encoding.find_encoding_artifacts("\uff71")
    assert not encoding.find_encoding_artifacts("\uff71", include_halfwidth_katakana=False)
    assert not encoding.find_encoding_artifacts("plain ascii")


def test_sanitize_to_ascii_folds_punctuation_and_compatibility_forms() -> None:
    assert encoding.sanitize_to_ascii("\u201cquoted\u201d \u2014 caf\u00e9") == '"quoted" - cafe'
    assert encoding.sanitize_to_ascii("\uff26\uff55\uff4c\uff4c\uff57\uff49\uff44\uff54\uff48") == "Fullwidth"


def test_sanitize_to_ascii_drops_unfoldable_text_and_preserves_ascii() -> None:
    assert encoding.sanitize_to_ascii("\u76f4\u3057\u3066") == ""
    assert encoding.sanitize_to_ascii("plain ASCII\nunchanged") == "plain ASCII\nunchanged"


def test_encoding_artifact_reverted_generated_file_exclusions() -> None:
    """Keep issuekit#229's restoration of previously excluded mojibake characters."""
    assert encoding.find_encoding_artifacts("\u83f4")
    assert encoding.find_encoding_artifacts("\u873f")
    assert encoding.find_encoding_artifacts("\u9015")
    assert encoding.find_encoding_artifacts("\u95ad")


def test_encoding_scan_maps_many_artifacts_and_stray_carriage_returns() -> None:
    text = "".join(f"line {index} \u7e67\uff62\u7e5d\u4e5d\u0393\n" for index in range(1, 501))
    artifacts = encoding.find_encoding_artifacts(text)

    confirmed, unconfirmed = encoding.confirmed_mojibake_hits("many.txt", text, artifacts)

    assert not unconfirmed
    assert [(hit["line"], hit["column"]) for hit in confirmed] == [
        (index, len(f"line {index} ") + 1) for index in range(1, 501)
    ]
    assert _stray_carriage_return_lines(b"\r" * 500 + b"\nend\r") == [1] * 499 + [2]


def test_mojibake_gate_batches_changed_line_and_tracked_path_queries(
    tmp_path, monkeypatch
) -> None:
    paths = (Path("changed.py"), Path("unchanged.py"), Path("new.py"))
    for path in paths:
        (tmp_path / path).write_text("value = '\u7e67\uff62\u7e5d\u4e5d\u0393'\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run_git(args, cwd):
        calls.append(list(args))
        if args[3] == "diff":
            return GitResult(
                returncode=0,
                stdout="+++ b/changed.py\n@@ -0,0 +1 @@\n+value = 'changed'\n",
                stderr="",
            )
        return GitResult(returncode=0, stdout="changed.py\0unchanged.py\0", stderr="")

    monkeypatch.setattr(run_claimed, "_touched_implementation_paths", lambda *args: paths)
    monkeypatch.setattr(run_claimed, "run_git", fake_run_git)

    confirmed, unconfirmed = run_claimed._mojibake_touched_hits(
        tmp_path,
        tmp_path / ".issues",
        include_halfwidth_katakana=True,
        exclude_patterns=(),
    )

    assert not unconfirmed
    assert [hit["file"] for hit in confirmed] == ["changed.py", "new.py"]
    assert calls == [
        [
            "-c",
            "core.quotepath=false",
            "--no-pager",
            "diff",
            "--unified=0",
            "HEAD",
            "--",
            "changed.py",
            "unchanged.py",
            "new.py",
        ],
        ["ls-files", "-z", "--", "changed.py", "unchanged.py", "new.py"],
    ]


def test_added_line_numbers_ignores_deleted_file_headers() -> None:
    diff = (
        "diff --git a/a.txt b/a.txt\n"
        "index 111..222 100644\n"
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -0,0 +1,1 @@\n"
        "+hello\n"
        "diff --git a/b.txt b/b.txt\n"
        "deleted file mode 100644\n"
        "index 333..000\n"
        "--- a/b.txt\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-x\n"
        "-y\n"
    )

    assert run_claimed._added_line_numbers(diff) == {Path("a.txt"): {1}}


def test_touched_paths_uses_raw_non_ascii_status_paths(tmp_path, monkeypatch) -> None:
    calls: list[tuple[Path, bool, str]] = []

    def fake_git_status_short(cwd, *, strip, untracked_files):
        calls.append((cwd, strip, untracked_files))
        return " M 日本語.py\n"

    monkeypatch.setattr(run_claimed, "git_root", lambda repo: tmp_path.resolve())
    monkeypatch.setattr(run_claimed, "git_status_short", fake_git_status_short)

    assert run_claimed._touched_paths(tmp_path) == (Path("日本語.py"),)
    assert calls == [(tmp_path, False, "all")]
