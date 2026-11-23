"""Selected lines go to the seat that owns them, through the mail path."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from foil_bridge.evidence import page
from foil_bridge.pages import evidence_html

from foil.cli import main
from foil.delivery import pending_pointers
from foil.project import foil_root
from foil.store import fleet_dir
from tests.test_bridge_evidence import _registry, _seat
from tests.test_bridge_home import _key, _short_repo
from tests.test_bridge_http import _origin, _request, _serving
from tests.test_init import _identity_environ

TASK = """---
contract: task/v1
id: login
owner: implementer-1
state: open
acceptance: the test passes
---

alpha
beta
gamma
"""

RESULT = """---
contract: result/v1
task: login
author: reviewer-1
outcome: pass
---

done
"""

STATUS = """---
contract: status/v1
state: working
decisions:
  - Keep   the   short timeout.
  - Ship the branch.
---
"""


def _git(repo: Path, *args: str) -> None:
    import subprocess

    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_identity_environ(),
    )


def _board(repo: Path) -> Path:
    return fleet_dir(repo) / "board"


def _people(repo: Path, **states: tuple[str, str]) -> None:
    seats = {}
    for name, (branch, state) in states.items():
        row = _seat(name, branch)
        row["state"] = state
        seats[name] = row
    _registry(repo, "main", seats)


def _write(repo: Path, relative: str, text: str) -> None:
    path = _board(repo) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _mail(repo: Path, seat: str) -> str:
    folder = _board(repo) / "mail" / seat
    files = sorted(folder.glob("*.md"))
    assert len(files) == 1
    return files[0].read_text(encoding="utf-8")


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    with _short_repo(monkeypatch) as path:
        _people(
            path,
            lead=("main", "idle"),
            **{"implementer-1": ("", "idle"), "reviewer-1": ("", "idle")},
        )
        _write(path, "tasks/login.md", TASK)
        _write(path, "results/login.md", RESULT)
        _write(path, "notes/scratch.md", "a note\n")
        _write(path, "status.md", STATUS)
        yield path


def test_a_task_file_goes_to_its_owner_with_the_pointer(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["quote", "tasks/login.md", "--lines", "10-11", "Fix the check."]) == 0
    body = _mail(repo, "implementer-1")
    assert "path: tasks/login.md" in body
    assert "lines: 10-11" in body
    assert "10|beta" in body
    assert "11|gamma" in body
    assert "Fix the check." in body
    pending = pending_pointers(fleet_dir(repo))
    assert [item["to"] for item in pending] == ["implementer-1"]
    assert not (_board(repo) / "mail" / "lead").exists()
    assert main(["mail", "list", "--sent"]) == 0
    assert "queued" in capsys.readouterr().out
    # The owner is in the front matter, past the quoted lines.
    assert main(["quote", "tasks/login.md", "--lines", "1-2", "The header."]) == 0
    assert len(list((_board(repo) / "mail" / "implementer-1").glob("*.md"))) == 2


def test_a_result_goes_to_its_author(repo: Path) -> None:
    assert main(["quote", "results/login.md", "--lines", "8", "Say why."]) == 0
    body = _mail(repo, "reviewer-1")
    assert "path: results/login.md" in body
    assert "lines: 8-8" in body
    assert "8|done" in body
    assert [item["to"] for item in pending_pointers(fleet_dir(repo))] == ["reviewer-1"]


def test_a_note_and_a_decision_go_to_the_lead(repo: Path) -> None:
    assert main(["quote", "notes/scratch.md", "--lines", "1", "Drop this."]) == 0
    assert main(["quote", "status.md#decisions", "--lines", "1-2", "Revisit both."]) == 0
    folder = _board(repo) / "mail" / "lead"
    texts = [path.read_text(encoding="utf-8") for path in sorted(folder.glob("*.md"))]
    assert any("path: notes/scratch.md" in text for text in texts)
    decision = next(text for text in texts if "status.md#decisions" in text)
    assert "lines: 1-2" in decision
    assert "1|Keep the short timeout." in decision
    assert "2|Ship the branch." in decision
    assert pending_pointers(fleet_dir(repo)) == []


def test_a_branch_diff_goes_to_that_branch_seat(repo: Path) -> None:
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    _git(repo, "checkout", "-b", "topic")
    (repo / "base.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "commit", "-am", "change")
    _git(repo, "checkout", "main")
    _people(
        repo,
        lead=("main", "idle"),
        **{"implementer-1": ("topic", "idle"), "implementer-2": ("topic", "idle")},
    )
    assert main(["quote", "branch:topic", "--lines", "1-3", "This hunk."]) == 0
    body = _mail(repo, "implementer-1")
    assert "path: branch:topic" in body
    assert "lines: 1-3" in body
    assert "diff --git" in body
    assert [item["to"] for item in pending_pointers(fleet_dir(repo))] == ["implementer-1"]


def test_a_branch_the_lead_also_records_uses_the_checkout(
    repo: Path,
) -> None:
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    _git(repo, "checkout", "-b", "topic")
    (repo / "base.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "commit", "-am", "change")
    _git(repo, "checkout", "main")
    _people(
        repo,
        lead=("topic", "idle"),
        **{"implementer-1": ("topic", "idle")},
    )
    assert main(["quote", "branch:topic", "--lines", "1-3", "The lead page."]) == 0
    body = _mail(repo, "implementer-1")
    assert "path: branch:topic" in body
    assert "diff --git" in body


def test_a_live_seat_wins_a_branch_tie(repo: Path) -> None:
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    _git(repo, "checkout", "-b", "topic")
    (repo / "base.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "commit", "-am", "change")
    _git(repo, "checkout", "main")
    _people(
        repo,
        lead=("main", "idle"),
        **{"implementer-1": ("topic", "killed"), "implementer-2": ("topic", "idle")},
    )
    assert main(["quote", "branch:topic", "--lines", "1", "One line."]) == 0
    assert (_board(repo) / "mail" / "implementer-2").is_dir()
    assert not (_board(repo) / "mail" / "implementer-1").exists()


def test_an_unknown_owner_falls_back_to_the_lead(repo: Path) -> None:
    text = TASK.replace("owner: implementer-1", "owner: nobody")
    _write(repo, "tasks/login.md", text)
    assert main(["quote", "tasks/login.md", "--lines", "10", "Who owns this?"]) == 0
    assert "path: tasks/login.md" in _mail(repo, "lead")
    assert pending_pointers(fleet_dir(repo)) == []


def test_a_killed_owner_still_receives_the_mail(repo: Path) -> None:
    _people(repo, lead=("main", "idle"), **{"implementer-1": ("", "killed")})
    assert main(["quote", "tasks/login.md", "--lines", "10", "Still yours."]) == 0
    assert "Still yours." in _mail(repo, "implementer-1")
    assert [item["to"] for item in pending_pointers(fleet_dir(repo))] == ["implementer-1"]


def test_refusals_write_nothing(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = _board(repo) / "notes" / "real.md"
    real.write_text("hello\n", encoding="utf-8")
    (_board(repo) / "notes" / "link.md").symlink_to(real)
    secret = TASK + "sk-live-token\n"
    _write(repo, "tasks/login.md", secret)
    cases = [
        (["quote", "tasks/login.md", "--lines", "0", "No."], "foil: --lines is N or N-M"),
        (["quote", "tasks/login.md", "--lines", "3-1", "No."], "foil: --lines is N or N-M"),
        (
            ["quote", "tasks/login.md", "--lines", "40", "No."],
            "foil: line range is outside the file",
        ),
        (["quote", "tasks/missing.md", "--lines", "1", "No."], "foil: file not found:"),
        (["quote", "notes/link.md", "--lines", "1", "No."], "foil: refusing symlink"),
        (["quote", "tasks/login.md", "--lines", "10", "   "], "foil: quote text is empty"),
        (
            ["quote", "tasks/login.md", "--lines", "12", "Look."],
            "foil: credential-shaped text refused",
        ),
        (["quote", "branch:no-such", "--lines", "1", "No."], "foil: no diff"),
    ]
    for argv, expected in cases:
        assert main(argv) == 1
        assert expected in capsys.readouterr().err
    assert list((_board(repo) / "mail").glob("*/*.md")) == []


def test_stdin_and_a_pinned_user_while_a_lead_is_joined(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("From stdin.\n"))
    assert main(["quote", "tasks/login.md", "--lines", "10", "-"]) == 0
    assert "From stdin." in _mail(repo, "implementer-1")
    opened = pending_pointers(fleet_dir(repo))
    assert [item["to"] for item in opened] == ["implementer-1"]
    _people(repo, lead=("main", "joined"), **{"implementer-1": ("", "idle")})
    _write(repo, "tasks/login.md", TASK)
    assert main(["quote", "tasks/login.md", "--lines", "10", "As the lead."]) == 0
    sent = [
        path.read_text(encoding="utf-8")
        for path in (_board(repo) / "mail" / "implementer-1").glob("*.md")
    ]
    assert any("from: lead" in text and "As the lead." in text for text in sent)
    assert pending_pointers(fleet_dir(repo)) == opened
    assert main(["quote", "--as", "user", "tasks/login.md", "--lines", "10", "As you."]) == 0
    texts = [
        path.read_text(encoding="utf-8")
        for path in (_board(repo) / "mail" / "implementer-1").glob("*.md")
    ]
    assert any("from: user" in text and "As you." in text for text in texts)
    assert [item["to"] for item in pending_pointers(fleet_dir(repo))] == [
        "implementer-1",
        "implementer-1",
    ]


def test_a_worker_quote_does_not_write_the_pointer(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOIL_SEAT_ID", "reviewer-1")
    assert main(["quote", "tasks/login.md", "--lines", "10", "From a seat."]) == 0
    assert "from: reviewer-1" in _mail(repo, "implementer-1")
    assert pending_pointers(fleet_dir(repo)) == []


def test_the_page_numbers_a_file_a_decision_and_a_diff(repo: Path) -> None:
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    _git(repo, "checkout", "-b", "topic")
    (repo / "base.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "commit", "-am", "change")
    _git(repo, "checkout", "main")
    _people(repo, lead=("main", "idle"), **{"implementer-1": ("topic", "idle")})
    _write(repo, "tasks/login.md", TASK)
    _write(repo, "status.md", STATUS)
    key = _key(repo)
    file_page = evidence_html(
        page(f"projects/{key}/fleets/main/board/tasks/login.md"), "token"
    ).decode()
    fleet_page = evidence_html(page(f"projects/{key}/fleets/main"), "token").decode()
    seat_page = evidence_html(
        page(f"projects/{key}/fleets/main/seats/implementer-1"), "token"
    ).decode()
    assert 'data-path="tasks/login.md"' in file_page
    assert "quote.js?token=token" in file_page
    assert 'data-path="status.md#decisions"' in fleet_page
    assert "Keep the short timeout." in fleet_page
    assert 'data-path="branch:topic"' in seat_page
    assert "diff --git" in seat_page


def test_the_page_posts_the_quote(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = foil_root(repo).name
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        from foil.bridge_cmd import read_desk_token

        token = read_desk_token()
        status, body = _request(
            port,
            "POST",
            "/v1/quote",
            token=token,
            headers=_origin(port),
            body={
                "project": key,
                "fleet": "main",
                "path": "tasks/login.md",
                "start": 10,
                "end": 11,
                "text": "Fix the check.",
            },
        )
        assert status == 200, body
        assert json.loads(body)["to"] == "implementer-1"
        mailed = _mail(repo, "implementer-1")
        assert "lines: 10-11" in mailed
        assert [item["to"] for item in pending_pointers(fleet_dir(repo))] == ["implementer-1"]
        status, body = _request(
            port,
            "POST",
            "/v1/quote",
            token=token,
            headers=_origin(port),
            body={
                "project": key,
                "fleet": "main",
                "path": "tasks/login.md",
                "start": True,
                "end": 1,
                "text": "No.",
            },
        )
        assert status == 400
        assert b"--lines is N or N-M" in body
        assert len(list((_board(repo) / "mail" / "implementer-1").glob("*.md"))) == 1
