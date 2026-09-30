"""Tests for mail and board read/list operations."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from foil.board_ops import board_list, board_read
from foil.cli import main
from foil.errors import FoilError
from foil.mail_ops import mail_list, mail_read
from foil.project import foil_root


@pytest.fixture(autouse=True)
def _outside_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)


def _identity_environ() -> dict[str, str]:
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
    return environment


def _init_git_repository(path: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(path)],
        check=True,
        capture_output=True,
        text=True,
        env=_identity_environ(),
    )


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(["init"]) == 0
    return repo


def _create_mail(repo: Path, seat: str, from_user: str, body: str) -> Path:
    """Create a test mail file."""
    mail_dir = foil_root(repo) / "board" / "mail" / seat
    mail_dir.mkdir(parents=True, exist_ok=True)
    mail_file = mail_dir / f"20260930T120000Z-{from_user}-test1234.md"
    content = f"""---
contract: mail/v1
from: {from_user}
to: {seat}
time: 2026-09-30T12:00:00Z
---

{body}"""
    mail_file.write_text(content, encoding="utf-8")
    return mail_file


def _create_board_file(repo: Path, rel_path: str, content: str) -> Path:
    """Create a test board file."""
    board_root = foil_root(repo) / "board"
    file_path = board_root / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


# Path validation tests


def test_mail_read_rejects_dotdot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """mail read rejects paths with .."""
    _repo(tmp_path, monkeypatch)
    with pytest.raises(FoilError, match="path must not contain"):
        mail_read("/some/path/../file.md")


def test_mail_read_rejects_outside_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """mail read rejects paths outside .foil/board/mail/."""
    _repo(tmp_path, monkeypatch)
    outside_file = tmp_path / "outside.md"
    outside_file.write_text("test", encoding="utf-8")

    with pytest.raises(FoilError, match="path not inside"):
        mail_read(str(outside_file.resolve()))


def test_board_read_rejects_dotdot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """board read rejects paths with .."""
    _repo(tmp_path, monkeypatch)
    with pytest.raises(FoilError, match="path must not contain"):
        board_read("tasks/../../../etc/passwd")


# mail read tests


def test_mail_read_outputs_body_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """mail read outputs body only in human mode."""
    repo = _repo(tmp_path, monkeypatch)
    mail_file = _create_mail(repo, "worker-1", "lead", "Do the task please.")

    mail_read(str(mail_file.resolve()), as_json=False)
    captured = capsys.readouterr()
    assert captured.out == "Do the task please.\n"
    assert "contract:" not in captured.out
    assert "from:" not in captured.out


def test_mail_read_outputs_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """mail read outputs full contract in JSON mode."""
    repo = _repo(tmp_path, monkeypatch)
    mail_file = _create_mail(repo, "worker-1", "lead", "Do the task.")

    mail_read(str(mail_file.resolve()), as_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data["from"] == "lead"
    assert data["to"] == "worker-1"
    assert data["time"] == "2026-09-30T12:00:00Z"
    assert data["re"] is None
    assert data["body"] == "Do the task."


# mail list tests


def test_mail_list_requires_seat_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mail list fails without FOIL_SEAT_ID."""
    _repo(tmp_path, monkeypatch)
    with pytest.raises(FoilError, match="FOIL_SEAT_ID not set"):
        mail_list()


def test_mail_list_outputs_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """mail list outputs mail for current seat."""
    repo = _repo(tmp_path, monkeypatch)
    mail_file = _create_mail(repo, "worker-1", "lead", "Task 1")

    monkeypatch.setenv("FOIL_SEAT_ID", "worker-1")
    mail_list(as_json=False)
    captured = capsys.readouterr()

    assert "2026-09-30T12:00:00Z" in captured.out
    assert "lead" in captured.out
    assert str(mail_file.resolve()) in captured.out


