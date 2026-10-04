from pathlib import Path

import pytest

from issuekit.paths import display_path, is_readable_regular_file, is_under


def test_display_path_resolves_base_and_falls_back_outside_it(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    target = base / "nested" / "file.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8", newline="\n")
    base_link = tmp_path / "base-link"
    try:
        base_link.symlink_to(base, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    assert display_path(target, base_link) == "nested/file.py"
    outside = tmp_path / "outside.py"
    assert display_path(outside, base) == outside.as_posix()


def test_path_helpers_check_regular_readable_files_and_containment(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    file = root / "file.py"
    file.write_text("value = 1\n", encoding="utf-8", newline="\n")

    assert is_readable_regular_file(file)
    assert not is_readable_regular_file(root)
    assert is_under(file, root)
    assert not is_under(tmp_path / "outside.py", root)
