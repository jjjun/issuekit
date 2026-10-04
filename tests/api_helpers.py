from pathlib import Path

import pytest


def configure_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_api,
    client,
    *,
    project: str = "demo",
    worker: bool = False,
    extra_config: str = "",
    chdir: bool = True,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        f"api_url = 'https://mine.example'\nproject = '{project}'\n{extra_config}",
        encoding="utf-8",
        newline="\n",
    )
    if worker:
        (tmp_path / "issuekit.local.toml").write_text(
            "[worker]\n"
            "machine_id = 'machine'\n"
            "repo_id = 'issuekit'\n"
            "worker_name = 'operator'\n",
            encoding="utf-8",
            newline="\n",
        )
    fake_api.install_client(client)
    if chdir:
        monkeypatch.chdir(tmp_path)
