from pathlib import Path

import pytest

import issuekit.agentrun.logtail as logtail_module
from issuekit import cli
from issuekit.agentrun.logtail import last_nonempty_line_of, tail_lines
from issuekit.agentrun.status import RunStatus, status_path, write_status
from issuekit.coerce import last_nonempty_line


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("empty", b""),
        ("without trailing newline", b"first\nlast"),
        ("trailing blank line", b"first\n \t\n"),
        ("last line longer than window", b"first\n" + b"x" * 70_000),
        ("invalid UTF-8", b"first\nbad-\xff\n"),
    ],
    ids=[
        "empty",
        "no trailing newline",
        "trailing blank",
        "long final line",
        "invalid UTF-8",
    ],
)
def test_last_nonempty_line_matches_whole_file_semantics(
    tmp_path: Path, name: str, content: bytes
) -> None:
    path = tmp_path / f"{name}.log"
    path.write_bytes(content)

    result = last_nonempty_line_of(path)

    expected = last_nonempty_line(content.decode("utf-8", errors="replace"))
    if expected is None:
        assert result is None
    else:
        assert result == (expected, path.stat().st_mtime_ns)


def test_tail_lines_expands_until_long_last_line_is_complete(tmp_path: Path) -> None:
    path = tmp_path / "long.log"
    path.write_bytes(b"first\nsecond\n" + b"z" * 70_000)

    assert tail_lines(path, 2) == ["second", "z" * 70_000]


def test_runs_detail_reads_large_log_tail_without_reading_whole_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    run_dir = tmp_path / ".agent-runs"
    run_dir.mkdir()
    run_id = "20261004-000002"
    stdout_log = run_dir / f"{run_id}.out.log"
    entries = [f"out-{index:04d}-" + "x" * 90 for index in range(1000)]
    stdout_log.write_text(
        "\n".join(entries) + "\n", encoding="utf-8", newline="\n"
    )
    agent_log = run_dir / f"{run_id}.agent.log"
    agent_log.write_text("agent log\n", encoding="utf-8", newline="\n")
    write_status(
        status_path(run_dir, run_id),
        RunStatus(
            run_id=run_id,
            agent="codex",
            issue=441,
            status="completed",
            pid=None,
            started_at="2026-10-04T00:00:00",
            ended_at="2026-10-04T00:00:01",
            elapsed_sec=1.0,
            exit_code=0,
            plan=".agent-runs/plan.md",
            stdout_log=f".agent-runs/{run_id}.out.log",
            agent_log=f".agent-runs/{run_id}.agent.log",
        ),
    )
    read_sizes: list[tuple[str, int, int]] = []
    read_tail = logtail_module.read_tail

    def measured_read_tail(path: Path, *, max_bytes: int = 65_536) -> bytes:
        data = read_tail(path, max_bytes=max_bytes)
        read_sizes.append((path.name, max_bytes, len(data)))
        return data

    monkeypatch.setattr(logtail_module, "read_tail", measured_read_tail)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["runs", run_id]) == 0

    output = capsys.readouterr().out
    stdout_tail = output.split("--- stdout tail", 1)[1].split("--- agent tail", 1)[0]
    assert stdout_tail.splitlines()[1:] == entries[-40:]
    stdout_reads = [read for read in read_sizes if read[0] == stdout_log.name]
    assert len(stdout_reads) == 1
    assert stdout_reads[0][1] == 65_536
    assert stdout_reads[0][2] <= 65_536 < stdout_log.stat().st_size
