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
        if args[1] == "diff":
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
