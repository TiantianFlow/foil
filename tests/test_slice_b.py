"""Slice B: mail files, notes, and project memory."""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root
from foil.store import SCHEMA_VERSION


@pytest.fixture(autouse=True)
def _outside_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)


def _git(path: Path) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "Foil Test",
            "GIT_AUTHOR_EMAIL": "foil-test@localhost",
            "GIT_COMMITTER_NAME": "Foil Test",
            "GIT_COMMITTER_EMAIL": "foil-test@localhost",
        }
    )
    environment.pop("FOIL_SEAT_ID", None)
    subprocess.run(
        ["git", "init", "--quiet", str(path)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo)
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0
    return repo


def _seed(repo: Path, name: str, *, window_id: str = "") -> None:
    path = foil_root(repo) / "run" / "registry.json"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "fleet_id": "fleet",
        "tmux_session": "",
        "lead": "lead",
        "seats": {
            name: {
                "name": name,
                "template": name,
                "harness": "fake",
                "window_id": window_id,
                "state": "dead",
                "worktree": "",
                "branch": "",
                "session_id": "",
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _mail(repo: Path, seat: str) -> list[Path]:
    directory = foil_root(repo) / "board" / "mail" / seat
    return sorted(directory.glob("*.md")) if directory.is_dir() else []


def test_init_creates_registry_and_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root = foil_root(repo)
    registry = root / "run" / "registry.json"
    assert registry.is_file()
    assert stat.S_IMODE(registry.stat().st_mode) == 0o600
    assert json.loads(registry.read_text(encoding="utf-8"))["schema_version"] == 1
    notes = root / "board" / "notes"
    assert notes.is_dir()
    note = notes / "plain.txt"
    note.write_text("ordinary\n", encoding="utf-8")
    assert note.read_text(encoding="utf-8") == "ordinary\n"


def test_send_writes_mail_from_user_and_skips_nudge_without_a_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _seed(repo, "lead")
    nudged: list[str] = []
    monkeypatch.setattr("foil.board.nudge", lambda *args: nudged.append(args[0]))

    assert main(["send", "lead", "hello"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert nudged == []
    files = _mail(repo, "lead")
    assert len(files) == 1
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert files[0].name[15:22] == "Z-user-"
    text = files[0].read_text(encoding="utf-8")
    assert text.startswith("---\ncontract: mail/v1\nfrom: user\nto: lead\n")
    assert "\n---\n\nhello\n" in text
    assert "re:" not in text


def test_send_from_seat_nudges_only_when_a_window_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _seed(repo, "implementer", window_id="@12")
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    nudged: list[tuple[str, str, Path]] = []

    def record(
        fleet_id: str,
        seat: str,
        session: str,
        window_id: str,
        sender: str,
        mail_path: Path,
    ) -> None:
        del fleet_id, seat, session
        nudged.append((window_id, sender, mail_path))

    monkeypatch.setattr("foil.board.nudge", record)
    assert main(["send", "implementer", "from the lead"]) == 0
    assert len(nudged) == 1
    assert nudged[0][0] == "@12"
    assert nudged[0][1] == "lead"
    text = nudged[0][2].read_text(encoding="utf-8")
    assert "from: lead\n" in text
    assert text.endswith("from the lead\n")


def test_send_stdin_unknown_seat_and_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _seed(repo, "lead")
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin\n"))
    assert main(["send", "lead", "-"]) == 0
    assert _mail(repo, "lead")[0].read_text(encoding="utf-8").endswith("from stdin\n")

    assert main(["send", "operator", "hello"]) == 1
    assert capsys.readouterr().err == "foil: unknown seat 'operator'\n"
    assert main(["send", "../notes", "x"]) == 1
    assert "Traceback" not in capsys.readouterr().err
    assert list(repo.rglob("x")) == []

    assert main(["send", "lead", "sk-secret"]) == 1
    assert capsys.readouterr().err == "foil: credential-shaped text refused\n"
    assert len(_mail(repo, "lead")) == 1


def test_memory_add_accept_reject_list_and_replaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer")
    assert main(["memory", "add", "prefer small diffs"]) == 0
    lesson_id = capsys.readouterr().out.strip().splitlines()[-1]
    path = foil_root(repo) / "memory" / f"{lesson_id}.json"
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "fleets" not in path.parts

    assert main(["memory", "list"]) == 0
    assert capsys.readouterr().out == ""
    assert main(["memory", "accept", lesson_id]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"
    assert main(["memory", "reject", lesson_id]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"

    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["memory", "accept", lesson_id]) == 0
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["state"] == "accepted"
    assert stored["proposer"] == "implementer"
    assert stored["reviewer"] == "lead"

    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    assert main(["memory", "add", "newer rule", "--replaces", lesson_id]) == 0
    replacement = capsys.readouterr().out.strip()
    assert main(["memory", "accept", replacement]) == 0
    replaced = json.loads(path.read_text(encoding="utf-8"))
    assert replaced["state"] == "superseded"
    successor = json.loads((foil_root(repo) / "memory" / f"{replacement}.json").read_text())
    assert successor["state"] == "accepted"
    assert successor["replaces"] == lesson_id
    assert successor["reviewer"] == "user"

    assert main(["memory", "add", "dropped rule"]) == 0
    dropped = capsys.readouterr().out.strip()
    assert main(["memory", "reject", dropped]) == 0

    assert main(["memory", "list", "--json"]) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in accepted] == [replacement]

    assert main(["memory", "list", "--all"]) == 0
    text = capsys.readouterr().out
    assert f"{lesson_id}\tsuperseded\t" in text
    assert f"{replacement}\taccepted\t" in text
    assert f"{dropped}\trejected\t" in text


def test_memory_add_refuses_symlinked_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    victim = tmp_path / "victim"
    victim.write_text("keep\n", encoding="utf-8")
    victim.chmod(0o644)
    lock = foil_root(repo) / "run" / "memory.lock"
    lock.symlink_to(victim)

    assert main(["memory", "add", "prefer small diffs"]) == 1
    assert capsys.readouterr().err == "foil: refusing symlink\n"
    assert lock.is_symlink()
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert list((foil_root(repo) / "memory").glob("*.json")) == []


def test_memory_id_errors_stay_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(tmp_path, monkeypatch)
    for bad in ("bad\nid", "bad\rid"):
        shown = bad.replace("\n", "").replace("\r", "")
        assert main(["memory", "accept", bad]) == 1
        assert capsys.readouterr().err == f"foil: unknown lesson '{shown}'\n"
        assert main(["memory", "reject", bad]) == 1
        assert capsys.readouterr().err == f"foil: unknown lesson '{shown}'\n"
        assert main(["memory", "add", "newer rule", "--replaces", bad]) == 1
        assert capsys.readouterr().err == f"foil: unknown lesson '{shown}'\n"


def test_memory_refuses_credentials_and_reads_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    assert main(["memory", "add", "ghp_canary"]) == 1
    assert capsys.readouterr().err == "foil: credential-shaped text refused\n"
    assert list((foil_root(repo) / "memory").glob("*.json")) == []

    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin\n"))
    assert main(["memory", "add", "-"]) == 0
    lesson_id = capsys.readouterr().out.strip()
    body = json.loads((foil_root(repo) / "memory" / f"{lesson_id}.json").read_text())
    assert body["text"] == "from stdin\n"
    assert body["proposer"] == "user"
