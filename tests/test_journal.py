"""Fleet journal: one line per fact, rotation, and foil decide."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from foil import api
from foil.cli import main
from foil.errors import FoilError
from foil.journal import LIMIT, read_log
from foil.project import foil_root
from foil.runner import load_record, save_record
from foil.store import load_registry
from tests.test_joined import _handoff
from tests.test_room_post import _install_tmux
from tests.test_spawn import _killable, _launch, _repo


def _log(repo: Path, fleet: str = "main") -> Path:
    return foil_root(repo) / "fleets" / fleet / "run" / "journal.log"


def _facts(repo: Path, fleet: str = "main") -> list[dict]:
    return read_log(foil_root(repo) / "fleets" / fleet)


def _events(repo: Path, fleet: str = "main") -> list[str]:
    return [item["event"] for item in _facts(repo, fleet)]


def test_decide_appends_one_line_and_json_matches_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    assert main(["decide", "ship it", "--why", "the tests passed"]) == 0
    assert capsys.readouterr().out == ""
    listed = api.journal(repo, "main", "user")
    capsys.readouterr()
    assert main(["journal", "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == listed
    assert printed[0]["event"] == "decision"
    assert printed[0]["by"] == "user"
    assert printed[0]["text"] == "ship it"
    assert printed[0]["why"] == "the tests passed"
    assert "n" in printed[0]
    human = main(["journal"])
    assert human == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{printed[0]['time']} decision by=user text=\"ship it\" why=\"the tests passed\""
    ]
    worker = api.decide(
        repo, "main", "implementer-1", "use the small fix", why="it fits the task", inside=True
    )
    assert worker == {"ok": True}
    assert _facts(repo)[-1]["by"] == "implementer-1"


def test_decide_refuses_a_credential_a_newline_and_a_long_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    api.decide(repo, "main", "user", "keep this", why="it was already written")
    before = _log(repo).read_text(encoding="utf-8")
    cases = (
        ("sk-refused", "because", "foil: credential-shaped text refused\n"),
        ("keep this", "bearer nope", "foil: credential-shaped text refused\n"),
        ("one\ntwo", "because", "foil: decision text must be one line\n"),
        ("", "because", "foil: decision text is empty\n"),
        ("keep", "   ", "foil: decision reason is empty\n"),
        ("x" * 401, "because", "foil: decision text is too long\n"),
        ("keep", "y" * 401, "foil: decision reason is too long\n"),
    )
    for text, why, error in cases:
        assert main(["decide", text, "--why", why]) == 1
        assert capsys.readouterr().err == error
        assert _log(repo).read_text(encoding="utf-8") == before
    with pytest.raises(FoilError, match="credential-shaped text refused"):
        api.decide(repo, "main", "user", "sk-refused", why="no")
    assert _log(repo).read_text(encoding="utf-8") == before


def test_since_keeps_facts_at_or_after_the_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    stamps = iter(("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"))
    monkeypatch.setattr("foil.journal._now", lambda: next(stamps))
    api.decide(repo, "main", "user", "first", why="earlier")
    api.decide(repo, "main", "user", "second", why="later")
    kept = api.journal(repo, "main", "user", since="2026-01-02T00:00:00Z")
    assert [item["text"] for item in kept] == ["second"]
    capsys.readouterr()
    assert main(["journal", "--since", "not-a-time"]) == 1
    assert capsys.readouterr().err == "foil: --since is YYYY-MM-DDTHH:MM:SSZ\n"


def test_rotation_keeps_one_previous_file_and_drops_the_oldest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr("foil.journal.LIMIT", 160)
    assert LIMIT == 64 * 1024
    for name in ("one", "two", "three"):
        api.decide(repo, "main", "user", name, why="rotation")
    previous = _log(repo).with_name("journal.log.1")
    active = _log(repo).read_text(encoding="utf-8")
    assert previous.is_file()
    assert "three" in active
    assert "one" not in active
    assert "one" not in previous.read_text(encoding="utf-8")
    texts = [item["text"] for item in _facts(repo)]
    assert texts == ["two", "three"]
    for line in previous.read_text(encoding="utf-8").splitlines() + active.splitlines():
        json.loads(line)


def test_two_fleets_do_not_share_a_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    api.decide(repo, "main", "user", "main choice", why="this fleet")
    api.decide(repo, "side", "user", "side choice", why="the other fleet")
    assert [item["text"] for item in api.journal(repo, "main", "user")] == ["main choice"]
    assert [item["text"] for item in api.journal(repo, "side", "user")] == ["side choice"]


def test_follow_streams_a_line_appended_after_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    api.decide(repo, "main", "user", "already", why="before")
    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "foil", "journal", "--follow", "--json"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    try:
        first = _read_line(process, timeout=5)
        assert json.loads(first)["text"] == "already"
        api.decide(repo, "main", "user", "arrived", why="after")
        second = _read_line(process, timeout=5)
        assert json.loads(second)["text"] == "arrived"
    finally:
        process.kill()
        process.wait(timeout=5)


def _read_line(process: subprocess.Popen[bytes], timeout: float) -> str:
    assert process.stdout is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], 0.2)
        if not ready:
            continue
        line = process.stdout.readline()
        if line:
            return line.decode()
    raise AssertionError("journal --follow did not stream a line")


def test_spawn_send_resume_renew_and_kill_each_write_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    assert main(["seat", "spawn", "no-such-role"]) == 1
    assert not _log(repo).exists()
    capsys.readouterr()
    body = "TASK-BODY-NOT-IN-JOURNAL"
    assert main(["seat", "spawn", "lead", "--task", body]) == 0
    capsys.readouterr()
    kinds = _events(repo)
    assert kinds == ["spawn", "mail_sent"]
    mailed = _facts(repo)[1]
    assert mailed["from"] == "user" and mailed["to"] == "lead"
    assert mailed["path"].startswith("mail/lead/")
    assert body not in _log(repo).read_text(encoding="utf-8")
    sent = "SENT-BODY-NOT-IN-JOURNAL"
    assert main(["send", "lead", sent]) == 0
    assert _events(repo)[-1] == "mail_sent"
    assert sent not in _log(repo).read_text(encoding="utf-8")

    _killable(monkeypatch)
    board = foil_root(repo) / "fleets" / "main" / "board"
    record = load_record(board, "lead")
    record["paused"] = True
    save_record(board, "lead", record)
    assert main(["seat", "resume", "lead"]) == 0
    assert _events(repo)[-1] == "resume"
    _handoff(repo)
    assert main(["seat", "renew", "lead"]) == 0
    assert _events(repo)[-1] == "renew"
    assert main(["seat", "kill", "lead"]) == 0
    assert _events(repo)[-1] == "kill"
    assert load_registry(repo)["seats"]["lead"]["state"] == "killed"
    assert _events(repo).count("spawn") == 1
    assert _events(repo).count("kill") == 1


def test_join_and_leave_each_write_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _install_tmux(monkeypatch)
    assert main(["seat", "join", "lead", "--harness", "fake", "--session", "s-1"]) == 0
    capsys.readouterr()
    assert _events(repo) == ["join"]
    assert _facts(repo)[0]["seat"] == "lead"
    assert main(["seat", "leave"]) == 1
    assert _events(repo) == ["join"]
    _handoff(repo)
    assert main(["seat", "leave"]) == 0
    assert _events(repo) == ["join", "leave"]
    assert not os.environ.get("FOIL_SEAT_ID")
