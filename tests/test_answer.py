"""foil answer: mail from user to the lead, quoting the question id."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root
from tests.test_mail_board_ops import _repo
from tests.test_room_post import _fleet, _seat

_STATUS = """---
contract: status/v1
state: blocked
questions:
  - id: reading
    ask: Which reading?
    options: [keep, drop]
    recommended: keep
    why: The note says keep.
    evidence: board/status.md
    owner: human
  - id: theirs
    ask: For the master?
    options: [later]
    owner: master
---
## Mandate
Done when: the reading is chosen.
"""


def _question(repo: Path) -> None:
    _fleet(repo, _seat("lead", window="@1"))
    board = foil_root(repo) / "fleets" / "main" / "board"
    (board / "status.md").write_text(_STATUS, encoding="utf-8")


def _lead_mail(repo: Path) -> list[Path]:
    folder = foil_root(repo) / "fleets" / "main" / "board" / "mail" / "lead"
    return sorted(folder.glob("*.md")) if folder.is_dir() else []


def test_answer_quotes_the_question_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _question(repo)
    assert main(["answer", "reading", "keep"]) == 0
    assert capsys.readouterr().out == ""
    files = _lead_mail(repo)
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "from: user" in text
    assert "to: lead" in text
    assert text.split("---", 2)[2].lstrip("\n").startswith("question: reading\n")
    assert "keep" in text

    capsys.readouterr()
    assert main(["answer", "missing", "keep"]) == 1
    assert "foil: unknown question 'missing'" in capsys.readouterr().err
    assert main(["answer", "theirs", "later"]) == 1
    assert capsys.readouterr().err == "foil: that question is for the master\n"
    assert main(["answer", "reading"]) == 1
    assert capsys.readouterr().err == "foil: answer text is empty\n"
    assert main(["answer", "reading", "drop"]) == 0
    assert capsys.readouterr().out == ""
    files = _lead_mail(repo)
    assert len(files) == 1
    assert "keep" in files[0].read_text(encoding="utf-8")
    assert "drop" not in files[0].read_text(encoding="utf-8")


def test_a_fifo_named_like_mail_does_not_block_an_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _question(repo)
    lead = foil_root(repo) / "fleets" / "main" / "board" / "mail" / "lead"
    lead.mkdir(parents=True)
    os.mkfifo(lead / "20990101T000000Z-user-abcd1234.md")
    assert main(["answer", "reading", "keep"]) == 0
    capsys.readouterr()
    files = [path for path in lead.iterdir() if path.is_file()]
    assert len(files) == 1
    assert "keep" in files[0].read_text(encoding="utf-8")


def test_a_mailbox_that_cannot_be_trusted_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _question(repo)
    board = foil_root(repo) / "fleets" / "main" / "board"
    lead = board / "mail" / "lead"
    lead.mkdir(parents=True)
    bad = lead / "20990101T000000Z-user-abcd1234.md"
    bad.write_bytes(b"\xff")
    assert main(["answer", "reading", "keep"]) == 1
    assert capsys.readouterr().err == "foil: mail cannot be read\n"
    assert list(lead.glob("*.md")) == [bad]
    assert b"keep" not in bad.read_bytes()

    bad.unlink()
    mail = board / "mail"
    outside = tmp_path / "outside"
    outside.mkdir()
    mail.rename(tmp_path / "mail-real")
    mail.symlink_to(outside)
    assert main(["answer", "reading", "keep"]) == 1
    assert capsys.readouterr().err == "foil: refusing symlink\n"
    assert list(outside.rglob("*.md")) == []


def test_the_master_does_not_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _question(repo)
    monkeypatch.setenv("FOIL_MASTER", "1")
    assert main(["answer", "--as", "user", "reading", "keep"]) == 1
    assert capsys.readouterr().err == "foil: the master does not answer\n"
    assert _lead_mail(repo) == []
    monkeypatch.delenv("FOIL_MASTER")
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["answer", "reading", "keep"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"
    assert _lead_mail(repo) == []


def test_a_joined_lead_must_pass_as_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _question(repo)
    seat = _seat("lead", window="")
    seat["state"] = "joined"
    _fleet(repo, seat)
    assert main(["answer", "reading", "keep"]) == 1
    assert capsys.readouterr().err == "foil: answer is from user\n"
    assert _lead_mail(repo) == []
    assert os.environ.get("FOIL_SEAT_ID", "") == ""
    assert main(["answer", "--as", "user", "reading", "keep"]) == 0
    assert len(_lead_mail(repo)) == 1
