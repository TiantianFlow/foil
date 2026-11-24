"""Delivery log, fleet pause, pointers, roster revisions, and session ids."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from foil.board import items_for, write_mail
from foil.cli import main
from foil.delivery import (
    LIMIT,
    append,
    derived,
    fleet_paused,
    list_pointers,
    pause_fleet,
    pending_pointers,
    read_log,
    resume_fleet,
)
from foil.mail_ops import undelivered
from foil.presets import BUILTIN_IDS, load_preset
from foil.runner import load_record, wake_prompt
from foil.store import fleet_dir
from tests.test_spawn import _commit, _launch, _repo
from tests.unit.test_runner import _plan, _run, _until

ROOT = Path(__file__).resolve().parents[1]


def test_derived_states_and_a_stuck_file_is_undelivered() -> None:
    assert derived("delivered", age=1000, running=False) == "delivered"
    assert derived("printed", age=1000, running=False) == "delivered"
    assert derived("skipped", age=1, running=False) == "skipped"
    assert derived("inflight", age=10, running=False) == "inflight"
    assert derived("retry", age=59, running=False) == "inflight"
    assert derived("inflight", age=60, running=False) == "stuck"
    assert derived("inflight", age=90, running=True) == "inflight"
    assert derived(None, age=10, running=False) == "queued"
    assert derived(None, age=60, running=False) == "stuck"


def test_the_log_rotates_at_the_size_cap(tmp_path: Path) -> None:
    fleet = tmp_path / "fleet"
    path = "mail/seat-1/one.md"
    append(fleet, "seat-1", [(path, "inflight")])
    rotated = fleet / "run" / "turns" / "seat-1.log.1"
    for _ in range(4000):
        append(fleet, "seat-1", [(path, "retry")])
        if rotated.exists():
            break
    else:
        raise AssertionError("delivery log did not rotate")
    append(fleet, "seat-1", [(path, "delivered")])
    active = fleet / "run" / "turns" / "seat-1.log"
    assert active.stat().st_size <= LIMIT
    assert active.stat().st_mode & 0o777 == 0o600
    lines = read_log(fleet, "seat-1")
    assert lines[-1]["state"] == "delivered"
    assert lines[0]["state"] == "inflight"
    assert all(set(item) == {"time", "path", "state"} for item in lines)


def test_mail_list_sent_reports_queued_and_stuck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    fresh = write_mail(repo, "lead", "just queued")
    board = fleet_dir(repo) / "board"
    old = board / "mail" / "lead" / "20200101T000000Z-user-abcdabcd.md"
    old.write_text(
        "---\ncontract: mail/v1\nfrom: user\nto: lead\ntime: 2020-01-01T00:00:00Z\n---\n\nold\n",
        encoding="utf-8",
    )
    assert main(["mail", "list", "--sent"]) == 0
    text = capsys.readouterr().out
    assert f"queued {fresh.resolve()}" in text
    assert f"stuck {old.resolve()}" in text
    assert main(["mail", "list", "--sent", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    by_state = {row["state"]: row for row in rows}
    assert by_state["queued"]["to"] == "lead"
    assert by_state["queued"]["from"] == "user"
    assert by_state["stuck"]["path"] == str(old.resolve())
    stuck = undelivered(board)
    assert [item["path"] for item in stuck] == [old.relative_to(board.resolve()).as_posix()]
    assert stuck[0]["kind"] == "undelivered"


def test_a_pointer_is_listed_once_and_wakes_nobody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    _commit(repo)
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "reviewer", "--name", "impl"]) == 0
    capsys.readouterr()
    board = fleet_dir(repo) / "board"
    before = items_for(board, "lead", set())
    assert main(["send", "impl", "direct"]) == 0
    after = items_for(board, "lead", set())
    assert [item["path"] for item in after] == [item["path"] for item in before]
    pending = pending_pointers(fleet_dir(repo))
    assert len(pending) == 1
    assert pending[0]["to"] == "impl"
    plan = {"seat": "lead", "instruction": "/instructions/lead.md", "system": False}
    prompt = wake_prompt(plan, [], restarted=False, pointers=pending)
    assert prompt.count(f"The human mailed impl directly: {pending[0]['time']} ") == 1
    list_pointers(fleet_dir(repo), [pending[0]["path"]])
    assert pending_pointers(fleet_dir(repo)) == []
    again = wake_prompt(plan, [], restarted=False, pointers=[])
    assert "The human mailed" not in again


def test_a_paused_runner_starts_nothing_and_resumes_the_queue(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import pathlib, sys\npathlib.Path(sys.argv[1]).write_text('ran')\n",
        encoding="utf-8",
    )
    plan = _plan(
        tmp_path,
        start=[sys.executable, str(probe), str(marker), "{prompt}"],
        turn=[sys.executable, str(probe), str(marker), "{prompt}"],
    )
    board = Path(plan["board"])
    mailed = board / "mail" / "seat-1" / "20200102T000000Z-user-abcdabcd.md"
    mailed.write_text(
        "---\ncontract: mail/v1\nfrom: user\nto: seat-1\ntime: 2020-01-02T00:00:00Z\n---\n\nwait\n",
        encoding="utf-8",
    )
    pause_fleet(board.parent, told=False)
    runner = _run(tmp_path, plan)
    try:
        time.sleep(2.5)
        assert not marker.exists()
        assert load_record(board, "seat-1")["turn_started"] is None
        assert read_log(board.parent, "seat-1") == []
        resume_fleet(board.parent)
        assert fleet_paused(board.parent) is False
        _until(marker.exists)
        relative = mailed.relative_to(board).as_posix()

        def delivered() -> bool:
            return any(item["state"] == "delivered" for item in read_log(board.parent, "seat-1"))

        _until(delivered)
        states = [
            item["state"]
            for item in read_log(board.parent, "seat-1")
            if item["path"] == relative
        ]
        assert states == ["inflight", "delivered"]
    finally:
        os.killpg(runner.pid, signal.SIGKILL)
        runner.wait(timeout=5)


def test_spawn_task_and_renew_log_skipped_and_keep_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    older = write_mail(repo, "lead", "from the previous goal")
    assert main(["seat", "spawn", "lead", "--task", "ship it"]) == 0
    capsys.readouterr()
    board = fleet_dir(repo) / "board"
    record = load_record(board, "lead")
    older_rel = older.resolve().relative_to(board.resolve()).as_posix()
    assert record["inflight"]
    task_rel = record["inflight"][0]
    assert task_rel != older_rel
    logged = read_log(fleet_dir(repo), "lead")
    assert [item["path"] for item in logged if item["state"] == "skipped"] == [older_rel]
    assert task_rel not in {item["path"] for item in logged}
    first = [item["id"] for item in record["sessions"]]
    assert len(first) == 1
    handoff = board / "handoffs"
    handoff.mkdir()
    (handoff / "lead.md").write_text("continue\n", encoding="utf-8")
    assert main(["seat", "renew", "lead"]) == 0
    renewed = load_record(board, "lead")
    ids = [item["id"] for item in renewed["sessions"]]
    assert ids[0] == first[0]
    assert len(ids) == 2
    assert ids[0] != ids[1]
    skipped = [
        item["path"]
        for item in read_log(fleet_dir(repo), "lead")
        if item["state"] == "skipped"
    ]
    assert older_rel in skipped
    assert task_rel in skipped
    bare = dict(renewed)
    del bare["sessions"]
    path = board.parent / "run" / "turns" / "lead.json"
    path.write_text(json.dumps(bare), encoding="utf-8")
    assert load_record(board, "lead")["sessions"] == []


def test_a_stale_roster_base_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    assert main(["roster", "list", "--json"]) == 0
    revision = json.loads(capsys.readouterr().out)["revision"]
    assert main(["roster", "update", "lead", "model=other", "--base", "0" * 64]) == 1
    err = capsys.readouterr().err
    assert err == f"foil: roster changed, current revision {revision}\n"
    assert main(["roster", "update", "lead", "model=other", "--base", revision]) == 0
    capsys.readouterr()
    assert main(["roster", "list", "--json"]) == 0
    current = json.loads(capsys.readouterr().out)["revision"]
    assert current != revision
    assert main(["roster", "update", "lead", "model=third", "--base", revision]) == 1
    assert capsys.readouterr().err == f"foil: roster changed, current revision {current}\n"


def test_builtins_name_a_transcript_and_core_does_not_read_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    for harness_id in BUILTIN_IDS:
        assert load_preset(repo, harness_id)["transcript"] == harness_id
    for name in ("runner.py", "lifecycle.py", "joined.py", "mail_ops.py", "board.py"):
        assert "transcript" not in (ROOT / "src" / "foil" / name).read_text(encoding="utf-8")


def test_a_worker_cannot_pause_and_a_joined_lead_is_told_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "impl")
    assert main(["fleet", "pause"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"
    monkeypatch.delenv("FOIL_SEAT_ID")
    assert main(["seat", "join", "lead", "--harness", "fake", "--session", "s-1"]) == 0
    capsys.readouterr()
    assert main(["fleet", "pause", "--as", "user"]) == 0
    told = capsys.readouterr()
    assert "foil: fleet paused" in told.out
    assert "foil: fleet is paused" not in told.err
    assert main(["mail", "list"]) == 0
    assert "foil: fleet is paused" in capsys.readouterr().err
    assert main(["mail", "list"]) == 0
    assert "foil: fleet is paused" not in capsys.readouterr().err
    assert main(["fleet", "resume"]) == 0
    capsys.readouterr()
    assert main(["fleet", "pause"]) == 0
    own = capsys.readouterr()
    assert "foil: fleet paused" in own.out
    assert "foil: fleet is paused" not in own.err
    from foil_bridge.evidence import _fleet as fleet_page
    from foil_bridge.pages import evidence_html

    html = evidence_html(fleet_page({"project": "local", "fleet": "main"}, repo), "token").decode()
    assert "fleet paused" in html


def test_mail_wait_appends_printed_for_a_joined_lead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    assert main(["seat", "join", "lead", "--harness", "fake", "--session", "s-1"]) == 0
    capsys.readouterr()
    assert main(["--as", "user", "send", "lead", "ping"]) == 0
    capsys.readouterr()
    assert main(["mail", "wait", "--timeout", "0"]) == 0
    printed = [item for item in read_log(fleet_dir(repo), "lead") if item["state"] == "printed"]
    assert len(printed) == 1
    assert printed[0]["path"].startswith("mail/lead/")


def test_skipped_does_not_replace_a_delivery(tmp_path: Path) -> None:
    from foil.delivery import skipped

    fleet = tmp_path / "fleet"
    delivered = "mail/lead/done.md"
    append(fleet, "lead", [(delivered, "delivered")])
    skipped(fleet, "lead", [delivered, "mail/lead/held.md"])
    states = {item["path"]: item["state"] for item in read_log(fleet, "lead")}
    assert states[delivered] == "delivered"
    assert states["mail/lead/held.md"] == "skipped"


def test_a_dead_process_group_does_not_keep_mail_inflight(tmp_path: Path) -> None:
    import subprocess

    from foil.mail_ops import _sent_state
    from foil.runner import empty_record, save_record

    board = tmp_path / "fleet" / "board"
    relative = "mail/seat-1/old.md"
    log = board.parent / "run" / "turns" / "seat-1.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps(
            {"path": relative, "state": "inflight", "time": "2020-01-01T00:00:00Z"},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    child = subprocess.Popen(["true"])
    child.wait()
    record = empty_record()
    record["turn_started"] = 1.0
    record["inflight"] = [relative]
    record["pgid"] = child.pid
    save_record(board, "seat-1", record)
    assert _sent_state(board, "seat-1", relative, "2020-01-01T00:00:00Z") == "stuck"
    record["pgid"] = os.getpid()
    save_record(board, "seat-1", record)
    assert _sent_state(board, "seat-1", relative, "2020-01-01T00:00:00Z") == "inflight"
