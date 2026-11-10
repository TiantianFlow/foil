"""F41 and F42: your own agent joins as the lead, waits for mail, and leaves.

A stand-in tmux answers every call. No network and no agent CLI.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from foil.board_ops import split_front_matter
from foil.cli import main
from foil.project import foil_root
from foil.store import load_registry
from tests.test_room_post import _fleet, _install_tmux, _seat
from tests.test_slice_b import _repo


def _board(repo: Path) -> Path:
    return foil_root(repo) / "fleets" / "main" / "board"


def _run(repo: Path) -> Path:
    return foil_root(repo) / "fleets" / "main" / "run"


def _join(repo: Path, capsys: pytest.CaptureFixture[str], *args: str) -> str:
    assert main(["seat", "join", "lead", *args]) == 0
    return capsys.readouterr().out


def _handoff(repo: Path) -> None:
    path = _board(repo) / "handoffs" / "lead.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("the plan\n", encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = _repo(tmp_path, monkeypatch)
    _install_tmux(monkeypatch)
    monkeypatch.setattr("foil.joined.COALESCE", 0)
    return path


def test_join_writes_a_joined_lead_with_no_window(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    brief = _join(repo, capsys)
    record = load_registry(repo)["seats"]["lead"]
    assert record["state"] == "joined"
    assert record["window_id"] == record["worktree"] == record["branch"] == ""
    instruction = (_run(repo) / "instructions" / "lead.md").resolve()
    assert f"Read first: {instruction}" in brief
    assert "# Joined lead" in instruction.read_text(encoding="utf-8")
    assert "New for lead: none." in brief
    assert "foil mail wait --timeout 0" in brief
    assert len(brief.encode()) < 2048
    assert main(["seat", "list"]) == 0
    assert "\tjoined\t" in capsys.readouterr().out


def test_join_takes_the_harness_and_session_from_the_environment(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s-1")
    monkeypatch.setenv("CODEX_THREAD_ID", "t-1")
    brief = _join(repo, capsys)
    record = load_registry(repo)["seats"]["lead"]
    assert (record["harness"], record["session_id"]) == ("codex", "t-1")
    assert "codex queue --thread" in brief
    assert main(["seat", "join", "lead", "--take", "--harness", "grok", "--session", "g-2"]) == 0
    record = load_registry(repo)["seats"]["lead"]
    assert (record["harness"], record["session_id"]) == ("grok", "g-2")
    assert "background command" in capsys.readouterr().out
    assert main(["seat", "join", "lead", "--take", "--session", "a b"]) == 1
    assert capsys.readouterr().err == "foil: invalid id\n"


def test_join_refuses_a_held_seat(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fleet(repo, _seat("lead", window="@1"))
    assert main(["seat", "join", "lead"]) == 1
    assert capsys.readouterr().err == "foil: lead is alive. Run foil seat kill lead first\n"
    _fleet(repo, _seat("lead", state="dead", window=""))
    assert main(["seat", "join", "lead"]) == 1
    assert capsys.readouterr().err == "foil: lead is dead. Run foil seat kill lead first\n"
    _fleet(repo, _seat("lead", state="killed", window="@1"))
    _join(repo, capsys)
    assert main(["seat", "join", "lead"]) == 1
    assert capsys.readouterr().err == "foil: lead is joined. Pass --take to take it\n"


def test_join_refuses_inside_a_seat_and_without_tmux(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["seat", "join", "lead"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"
    monkeypatch.delenv("FOIL_SEAT_ID")

    def broken(self, argv):
        raise OSError("sandboxed")

    monkeypatch.setattr("foil.tmux.TmuxController._run", broken)
    assert main(["seat", "join", "lead"]) == 1
    err = capsys.readouterr().err
    assert err == "foil: run foil outside your harness sandbox and without a prompt\n"
    assert "lead" not in load_registry(repo)["seats"]


def test_join_marks_earlier_mail_seen_and_lists_it(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fleet(repo, _seat("lead", state="killed", window=""), _seat("implementer-1", window="@2"))
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    for index in range(12):
        assert main(["send", "lead", f"note {index}"]) == 0
    monkeypatch.delenv("FOIL_SEAT_ID")
    capsys.readouterr()
    brief = _join(repo, capsys)
    assert "New for lead (12):" in brief
    assert brief.count(" implementer-1 ") == 10
    assert "... and 2 more." in brief
    assert main(["mail", "wait", "--timeout", "0"]) == 0
    assert capsys.readouterr().out == "no new mail\n"


def test_join_warns_about_uncommitted_changes(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert "uncommitted" not in _join(repo, capsys)
    (repo / "draft.txt").write_text("wip\n", encoding="utf-8")
    assert "Workers branch from HEAD" in _join(repo, capsys, "--take")


def test_a_joined_lead_acts_as_lead_outside_the_fleet(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fleet(repo, _seat("lead", state="killed", window=""), _seat("implementer-1", window="@2"))
    _join(repo, capsys)
    assert main(["send", "implementer-1", "hello"]) == 0
    mail = next((_board(repo) / "mail" / "implementer-1").glob("*.md"))
    fields, _ = split_front_matter(mail.read_text(encoding="utf-8"))
    assert fields["from"] == "lead"
    assert "-lead-" in mail.name
    assert main(["send", "user", "hello"]) == 1
    assert capsys.readouterr().err == "foil: a joined lead asks in its own chat\n"


def test_a_room_post_wakes_a_joined_lead_without_a_keystroke(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fleet(repo, _seat("lead", state="killed", window=""), _seat("implementer-1", window="@2"))
    _join(repo, capsys)
    assert main(["post", "--members", "lead,implementer-1", "a question"]) == 0
    opened = next(_board(repo).joinpath("room").rglob("*.md"))
    fields, _ = split_front_matter(opened.read_text(encoding="utf-8"))
    assert fields["from"] == "lead"
    assert fields["nudged"] == ["implementer-1"]
    assert main(["mail", "wait", "--timeout", "0"]) == 0
    assert capsys.readouterr().out == "no new mail\n"
    calls = _install_tmux(monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["post", "--re", str(opened), "an answer"]) == 0
    monkeypatch.delenv("FOIL_SEAT_ID")
    room = next(path for path in opened.parent.glob("*.md") if path != opened)
    fields, _ = split_front_matter(room.read_text(encoding="utf-8"))
    assert fields["nudged"] == ["lead"]
    assert not [call for call in calls if call[:1] == ["send-keys"]]
    capsys.readouterr()
    assert main(["mail", "wait", "--timeout", "0"]) == 0
    assert str(room.resolve()) in capsys.readouterr().out


def test_kill_resume_and_peek_refuse_a_joined_lead(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fleet(repo, _seat("lead", state="killed", window=""), _seat("implementer-1", window="@2"))
    _join(repo, capsys)
    for argv in (["seat", "kill", "lead"], ["seat", "resume", "lead"], ["seat", "peek", "lead"]):
        assert main(argv) == 1
        assert capsys.readouterr().err == "foil: lead is joined. It has no pane.\n"
    assert main(["seat", "spawn", "lead"]) == 1
    assert capsys.readouterr().err == "foil: lead is joined. Run foil seat leave first\n"
    assert main(["seat", "kill", "--all"]) == 0
    seats = load_registry(repo)["seats"]
    assert seats["lead"]["state"] == "joined"
    assert seats["implementer-1"]["state"] == "killed"


def test_leave_needs_a_fresh_handoff(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["seat", "leave"]) == 1
    assert capsys.readouterr().err == "foil: no joined lead\n"
    _handoff(repo)
    old = time.time() - 600
    os.utime(_board(repo) / "handoffs" / "lead.md", (old, old))
    _join(repo, capsys)
    assert main(["seat", "leave"]) == 1
    assert capsys.readouterr().err == "foil: write board/handoffs/lead.md first\n"
    _handoff(repo)
    assert main(["seat", "leave"]) == 0
    assert load_registry(repo)["seats"]["lead"]["state"] == "killed"
    assert main(["send", "lead", "queued"]) == 0
    capsys.readouterr()
    brief = _join(repo, capsys)
    assert "New for lead (1):" in brief


def test_mail_wait_prints_new_items_oldest_first_and_marks_them_seen(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fleet(repo, _seat("lead", state="killed", window=""), _seat("implementer-1", window="@2"))
    _join(repo, capsys)
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["send", "lead", "first"]) == 0
    assert main(["send", "lead", "second"]) == 0
    monkeypatch.delenv("FOIL_SEAT_ID")
    capsys.readouterr()
    assert main(["mail", "wait", "--timeout", "5"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines == sorted(lines)
    seen = json.loads((_run(repo) / "seats" / "lead.json").read_text(encoding="utf-8"))
    assert len([item for item in seen["seen"] if item.startswith("mail/lead/")]) == 2
    assert main(["mail", "wait", "--timeout", "0"]) == 0
    assert main(["mail", "wait", "--timeout", "0"]) == 0
    out = capsys.readouterr().out
    assert out == "no new mail\nno new mail (2 quiet waits in a row)\n"


def test_mail_wait_skips_files_send_could_not_have_written(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _join(repo, capsys)
    mailbox = _board(repo) / "mail" / "lead"
    mailbox.mkdir(parents=True, exist_ok=True)
    body = "---\ncontract: mail/v1\nfrom: x\nto: lead\ntime: 2026-11-09T00:00:00Z\n---\n\nhi\n"
    (mailbox / "notes.md").write_text(body, encoding="utf-8")
    target = mailbox.parent / "elsewhere.md"
    target.write_text(body, encoding="utf-8")
    (mailbox / "20261109T000000Z-x-0123abcd.md").symlink_to(target)
    assert main(["mail", "wait", "--timeout", "0"]) == 0
    assert capsys.readouterr().out == "no new mail\n"


def test_mail_wait_runs_wake_with_the_senders_and_paths(
    repo: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _fleet(repo, _seat("lead", state="killed", window=""), _seat("implementer-1", window="@2"))
    _join(repo, capsys)
    out = tmp_path / "wake.txt"
    os.environ["FOIL_SEAT_ID"] = "implementer-1"
    try:
        assert main(["send", "lead", "a secret body"]) == 0
    finally:
        del os.environ["FOIL_SEAT_ID"]
    capsys.readouterr()
    command = f'printf "%s" "$FOIL_WAKE_TEXT" > {out}; exit 3'
    assert main(["mail", "wait", "--timeout", "0", "--wake", command]) == 0
    text = out.read_text(encoding="utf-8")
    assert "implementer-1" in text
    assert str((_board(repo) / "mail" / "lead").resolve()) in text
    assert "secret" not in text
    out.unlink()
    assert main(["mail", "wait", "--timeout", "0", "--wake", command]) == 0
    assert not out.exists()


def test_mail_wait_is_outside_the_fleet_only(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["mail", "wait", "--timeout", "0"]) == 1
    assert capsys.readouterr().err == "foil: mail wait is outside the fleet only\n"


def test_mail_wait_without_a_join_waits_on_the_users_mailbox(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fleet(repo, _seat("lead", window="@1"))
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["send", "user", "done"]) == 0
    monkeypatch.delenv("FOIL_SEAT_ID")
    capsys.readouterr()
    assert main(["mail", "wait", "--timeout", "0"]) == 0
    assert " lead " in capsys.readouterr().out
    assert (_run(repo) / "seats" / "user.json").is_file()


def test_a_dead_seat_becomes_mail_from_foil(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    states = {"implementer-1": "alive", "reviewer-1": "alive", "reviewer-2": "alive"}
    monkeypatch.setattr("foil.joined._states_now", lambda root: dict(states))
    _join(repo, capsys)
    states.update({"implementer-1": "dead", "reviewer-1": "killed"})
    del states["reviewer-2"]
    seen = _run(repo) / "seats" / "lead.json"
    record = json.loads(seen.read_text(encoding="utf-8"))
    record["checked"] = 0
    seen.write_text(json.dumps(record), encoding="utf-8")
    assert main(["mail", "wait", "--timeout", "0"]) == 0
    out = capsys.readouterr().out
    assert " foil " in out
    [mail] = (_board(repo) / "mail" / "lead").glob("*-foil-*.md")
    assert "implementer-1 died. See foil seat peek implementer-1 --lines 200." in mail.read_text(
        encoding="utf-8"
    )


def test_other_commands_end_with_the_new_items_line(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fleet(repo, _seat("lead", state="killed", window=""), _seat("implementer-1", window="@2"))
    _join(repo, capsys)
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["send", "lead", "ready"]) == 0
    assert capsys.readouterr().err == ""
    monkeypatch.delenv("FOIL_SEAT_ID")
    line = "foil: 1 new for lead. Run foil mail wait --timeout 0.\n"
    assert main(["seat", "list", "--json"]) == 0
    captured = capsys.readouterr()
    json.loads(captured.out)
    assert captured.err == line
    assert main(["seat", "peek", "lead"]) == 1
    assert capsys.readouterr().err == "foil: lead is joined. It has no pane.\n" + line
    assert main(["seat", "join", "lead", "--take"]) == 0
    assert capsys.readouterr().err == ""
    assert main(["seat", "list"]) == 0
    assert capsys.readouterr().err == ""
