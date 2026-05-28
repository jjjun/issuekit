import json
from pathlib import Path
import subprocess

from issuekit import cli


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)


def add_tracked(path: Path, name: str, content: bytes) -> None:
    file_path = path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)
    subprocess.run(["git", "add", name], cwd=path, check=True)


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
    assert payload == {"bom_files": ["bom.py"], "mojibake_files": ["bad.md"]}


def test_check_encoding_no_mojibake_toggle(tmp_path: Path, monkeypatch) -> None:
    init_git_repo(tmp_path)
    add_tracked(tmp_path, "bad.md", "\u7e67\n".encode("utf-8"))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["check-encoding", "--no-mojibake"]) == 0
