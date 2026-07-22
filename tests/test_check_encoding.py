import json
from pathlib import Path
import subprocess

import pytest

from issuekit import cli
from issuekit.commands import check_encoding


MOJIBAKE = "\u7e67\uff62\u7e5d\u4e5d\u0393"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (OSError(206, "The filename or extension is too long"), "OSError"),
        (subprocess.TimeoutExpired(["git", "ls-files"], 30), "TimeoutExpired"),
    ],
)
def test_git_stdout_reports_git_failure_cause(
    tmp_path: Path,
    monkeypatch,
    failure: BaseException,
    expected: str,
) -> None:
    def fail_run_git(args, cwd, *, timeout=30, strict=False):
        assert strict is True
        raise failure

    monkeypatch.setattr(check_encoding, "run_git", fail_run_git)

    with pytest.raises(RuntimeError) as raised:
        check_encoding._git_stdout(["ls-files", "-z"], tmp_path)

    assert expected in str(raised.value)
    assert str(failure) in str(raised.value)
    assert "argv=['git', 'ls-files', '-z']" in str(raised.value)
    assert raised.value.__cause__ is failure


def test_format_git_argv_summarizes_large_commands() -> None:
    large_pathspec = "a" * 1_000

    formatted = check_encoding._format_git_argv(["ls-files", "--", large_pathspec])

    assert large_pathspec not in formatted
    assert "4 arguments" in formatted
    assert "1013 characters total" in formatted


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=path, check=True)


def add_tracked(path: Path, name: str, content: bytes) -> None:
    file_path = path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)
    subprocess.run(["git", "add", name], cwd=path, check=True)


def commit_all(path: Path, message: str = "commit") -> str:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=path,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_check_encoding_clean_tree_passes(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "clean.py", b"print('ok')\n")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding"])

    assert exit_code == 0
    assert "Encoding check passed" in capsys.readouterr().out


def test_check_encoding_full_scan_uses_no_crlf_pathspec(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "clean.py", b"print('ok')\n")
    crlf_paths: list[list[str] | None] = []
    original_list_crlf_files = check_encoding.list_crlf_files

    def record_crlf_paths(cwd: Path, paths: list[str] | None = None) -> list[str]:
        crlf_paths.append(paths)
        return original_list_crlf_files(cwd, paths)

    monkeypatch.setattr(check_encoding, "list_crlf_files", record_crlf_paths)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 0
    assert crlf_paths == [None]


def test_check_encoding_bom_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bom.py", b"\xef\xbb\xbfprint('bad')\n")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding"])

    assert exit_code == 1
    assert "bom.py" in capsys.readouterr().err


def test_check_encoding_mojibake_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bad.md", f"first\nprefix {MOJIBAKE} suffix\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "bad.md:2:8: U+7E67" in captured.err
    assert "[U+7E67]" in captured.err
    assert "\u7e67" not in captured.err
    assert "recovers to U+30A2 U+30CB U+30E1" in captured.err


def test_check_encoding_drops_unconfirmed_legitimate_japanese(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "clean.md", "\u95be\u5024\n\uff71\uff86\uff92\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 0


def test_check_encoding_audits_unconfirmed_mojibake_candidates(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "clean.md", "\u87f2\u5e2b\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding", "--show-unconfirmed-mojibake"]) == 0
    captured = capsys.readouterr()
    assert "Encoding audit" in captured.err
    assert "U+87F2" in captured.err
    assert "Encoding check failed" not in captured.err


def test_check_encoding_fails_on_unconfirmed_mojibake_candidates(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "clean.md", "\u87f2\u5e2b\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 0
    assert cli.main(["check-encoding", "--fail-on-unconfirmed"]) == 1
    captured = capsys.readouterr()
    assert "Encoding check failed: 1 unconfirmed mojibake candidate(s) with --fail-on-unconfirmed." in captured.err
    assert "Encoding audit" not in captured.err
    assert "clean.md:1:1: U+87F2" in captured.err


def test_check_encoding_clean_tree_passes_with_fail_on_unconfirmed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "clean.py", b"print('ok')\n")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding", "--fail-on-unconfirmed"]) == 0


