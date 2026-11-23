"""Evidence pages: fleets, one fleet, a board file, and a seat."""

from __future__ import annotations

import json
import os
import subprocess
from html import unescape
from pathlib import Path
from urllib.parse import quote

import pytest
from foil_bridge.evidence import FILE_CAP, _base, _diff, _git_diff, _take, board_listing
from foil_bridge.home import links_in
from foil_bridge.transcript import mask

from foil.bridge_cmd import read_desk_token
from foil.errors import FoilError
from foil.journal import note
from foil.lifecycle import TmuxController
from foil.project import foil_root
from foil.store import fleet_dir
from tests.test_bridge_home import _key, _short_repo
from tests.test_bridge_http import _request, _serving
from tests.test_mail_board_ops import _identity_environ

FAKE = "55555555-5555-4555-8555-555555555555"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_identity_environ(),
    )
    return result.stdout.strip()


def _registry(repo: Path, fleet: str, seats: dict[str, dict[str, str]]) -> None:
    directory = foil_root(repo) / "fleets" / fleet
    (directory / "run").mkdir(parents=True, exist_ok=True)
    (directory / "board").mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "fleet_id": fleet,
        "tmux_session": "s",
        "lead": "lead",
        "seats": seats,
        "rotation": {},
    }
    (directory / "run" / "registry.json").write_text(
        json.dumps(payload) + "\n", encoding="utf-8"
    )


def _seat(name: str, branch: str, session: str = "") -> dict[str, str]:
    return {
        "name": name,
        "template": "implementer" if name != "lead" else "lead",
        "harness": "fake",
        "model": "",
        "window_id": "",
        "state": "idle",
        "worktree": "",
        "branch": branch,
        "session_id": session,
    }


def _status(board: Path, text: str) -> None:
    board.mkdir(parents=True, exist_ok=True)
    (board / "status.md").write_text(text, encoding="utf-8")


def _alive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TmuxController, "matches_window", lambda *args, **kwargs: True)


