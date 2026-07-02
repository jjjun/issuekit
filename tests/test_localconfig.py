from pathlib import Path

from issuekit.localconfig import (
    LOCAL_CONFIG_NAME,
    missing_gitignore_entries,
    read_local_config,
    write_local_config,
)


def test_local_config_round_trips_worker_and_refs(tmp_path: Path) -> None:
    worker = {
        "machine_id": "machine",
        "repo_id": "repo",
        "worker_id": "checkout",
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
        'worker_id = "checkout"\n'
        "\n"
        "[refs]\n"
        'other = "../other"\n'
        'self = "."\n'
    )


def test_missing_gitignore_entries_accepts_agent_runs_without_slash() -> None:
    assert missing_gitignore_entries("issuekit.local.toml\n.agent-runs\n") == []
    assert missing_gitignore_entries("issuekit.local.toml\n") == [".agent-runs/"]
