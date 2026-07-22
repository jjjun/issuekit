from pathlib import Path

from issuekit.config.local import (
    LOCAL_CONFIG_NAME,
    missing_gitignore_entries,
    read_local_config,
    write_local_config,
)


def test_local_config_round_trips_worker_and_refs(tmp_path: Path) -> None:
    worker = {
        "machine_id": "machine",
        "repo_id": "repo",
        "worker_name": "checkout",
    }
    refs = {"other": "../other", "self": "."}

    write_local_config(tmp_path, worker=worker, refs=refs)

    local_config = read_local_config(tmp_path)
    assert local_config.worker == worker
    assert local_config.refs == refs
    assert (tmp_path / LOCAL_CONFIG_NAME).read_text(encoding="utf-8") == (
        "[worker]\n"
        'machine_id = "machine"\n'
        'repo_id = "repo"\n'
        'worker_name = "checkout"\n'
        "\n"
        "[refs]\n"
        'other = "../other"\n'
        'self = "."\n'
    )


def test_local_config_reads_legacy_worker_id(tmp_path: Path) -> None:
    (tmp_path / LOCAL_CONFIG_NAME).write_text(
        (
            "[worker]\n"
            'machine_id = "machine"\n'
            'repo_id = "repo"\n'
            'worker_id = "checkout"\n'
            "\n"
            "[refs]\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    assert read_local_config(tmp_path).worker == {
        "machine_id": "machine",
        "repo_id": "repo",
        "worker_id": "checkout",
    }


def test_local_config_reads_disabled_agents(tmp_path: Path) -> None:
    (tmp_path / LOCAL_CONFIG_NAME).write_text(
        (
            'disabled_agents = ["kimi", "old_agent"]\n'
            "\n"
            "[refs]\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    assert read_local_config(tmp_path).disabled_agents == ("kimi", "old_agent")


def test_local_config_reads_tool_issuekit_disabled_agents(tmp_path: Path) -> None:
    (tmp_path / LOCAL_CONFIG_NAME).write_text(
        (
            "[tool.issuekit]\n"
            'disabled_agents = ["kimi"]\n'
            "\n"
            "[refs]\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    assert read_local_config(tmp_path).disabled_agents == ("kimi",)


def test_local_config_preserves_disabled_agents_when_rewriting_refs(tmp_path: Path) -> None:
    (tmp_path / LOCAL_CONFIG_NAME).write_text(
        (
            'disabled_agents = ["kimi"]\n'
            "\n"
            "[refs]\n"
            'self = "."\n'
        ),
        encoding="utf-8",
        newline="\n",
    )

    write_local_config(tmp_path, worker=None, refs={"other": "../other"})

    assert read_local_config(tmp_path).disabled_agents == ("kimi",)
    assert (tmp_path / LOCAL_CONFIG_NAME).read_text(encoding="utf-8") == (
        'disabled_agents = ["kimi"]\n'
        "\n"
        "[refs]\n"
        'other = "../other"\n'
    )


def test_local_config_writes_explicit_empty_disabled_agents(tmp_path: Path) -> None:
    write_local_config(tmp_path, worker=None, refs={}, disabled_agents=())

    assert read_local_config(tmp_path).disabled_agents == ()
    assert (tmp_path / LOCAL_CONFIG_NAME).read_text(encoding="utf-8") == (
        "disabled_agents = []\n"
        "\n"
        "[refs]\n"
    )


def test_local_config_round_trips_author_guard(tmp_path: Path) -> None:
    guard = {
        "project": "demo",
        "kind": "issue",
        "id": "12",
        "ref": "demo#12",
        "author_agent": "codex",
        "worker": "machine/repo/checkout",
        "created": "2026-07-02T00:00:00+00:00",
        "required_next_action": "STOP",
    }

    write_local_config(tmp_path, worker=None, refs={}, author_guard=guard)

    local_config = read_local_config(tmp_path)
    assert local_config.author_guard == guard
    assert "[author_guard]" in (tmp_path / LOCAL_CONFIG_NAME).read_text(encoding="utf-8")


def test_missing_gitignore_entries_accepts_agent_runs_without_slash() -> None:
    assert missing_gitignore_entries("issuekit.local.toml\n.agent-runs\n") == []
    assert missing_gitignore_entries("issuekit.local.toml\n") == [".agent-runs/"]
