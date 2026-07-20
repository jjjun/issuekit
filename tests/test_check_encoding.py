import json
from pathlib import Path
import subprocess

from issuekit import cli


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


def test_check_encoding_bom_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bom.py", b"\xef\xbb\xbfprint('bad')\n")
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding"])

    assert exit_code == 1
    assert "bom.py" in capsys.readouterr().err


def test_check_encoding_mojibake_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bad.md", "\u7e67\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding"])

    assert exit_code == 1
    assert "bad.md" in capsys.readouterr().err


def test_check_encoding_ignores_non_source_extensions(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "image.bin", b"\xef\xbb\xbf")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding"]) == 0


def test_check_encoding_json_shape(tmp_path: Path, monkeypatch, capsys) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bom.py", b"\xef\xbb\xbfprint('bad')\n")
    add_tracked(tmp_path, "bad.md", "\u7e67\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["check-encoding", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload == {
        "bom_files": ["bom.py"],
        "mojibake_files": ["bad.md"],
        "stray_cr_files": [],
        "crlf_files": [],
        "fixed": [],
    }


def test_check_encoding_no_mojibake_toggle(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bad.md", "\u7e67\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding", "--no-mojibake"]) == 0


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
    add_tracked(tmp_path, "bad.md", "\u7e67\n".encode("utf-8"))
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
    add_tracked(tmp_path, "bad.md", "\u7e67\n".encode("utf-8"))
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
    original = "\u7e67\r\n".encode("utf-8")
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
    add_tracked(tmp_path, "bad.md", "\u7e67\n".encode("utf-8"))
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
    add_tracked(tmp_path, "bad.md", "\u7e67\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding", "--fix"]) == 1
    assert (tmp_path / "bom.py").read_bytes() == b"print('ok')\n"
