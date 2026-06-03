from pathlib import Path

import pytest

from issuekit.refs import RefError, add_ref, list_refs, resolve_ref


def test_add_list_and_resolve_ref(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (target / "issuekit.toml").write_text(
        'issues_dir = "tracker"\n',
        encoding="utf-8",
        newline="\n",
    )

    add_ref("target", target, source)

    refs = list_refs(source)
    resolved = resolve_ref("target", source)

    assert refs == {"target": target.resolve().as_posix()}
    assert resolved.repo_path == target.resolve()
    assert resolved.issues_dir == target.resolve() / "tracker"


def test_add_ref_rejects_missing_path(tmp_path: Path) -> None:
    with pytest.raises(RefError, match="does not exist"):
        add_ref("missing", tmp_path / "missing", tmp_path)


def test_resolve_ref_rejects_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(RefError, match="Unknown ref"):
        resolve_ref("missing", tmp_path)