def test_mail_list_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """mail list outputs JSON array."""
    repo = _repo(tmp_path, monkeypatch)
    _create_mail(repo, "worker-1", "lead", "Task 1")

    monkeypatch.setenv("FOIL_SEAT_ID", "worker-1")
    mail_list(as_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["from"] == "lead"
    assert data[0]["to"] == "worker-1"


def test_mail_list_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """mail list outputs empty list when no mail."""
    _repo(tmp_path, monkeypatch)

    monkeypatch.setenv("FOIL_SEAT_ID", "worker-1")
    mail_list(as_json=True)
    captured = capsys.readouterr()

    assert captured.out.strip() == "[]"


# board read tests


def test_board_read_contract_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """board read outputs full file for contract files."""
    repo = _repo(tmp_path, monkeypatch)
    content = """---
contract: task/v1
id: t1
owner: worker-1
state: open
---

# Task 1

Do the task."""
    _create_board_file(repo, "tasks/t1.md", content)

    board_read("tasks/t1.md", as_json=False)
    captured = capsys.readouterr()
    assert captured.out == content + "\n"


def test_board_read_json_with_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """board read parses frontmatter in JSON mode."""
    repo = _repo(tmp_path, monkeypatch)
    content = """---
contract: task/v1
id: t1
state: open
---

Body text"""
    _create_board_file(repo, "tasks/t1.md", content)

    board_read("tasks/t1.md", as_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data["contract"] == "task/v1"
    assert data["id"] == "t1"
    assert data["state"] == "open"
    assert data["body"] == "Body text"


def test_board_read_json_without_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """board read outputs body-only JSON for non-contract files."""
    repo = _repo(tmp_path, monkeypatch)
    content = "Just a plain note file."
    _create_board_file(repo, "notes/test.md", content)

    board_read("notes/test.md", as_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data == {"body": content}


# board list tests


def test_board_list_glob_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """board list matches glob patterns."""
    repo = _repo(tmp_path, monkeypatch)
    _create_board_file(repo, "tasks/t1.md", "Task 1")
    _create_board_file(repo, "tasks/t2.md", "Task 2")
    _create_board_file(repo, "results/r1.md", "Result 1")

    board_list("tasks/*.md", as_json=False)
    captured = capsys.readouterr()

    assert "tasks/t1.md" in captured.out
    assert "tasks/t2.md" in captured.out
    assert "results/r1.md" not in captured.out


def test_board_list_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """board list outputs JSON with files array."""
    repo = _repo(tmp_path, monkeypatch)
    _create_board_file(repo, "tasks/t1.md", "Task 1")

    board_list("tasks/*.md", as_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert "files" in data
    assert isinstance(data["files"], list)
    assert "tasks/t1.md" in data["files"]


def test_board_list_sorted_alphabetically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """board list sorts results alphabetically."""
    repo = _repo(tmp_path, monkeypatch)
    _create_board_file(repo, "tasks/t3.md", "Task 3")
    _create_board_file(repo, "tasks/t1.md", "Task 1")
    _create_board_file(repo, "tasks/t2.md", "Task 2")

    board_list("tasks/*.md", as_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data["files"] == ["tasks/t1.md", "tasks/t2.md", "tasks/t3.md"]


# CLI integration tests


def test_cli_mail_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """foil mail read command works."""
    repo = _repo(tmp_path, monkeypatch)
    mail_file = _create_mail(repo, "worker-1", "lead", "Test mail")

    assert main(["mail", "read", str(mail_file.resolve())]) == 0
    captured = capsys.readouterr()
    assert "Test mail" in captured.out


def test_cli_mail_list_requires_seat_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """foil mail list fails without FOIL_SEAT_ID."""
    _repo(tmp_path, monkeypatch)

    assert main(["mail", "list"]) == 1
    captured = capsys.readouterr()
    assert "FOIL_SEAT_ID not set" in captured.err


def test_cli_board_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """foil board read command works."""
    repo = _repo(tmp_path, monkeypatch)
    _create_board_file(repo, "notes/test.md", "Test note")

    assert main(["board", "read", "notes/test.md"]) == 0
    captured = capsys.readouterr()
    assert "Test note" in captured.out


def test_cli_board_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """foil board list command works."""
    repo = _repo(tmp_path, monkeypatch)
    _create_board_file(repo, "tasks/t1.md", "Task")

    assert main(["board", "list", "tasks/*.md"]) == 0
    captured = capsys.readouterr()
    assert "tasks/t1.md" in captured.out


# Error message tests (N8 requirement)


def test_error_messages_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Errors output one line to stderr, no tracebacks."""
    _repo(tmp_path, monkeypatch)

    # Test mail read with invalid path
    assert main(["mail", "read", "/nonexistent/path.md"]) == 1
    captured = capsys.readouterr()
    assert captured.err.count("\n") == 1  # One line only
    assert "foil:" in captured.err
    assert "Traceback" not in captured.err

    # Test mail list without seat ID
    assert main(["mail", "list"]) == 1
    captured = capsys.readouterr()
    assert captured.err.count("\n") == 1
    assert "foil:" in captured.err
    assert "Traceback" not in captured.err