def _branches(repo: Path) -> str:
    trunk = _git(repo, "symbolic-ref", "--short", "HEAD")
    (repo / "note.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "note.txt")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "foil/goal-main")
    _git(repo, "checkout", "foil/goal-main")
    (repo / "note.txt").write_text("base\ngoal\n", encoding="utf-8")
    _git(repo, "commit", "-am", "goal")
    _git(repo, "checkout", "-b", "foil/worker")
    (repo / "note.txt").write_text("base\ngoal\npassword=hunter2\n", encoding="utf-8")
    _git(repo, "commit", "-am", "worker")
    _git(repo, "checkout", trunk)
    return trunk


def _transcript(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setattr("foil_bridge.transcript.user_home", lambda: home)
    folder = home / ".foil-fake" / "sessions"
    folder.mkdir(parents=True)
    lines = [
        {
            "role": "user",
            "kind": "message",
            "text": "hello from the seat",
            "tool": "",
            "usage": None,
        },
        {"role": "tool", "kind": "tool", "text": "ran true", "tool": "shell", "usage": None},
        {
            "role": "assistant",
            "kind": "usage",
            "text": "",
            "tool": "",
            "usage": {"input": 4, "output": 5},
        },
        {
            "role": "assistant",
            "kind": "message",
            "text": "password=hunter2",
            "tool": "",
            "usage": None,
        },
    ]
    (folder / f"{FAKE}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )

    def _peek(_root: Path, name: str, *, lines: int, fleet: str | None = None) -> str:
        if name == "worker":
            return "pane says hello\n"
        raise FoilError(f"foil: no transcript for '{name}'")

    monkeypatch.setattr("foil_bridge.transcript.peek_text", _peek)


def _open(port: int, token: str, target: str, level: str = "") -> tuple[int, bytes]:
    query = "target=" + quote(target, safe="/")
    if level:
        query += "&level=" + quote(level)
    return _request(port, "GET", "/evidence?" + query, token=token)


def test_pages_list_fleets_a_fleet_a_file_and_a_seat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _alive(monkeypatch)
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_DIR", "GROK_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    with _short_repo(monkeypatch) as repo:
        trunk = _branches(repo)
        key = _key(repo)
        main = foil_root(repo) / "fleets" / "main" / "board"
        _registry(
            repo,
            "main",
            {
                "lead": _seat("lead", "foil/goal-main", FAKE),
                "worker": _seat("worker", "foil/worker"),
                "quiet": _seat("quiet", ""),
            },
        )
        _status(
            main,
            "---\n"
            "state: working\n"
            "updated: 2026-11-22T12:00:00Z\n"
            "questions:\n"
            "  - id: q1\n"
            "    ask: Ship it?\n"
            "    options: [yes, no]\n"
            "    recommended: yes\n"
            "    why: The tests pass.\n"
            "    evidence: board/status.md\n"
            "    owner: human\n"
            "decisions:\n"
            "  - Keep the branch. Evidence: board/status.md. Undo: revert.\n"
            "  - password=hunter2\n"
            "---\n"
            "\n"
            "## Mandate\n"
            "\n"
            "Done when: the tests pass.\n",
        )
        note(
            fleet_dir(repo, "main"),
            "decision",
            by="lead",
            text="Keep the branch",
            why="the tests pass",
        )
        side = foil_root(repo) / "fleets" / "side" / "board"
        _registry(repo, "side", {})
        _status(
            side,
            "---\nstate: done\nupdated: 2026-11-22T12:00:00Z\n---\n\n"
            "## Mandate\n\nDone when: later.\n",
        )
        _transcript(monkeypatch, tmp_path / "home")
        seat = f"projects/{key}/fleets/main/seats/lead"
        assert links_in(f"See {seat}.")[0]["kind"] == "seat"

        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, body = _open(port, token, f"projects/{key}")
            assert status == 200
            assert b"rank 0 question: Ship it?" in body
            assert b"rank 4 done:" in body
            assert b"Secrets are masked as a best effort." in body

            status, body = _open(port, token, f"projects/{key}/fleets/main")
            text = body.decode()
            assert status == 200
            assert "the tests pass" in text
            assert "working" in text
            assert "Keep the branch. Evidence: board/status.md. Undo: revert." in text
            assert "password=[hidden]" in text
            assert "hunter2" not in text
            assert "decision" in text
            assert "Ship it?" in text
            assert "lead" in text
            assert "in 4 out 5" in text
            assert "status.md" in text

            status, body = _open(port, token, f"projects/{key}/fleets/main/board/status.md")
            assert status == 200
            assert b"hunter2" not in body
            assert b"password=[hidden]" in body
            assert b"truncated" not in body

            status, body = _open(port, token, seat)
            text = body.decode()
            assert status == 200
            assert "hello from the seat" in text
            assert "tool:" not in text
            assert "password=[hidden]" in text
            assert "hunter2" not in text
            assert "in 4 out 5" in text
            assert f"{trunk}...foil/goal-main" in text
            assert "goal" in text
            assert "Live pane." not in text

            status, tools = _open(port, token, seat, "tools")
            assert status == 200
            assert b"tool: shell" in tools
            assert b"ran true" not in tools
            assert b"in 4 out 5" in tools

            status, everything = _open(port, token, seat, "all")
            assert b"tool: shell: ran true" in everything
            assert b"usage: in 4 out 5" in everything

            status, body = _open(port, token, seat, "nope")
            assert status == 200
            assert b"tool:" not in body

            status, body = _open(port, token, f"projects/{key}/fleets/main/seats/worker")
            text = body.decode()
            assert "Live pane." in text
            assert "pane says hello" in text
            assert "foil/goal-main...foil/worker" in text
            assert "hunter2" not in text
            assert "password=[hidden]" in text

            status, body = _open(port, token, f"projects/{key}/fleets/main/seats/quiet")
            text = unescape(body.decode())
            assert "foil-bridge: no transcript for 'quiet'" in text
            assert "This seat has no branch." in text
            assert "in 0 out 0" in text

            status, body = _open(port, token, f"projects/{key}/fleets/main/board/missing.md")
            assert status == 404 and body == b""
            (main / "alias.md").symlink_to(main / "status.md")
            (main / ".secret").write_text("password=hunter2\n", encoding="utf-8")
            status, body = _open(port, token, f"projects/{key}/fleets/main/board/alias.md")
            assert status == 404 and body == b""
            status, body = _open(port, token, f"projects/{key}/fleets/main/board/.secret")
            assert status == 404 and b"hunter2" not in body
            status, body = _open(port, token, f"projects/{key}/fleets/main/seats/nobody")
            assert status == 404 and body == b""


def test_a_long_diff_is_cut_after_masking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("foil_bridge.evidence.DIFF_CAP", 24)

    def _git(_checkout: Path, args: list[str]) -> tuple[bytes, bool]:
        assert args[0] == "diff"
        assert "--no-textconv" in args
        return b"password=hunter2\n" + (b"a" * 80), True

    monkeypatch.setattr("foil_bridge.evidence._git_diff", _git)
    diff = _diff(Path("."), "foil/worker", "main")
    assert diff["truncated"] is True
    assert diff["note"] == "truncated"
    assert "hunter2" not in diff["text"]
    assert diff["text"].startswith("password=[hidden]")


def test_a_seat_on_the_lead_branch_diffs_against_that_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("foil_bridge.evidence._ref_exists", lambda *_args: True)
    monkeypatch.setattr("foil_bridge.evidence._head", lambda *_args: "main")
    assert _base(Path("."), "worker", "foil/goal-main", "foil/goal-main") == "foil/goal-main"


def test_a_secret_that_crosses_the_file_cut_is_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        board = foil_root(repo) / "fleets" / "main" / "board"
        _registry(repo, "main", {"lead": _seat("lead", "")})
        _status(board, "---\nstate: working\n---\n\n## Mandate\n\nDone when: stay.\n")
        prefix = b"x" * (FILE_CAP - len(b"https://user:"))
        (board / "notes.md").write_bytes(
            prefix + b"https://user:hunter2@example.test/repo\n"
        )
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, body = _open(port, token, f"projects/{key}/fleets/main/board/notes.md")
        text = body.decode()
        assert status == 200
        assert "hunter2" not in text
        assert "user:hunter2" not in text
        assert "[hidd" in text


def test_a_linked_board_and_journal_stay_on_the_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        fleet = foil_root(repo) / "fleets" / "main"
        board = fleet / "board"
        _registry(repo, "main", {"lead": _seat("lead", "")})
        _status(board, "---\nstate: working\n---\n\n## Mandate\n\nDone when: the tests pass.\n")
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        (foreign / "status.md").write_text(
            "---\nstate: done\n---\n\n## Mandate\n\nDone when: foreign goal.\n",
            encoding="utf-8",
        )
        (tmp_path / "journal.log").write_text("password=hunter2\n", encoding="utf-8")
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            target = f"projects/{key}/fleets/main"
            status, body = _open(port, token, target)
            assert status == 200
            assert b"the tests pass" in body

            saved = tmp_path / "board-real"
            board.rename(saved)
            board.symlink_to(foreign)
            status, body = _open(port, token, target)
            assert status == 200
            assert b"foreign goal" not in body
            assert b"the tests pass" not in body
            assert b"lead" in body

            board.unlink()
            saved.rename(board)
            log = fleet / "run" / "journal.log"
            if log.exists() or log.is_symlink():
                log.unlink()
            log.symlink_to(tmp_path / "journal.log")
            status, body = _open(port, token, target)
            text = body.decode()
            assert status == 200
            assert "the tests pass" in text
            assert "hunter2" not in text


def test_a_dotfile_under_a_directory_is_not_a_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        board = foil_root(repo) / "fleets" / "main" / "board"
        _registry(repo, "main", {"lead": _seat("lead", "")})
        _status(board, "---\nstate: working\n---\n\n## Mandate\n\nDone when: stay.\n")
        (board / "notes" / ".secret").write_text("password=hunter2\n", encoding="utf-8")
        found = links_in(
            f"see board/notes/.secret and projects/{key}/fleets/main/board/notes/.secret",
            project=key,
            fleet="main",
        )
        assert found == []
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, body = _open(port, token, f"projects/{key}/fleets/main/board/notes/.secret")
        assert status == 404 and b"hunter2" not in body


def test_a_directory_name_that_is_not_utf8_is_skipped(tmp_path: Path) -> None:
    board = tmp_path / "board"
    board.mkdir()
    (board / "status.md").write_text("status\n", encoding="utf-8")
    bad = os.fsencode(board) + b"/\xff"
    os.mkdir(bad)
    with open(bad + b"/note.md", "w", encoding="utf-8") as handle:
        handle.write("password=hunter2\n")
    assert board_listing(board) == ["status.md"]


def test_a_diff_read_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("foil_bridge.evidence.DIFF_CAP", 64)
    monkeypatch.setattr("foil_bridge.evidence.LINE_BOUND", 16)
    with _short_repo(monkeypatch) as repo:
        _branches(repo)
        blob = repo / "blob.txt"
        blob.write_bytes(b"z" * 200_000)
        _git(repo, "add", "blob.txt")
        _git(repo, "commit", "-m", "blob")
        found = _git_diff(repo, ["diff", "--no-textconv", "HEAD~1", "HEAD"])
        assert found is not None
        data, cut = found
        assert cut is True
        assert len(data) == 64 + 16


def test_take_stops_at_the_limit() -> None:
    import io

    data, cut = _take(io.BytesIO(b"a" * 1000), 10)
    assert data == b"a" * 10
    assert cut is True
    data, cut = _take(io.BytesIO(b"ab"), 10)
    assert data == b"ab"
    assert cut is False


def test_a_symlinked_status_keeps_other_inbox_items(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        fleet = foil_root(repo) / "fleets" / "main"
        board = fleet / "board"
        _registry(repo, "main", {"lead": _seat("lead", "")})
        _status(board, "---\nstate: working\n---\n\n## Mandate\n\nDone when: the tests pass.\n")
        foreign = tmp_path / "foreign.md"
        foreign.write_text(
            "---\nstate: working\n---\n\n## Mandate\n\nDone when: foreign goal.\n",
            encoding="utf-8",
        )
        status = board / "status.md"
        status.unlink()
        status.symlink_to(foreign)
        (fleet / "run" / "fleet.json").write_text(
            '{"paused": true, "told": false}\n', encoding="utf-8"
        )
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status_code, body = _open(port, token, f"projects/{key}")
            text = body.decode()
            assert status_code == 200
            assert "fleet paused" in text
            assert "foreign goal" not in text
            status_code, body = _open(port, token, f"projects/{key}/fleets/main")
            text = body.decode()
            assert status_code == 200
            assert "foreign goal" not in text
            assert "the tests pass" not in text


def test_an_unreadable_board_and_journal_leave_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        fleet = foil_root(repo) / "fleets" / "main"
        board = fleet / "board"
        _registry(repo, "main", {"lead": _seat("lead", "")})
        _status(board, "---\nstate: working\n---\n\n## Mandate\n\nDone when: the tests pass.\n")
        log = fleet / "run" / "journal.log"
        log.write_text('{"time":"2026-01-01T00:00:00Z","event":"note"}\n', encoding="utf-8")
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            target = f"projects/{key}/fleets/main"
            board.chmod(0)
            try:
                status_code, body = _open(port, token, target)
            finally:
                board.chmod(0o755)
            assert status_code == 200
            assert b"lead" in body
            log.chmod(0)
            try:
                status_code, body = _open(port, token, target)
            finally:
                log.chmod(0o644)
            assert status_code == 200
            assert b"the tests pass" in body


def test_a_symlinked_run_is_not_a_fleet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        fleet = foil_root(repo) / "fleets" / "main"
        _registry(repo, "main", {"lead": _seat("lead", "")})
        outside = tmp_path / "outside-run"
        outside.mkdir()
        (outside / "journal.log").write_text("password=hunter2\n", encoding="utf-8")
        (outside / "registry.json").write_text(
            json.dumps({"seats": {"lead": {"name": "lead"}}}) + "\n", encoding="utf-8"
        )
        run = fleet / "run"
        saved = tmp_path / "run-real"
        run.rename(saved)
        run.symlink_to(outside, target_is_directory=True)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status_code, body = _open(port, token, f"projects/{key}/fleets/main")
            assert status_code == 404 and b"hunter2" not in body
            status_code, body = _open(port, token, f"projects/{key}/fleets/main/seats/lead")
            assert status_code == 404 and b"hunter2" not in body
        assert links_in(f"projects/{key}/fleets/main") == []


def test_a_linked_or_unreadable_run_is_not_listed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        fleet = foil_root(repo) / "fleets" / "main"
        _registry(repo, "main", {"lead": _seat("lead", "")})
        outside = tmp_path / "outside-run"
        outside.mkdir()
        (outside / "registry.json").write_text("{}\n", encoding="utf-8")
        (outside / "fleet.json").write_text(
            '{"paused": true, "told": false}\n', encoding="utf-8"
        )
        (outside / "journal.log").write_text("password=hunter2\n", encoding="utf-8")
        run = fleet / "run"
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            target = f"projects/{key}"
            run.chmod(0)
            try:
                status_code, body = _open(port, token, target)
            finally:
                run.chmod(0o755)
            assert status_code == 200
            assert b"hunter2" not in body

            saved = tmp_path / "run-real"
            run.rename(saved)
            run.symlink_to(outside, target_is_directory=True)
            status_code, body = _open(port, token, target)
            text = body.decode()
            assert status_code == 200
            assert "No fleet." in text
            assert "hunter2" not in text
            assert "fleet paused" not in text


def test_a_linked_turns_directory_is_not_the_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _alive(monkeypatch)
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GEMINI_DIR", "GROK_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    _transcript(monkeypatch, tmp_path / "home")
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        fleet = foil_root(repo) / "fleets" / "main"
        _registry(repo, "main", {"lead": _seat("lead", "")})
        outside = tmp_path / "outside-turns"
        outside.mkdir()
        (outside / "lead.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "session_id": FAKE,
                    "sessions": [
                        {
                            "id": FAKE,
                            "harness": "fake",
                            "model": "",
                            "started": "2026-01-01T00:00:00Z",
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        turns = fleet / "run" / "turns"
        turns.mkdir()
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            seat = f"projects/{key}/fleets/main/seats/lead"
            fleet_target = f"projects/{key}/fleets/main"
            turns.chmod(0)
            try:
                status_code, body = _open(port, token, fleet_target)
            finally:
                turns.chmod(0o755)
            assert status_code == 200 and b"lead" in body
            turns.rmdir()
            turns.symlink_to(outside, target_is_directory=True)
            status_code, body = _open(port, token, seat)
            assert status_code == 200
            assert b"hunter2" not in body
            assert b"hello from the seat" not in body


def test_a_journal_field_is_masked_before_it_is_rendered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        fleet = foil_root(repo) / "fleets" / "main"
        board = fleet / "board"
        _registry(repo, "main", {"lead": _seat("lead", "")})
        _status(board, "---\nstate: working\n---\n\n## Mandate\n\nDone when: the tests pass.\n")
        line = {
            "time": "2026-01-01T00:00:00Z",
            "n": 1,
            "event": "decision",
            "by": "lead",
            "text": "sk-fixturevalue",
            "why": "password=hunter2",
        }
        (fleet / "run" / "journal.log").write_text(json.dumps(line) + "\n", encoding="utf-8")
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status_code, body = _open(port, token, f"projects/{key}/fleets/main")
        text = body.decode()
        assert status_code == 200
        assert "sk-fixturevalue" not in text
        assert "hunter2" not in text
        assert "[hidden]" in text


def test_a_bad_mailbox_name_leaves_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        board = foil_root(repo) / "fleets" / "main" / "board"
        _registry(repo, "main", {"lead": _seat("lead", "")})
        _status(board, "---\nstate: working\n---\n\n## Mandate\n\nDone when: the tests pass.\n")
        mailbox = board / "mail" / "not a seat"
        mailbox.mkdir(parents=True)
        (mailbox / "20200101T000000Z-lead-abcdabcd.md").write_text(
            "---\ncontract: mail/v1\nfrom: lead\nto: user\ntime: 2020-01-01T00:00:00Z\n---\n\n"
            "hello\n",
            encoding="utf-8",
        )
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status_code, body = _open(port, token, f"projects/{key}/fleets/main")
        assert status_code == 200
        assert b"the tests pass" in body


def test_seen_and_a_turn_log_do_not_follow_a_directory_symlink(tmp_path: Path) -> None:
    from foil.delivery import _read_lines
    from foil.inbox import _seen

    fleet = tmp_path / "fleet"
    (fleet / "run").mkdir(parents=True)
    outside = tmp_path / "outside-seats"
    outside.mkdir()
    (outside / "user.json").write_text(
        json.dumps({"seen": ["mail/user/secret.md"]}) + "\n", encoding="utf-8"
    )
    (fleet / "run" / "seats").symlink_to(outside)
    assert _seen(fleet) == set()
    turns = tmp_path / "outside-turns"
    turns.mkdir()
    (turns / "lead.log").write_text('{"path":"mail/user/secret.md","state":"inflight"}\n')
    link = fleet / "run" / "turns"
    link.symlink_to(turns)
    with pytest.raises(FoilError, match="refusing symlink"):
        _read_lines(link / "lead.log")


def test_a_bad_target_and_a_surrogate_do_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from foil_bridge.home import describe

    assert describe("projects/demo\x00") is None
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        fleet = foil_root(repo) / "fleets" / "main"
        board = fleet / "board"
        _registry(repo, "main", {"lead": _seat("lead", "")})
        _status(board, "---\nstate: working\n---\n\n## Mandate\n\nDone when: the tests pass.\n")
        line = {
            "time": "2026-01-01T00:00:00Z",
            "n": 1,
            "event": "decision",
            "by": "lead",
            "text": "\ud800",
            "why": "kept",
        }
        (fleet / "run" / "journal.log").write_text(json.dumps(line) + "\n", encoding="utf-8")
        project = foil_root(repo)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            project.chmod(0)
            try:
                status_code, body = _open(port, token, f"projects/{key}")
            finally:
                project.chmod(0o755)
            assert status_code == 404 and body == b""
            status_code, body = _open(port, token, f"projects/{key}/fleets/main")
        assert status_code == 200
        assert b"the tests pass" in body


def test_oversized_sidecars_are_not_read(tmp_path: Path) -> None:
    from foil_bridge.home import _seat_known
    from foil_bridge.transcript import _json, _turn_times

    from foil.journal import LIMIT as JOURNAL_LIMIT
    from foil.runner import MAX_PLAN_BYTES
    from foil.store import MAX_BYTES

    fleet = tmp_path / "fleet"
    (fleet / "run").mkdir(parents=True)
    log = fleet / "run" / "journal.log"
    log.write_bytes(b"x" * (JOURNAL_LIMIT + 1))
    assert _turn_times(fleet, "lead") == []
    registry = fleet / "run" / "registry.json"
    registry.write_bytes(b"{" + b" " * MAX_BYTES + b"}")
    assert _seat_known(fleet, "lead") is False
    plan = fleet / "run" / "plans"
    plan.mkdir()
    (plan / "lead.json").write_bytes(b"{" + b" " * MAX_PLAN_BYTES + b"}")
    assert _json(plan / "lead.json", fleet) == {}


def test_a_prefixed_token_inside_an_assignment_is_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        board = foil_root(repo) / "fleets" / "main" / "board"
        _registry(repo, "main", {"lead": _seat("lead", "")})
        _status(board, "---\nstate: working\n---\n\n## Mandate\n\nDone when: stay.\n")
        (board / "notes.md").write_text("token=ghp_fixturevalue\n", encoding="utf-8")
        log = foil_root(repo) / "fleets" / "main" / "run" / "journal.log"
        log.write_bytes(b"\xff\n")
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status_code, body = _open(port, token, f"projects/{key}/fleets/main/board/notes.md")
            text = body.decode()
            assert status_code == 200
            assert "ghp_fixturevalue" not in text
            assert "[hidden]" in text
            hidden = mask("+ghp_fixturevalue\n-sk-fixturevalue\n")
            assert "ghp_fixturevalue" not in hidden
            assert "sk-fixturevalue" not in hidden
            status_code, body = _open(port, token, f"projects/{key}/fleets/main")
            assert status_code == 200
            assert b"stay" in body


def test_a_checkout_that_maps_elsewhere_is_not_shown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        _registry(repo, "main", {"lead": _seat("lead", "")})
        monkeypatch.setattr(
            "foil_bridge.evidence.foil_root", lambda _path: Path("projects") / "other"
        )
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status_code, body = _open(port, token, f"projects/{key}")
        assert status_code == 404 and body == b""
