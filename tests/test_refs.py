from pathlib import Path

import pytest

from issuekit.refs import (
    RefError,
    add_ref,
    add_workspace_ref,
    find_workspace_file,
    list_effective_refs,
    list_refs,
    load_workspace_refs,
    resolve_ref,
)


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


def test_workspace_file_is_discovered_from_parent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ISSUEKIT_WORKSPACE", raising=False)
    repo = tmp_path / "repo" / "nested"
    repo.mkdir(parents=True)
    workspace_file = tmp_path / "issuekit.workspace.toml"
    workspace_file.write_text("[projects]\nrepo = \"repo\"\n", encoding="utf-8", newline="\n")

    assert find_workspace_file(repo) == workspace_file


def test_workspace_env_overrides_discovery(tmp_path: Path, monkeypatch) -> None:
    discovered = tmp_path / "issuekit.workspace.toml"
    override = tmp_path / "override.toml"
    discovered.write_text("[projects]\ndiscovered = \"repo\"\n", encoding="utf-8", newline="\n")
    override.write_text("[projects]\noverride = \"repo\"\n", encoding="utf-8", newline="\n")
    monkeypatch.setenv("ISSUEKIT_WORKSPACE", str(override))

    assert find_workspace_file(tmp_path) == override
    assert load_workspace_refs(tmp_path) == {"override": (tmp_path / "repo").resolve().as_posix()}


def test_workspace_refs_resolve_relative_to_workspace_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ISSUEKIT_WORKSPACE", raising=False)
    repo = tmp_path / "repo"
    target = tmp_path / "target"
    nested = repo / "subdir"
    nested.mkdir(parents=True)
    target.mkdir()
    (tmp_path / "issuekit.workspace.toml").write_text(
        "[projects]\ntarget = \"target\"\n",
        encoding="utf-8",
        newline="\n",
    )

    refs = load_workspace_refs(nested)
    resolved = resolve_ref("target", nested)

    assert refs == {"target": target.resolve().as_posix()}
    assert resolved.repo_path == target.resolve()


def test_effective_refs_merge_workspace_and_local_with_local_precedence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ISSUEKIT_WORKSPACE", raising=False)
    repo = tmp_path / "repo"
    workspace_target = tmp_path / "workspace-target"
    local_target = tmp_path / "local-target"
    workspace_only = tmp_path / "workspace-only"
    local_only = tmp_path / "local-only"
    for path in (repo, workspace_target, local_target, workspace_only, local_only):
        path.mkdir()
    (tmp_path / "issuekit.workspace.toml").write_text(
        "[projects]\n"
        "shared = \"workspace-target\"\n"
        "workspace-only = \"workspace-only\"\n",
        encoding="utf-8",
        newline="\n",
    )
    add_ref("shared", local_target, repo)
    add_ref("local-only", local_only, repo)

    refs = list_effective_refs(repo)

    assert refs["shared"].path == local_target.resolve()
    assert refs["shared"].source == "local"
    assert refs["workspace-only"].path == workspace_only.resolve()
    assert refs["workspace-only"].source == "workspace"
    assert refs["local-only"].path == local_only.resolve()
    assert refs["local-only"].source == "local"


def test_add_workspace_ref_writes_relative_project_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ISSUEKIT_WORKSPACE", raising=False)
    repo = tmp_path / "repo"
    target = tmp_path / "target"
    repo.mkdir()
    target.mkdir()
    workspace_file = tmp_path / "issuekit.workspace.toml"
    workspace_file.write_text("[projects]\n", encoding="utf-8", newline="\n")

    refs = add_workspace_ref("target", target, repo)

    assert refs == {"target": "target"}
    assert 'target = "target"' in workspace_file.read_text(encoding="utf-8")


def test_workspace_scope_requires_discovered_or_explicit_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ISSUEKIT_WORKSPACE", raising=False)
    repo = tmp_path / "repo"
    target = tmp_path / "target"
    repo.mkdir()
    target.mkdir()

    with pytest.raises(RefError, match="No issuekit.workspace.toml found"):
        add_workspace_ref("target", target, repo)


def test_malformed_workspace_projects_table_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ISSUEKIT_WORKSPACE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "issuekit.workspace.toml").write_text(
        "projects = []\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(RefError, match=r"\[projects\] table"):
        load_workspace_refs(repo)


def test_add_ref_rejects_missing_path(tmp_path: Path) -> None:
    with pytest.raises(RefError, match="does not exist"):
        add_ref("missing", tmp_path / "missing", tmp_path)


def test_resolve_ref_rejects_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(RefError, match="Unknown ref"):
        resolve_ref("missing", tmp_path)