def test_check_encoding_confirmed_mojibake_fails_with_fail_on_unconfirmed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bad.md", f"{MOJIBAKE}\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding", "--fail-on-unconfirmed"]) == 1


def test_check_encoding_confirms_single_kanji_recovery_with_c1_control(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bad.md", "\u87bb\u0080\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 1
    assert "recovers to U+5C40" in capsys.readouterr().err


def test_check_encoding_excludes_configured_generated_paths(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "generated/openapi.ts", b"\xef\xbb\xbf\xef\xbd\xb1\r\n")
    (tmp_path / "issuekit.toml").write_text(
        "check_encoding_exclude = ['generated/**']\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 0


def test_check_encoding_exclude_keeps_other_paths_checked(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "generated/openapi.ts", f"{MOJIBAKE}\n".encode("utf-8"))
    add_tracked(tmp_path, "source/bad.md", f"{MOJIBAKE}\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding", "--exclude", "generated/**"])

    assert exit_code == 1
    assert "source/bad.md" in capsys.readouterr().err


def test_check_encoding_default_does_not_exclude_generated_paths(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "generated/openapi.ts", f"{MOJIBAKE}\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 1


def test_check_encoding_ignores_non_source_extensions(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "image.bin", b"\xef\xbb\xbf")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 0


def test_check_encoding_json_shape(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bom.py", b"\xef\xbb\xbfprint('bad')\n")
    add_tracked(tmp_path, "bad.md", f"{MOJIBAKE}\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload == {
        "bom_files": ["bom.py"],
        "mojibake_files": ["bad.md"],
        "mojibake_hits": [
            {
                "file": "bad.md",
                "line": 1,
                "column": 1,
                "code_point": "U+7E67",
                "context": "... [U+7E67] U+FF62 U+7E5D U+4E5D U+0393 U+000A ...",
                "recovered": "U+30A2 U+30CB U+30E1",
            }
        ],
        "unconfirmed_mojibake_hits": [],
        "stray_cr_files": [],
        "crlf_files": [],
        "fixed": [],
    }


def test_check_encoding_no_mojibake_toggle(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bad.md", f"{MOJIBAKE}\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding", "--no-mojibake"]) == 0


def test_check_encoding_allows_legitimate_halfwidth_katakana(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bad.md", "\uff71\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 0
    assert cli.main(["check-encoding", "--no-halfwidth-kana"]) == 0


def test_check_encoding_additional_encoding_artifacts_fail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bad.md", f"{MOJIBAKE}\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 1


def test_check_encoding_crlf_blob_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bad.txt", b"one\r\ntwo\r\n")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "bad.txt" in captured.err
    assert "git add --renormalize ." in captured.err


def test_check_encoding_crlf_json_reports_files(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bad.txt", b"one\r\ntwo\r\n")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["crlf_files"] == ["bad.txt"]


def test_check_encoding_respects_eol_crlf_attribute(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, ".gitattributes", b"*.bat text eol=crlf\n")
    add_tracked(tmp_path, "script.bat", b"@echo off\r\n")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 0


def test_check_encoding_ignores_binary_files_for_crlf(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, ".gitattributes", b"*.bin -text\n")
    add_tracked(tmp_path, "image.bin", b"one\r\ntwo\r\n")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 0


def test_check_encoding_mixed_endings_fail(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "mixed.txt", b"one\ntwo\r\n")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding"])

    assert exit_code == 1
    assert "mixed.txt" in capsys.readouterr().err


def test_check_encoding_no_crlf_toggle_keeps_other_checks(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "crlf.txt", b"one\r\ntwo\r\n")
    add_tracked(tmp_path, "bom.py", b"\xef\xbb\xbfprint('bad')\n")
    add_tracked(tmp_path, "bad.md", f"{MOJIBAKE}\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding", "--no-crlf"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "bom.py" in captured.err
    assert "bad.md" in captured.err
    assert "crlf.txt" not in captured.err


def test_check_encoding_no_stray_cr_toggle_keeps_other_checks(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "stray.txt", b"one\rtwo\n")
    add_tracked(tmp_path, "bom.py", b"\xef\xbb\xbfprint('bad')\n")
    add_tracked(tmp_path, "bad.md", f"{MOJIBAKE}\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding", "--no-stray-cr"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "bom.py" in captured.err
    assert "bad.md" in captured.err
    assert "stray.txt" not in captured.err


def test_check_encoding_stray_carriage_return_fails(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "stray.txt", b"one\ntwo\rthree\n")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["stray_cr_files"] == ["stray.txt"]
    assert payload["crlf_files"] == []


def test_check_encoding_crlf_path_with_space(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bad path.txt", b"one\r\ntwo\r\n")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding"])

    assert exit_code == 1
    assert "bad path.txt" in capsys.readouterr().err


def test_check_encoding_fix_strips_bom_and_preserves_remaining_bytes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    init_git_repo(tmp_path)
    payload = "print('ok')\r\n# \u3042\r\n".encode("utf-8")
    add_tracked(tmp_path, "bom.py", b"\xef\xbb\xbf" + payload)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding", "--fix"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Fixed BOM: bom.py" in captured.out
    assert "bom.py" in captured.err
    assert (tmp_path / "bom.py").read_bytes() == payload


def test_check_encoding_fix_does_not_modify_mojibake_files(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    init_git_repo(tmp_path)
    original = f"{MOJIBAKE}\r\n".encode("utf-8")
    add_tracked(tmp_path, "bad.md", original)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding", "--fix"])

    assert exit_code == 1
    assert "bad.md" in capsys.readouterr().err
    assert (tmp_path / "bad.md").read_bytes() == original


def test_check_encoding_fix_json_reports_fixed_files(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bom.py", b"\xef\xbb\xbfprint('ok')\n")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding", "--fix", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {
        "bom_files": [],
        "mojibake_files": [],
        "mojibake_hits": [],
        "unconfirmed_mojibake_hits": [],
        "stray_cr_files": [],
        "crlf_files": [],
        "fixed": ["bom.py"],
    }
    assert (tmp_path / "bom.py").read_bytes() == b"print('ok')\n"


def test_check_encoding_changed_skips_unchanged_files(tmp_path: Path, monkeypatch) -> None:
    # #164: with a clean working tree, --changed scans nothing and stays fast,
    # even though a committed file has a BOM that a full scan would flag.
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bom.py", b"\xef\xbb\xbfprint('bad')\n")
    commit_all(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 1
    assert cli.main(["check-encoding", "--changed"]) == 0


def test_check_encoding_changed_flags_new_untracked_file(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "clean.py", b"print('ok')\n")
    commit_all(tmp_path)
    (tmp_path / "new.py").write_bytes(b"\xef\xbb\xbfprint('bad')\n")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding", "--changed"])

    assert exit_code == 1
    assert "new.py" in capsys.readouterr().err


def test_check_encoding_changed_flags_crlf_in_changed_file(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "clean.txt", b"one\ntwo\n")
    commit_all(tmp_path)
    add_tracked(tmp_path, "clean.txt", b"one\r\ntwo\r\n")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding", "--changed"])

    assert exit_code == 1
    assert "clean.txt" in capsys.readouterr().err


def test_check_encoding_changed_base_diffs_against_ref(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "clean.py", b"print('ok')\n")
    base = commit_all(tmp_path)
    add_tracked(tmp_path, "bad.md", f"{MOJIBAKE}\n".encode("utf-8"))
    commit_all(tmp_path)
    monkeypatch.chdir(tmp_path)

    # A clean working tree means the default --changed run finds nothing.
    assert cli.main(["check-encoding", "--changed"]) == 0
    # Diffing against the base still catches the committed bad file.
    exit_code = cli.main(["check-encoding", "--changed", "--base", base])
    assert exit_code == 1
    assert "bad.md" in capsys.readouterr().err


def test_check_encoding_fix_exits_nonzero_when_mojibake_remains(
    tmp_path: Path,
    monkeypatch,
) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bom.py", b"\xef\xbb\xbfprint('ok')\n")
    add_tracked(tmp_path, "bad.md", f"{MOJIBAKE}\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding", "--fix"]) == 1
    assert (tmp_path / "bom.py").read_bytes() == b"print('ok')\n"
