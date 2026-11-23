"""Inbox order and folding, and a byte-stable brief."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from foil_bridge.evidence import _fleet as fleet_page
from foil_bridge.pages import evidence_html

from foil.cli import main
from foil.inbox import _goal, collect_brief, format_brief
from foil.lifecycle import TmuxController
from foil.project import foil_root
from tests.test_spawn import _repo

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "brief"
_SECRET = "BODY-SECRET"


def _alive(monkeypatch: pytest.MonkeyPatch, names: set[str]) -> None:
    def matches(self: TmuxController, fleet: str, name: str, session: str, window: str) -> bool:
        return name in names

    monkeypatch.setattr(TmuxController, "matches_window", matches)


def _registry(path: Path, seats: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "fleet_id": path.parents[1].name,
        "tmux_session": "s",
        "lead": "",
        "seats": seats,
        "rotation": {},
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _seat(name: str, window: str) -> dict[str, str]:
    return {
        "name": name,
        "template": "implementer",
        "harness": "fake",
        "model": "",
        "window_id": window,
        "state": "idle",
        "worktree": "",
        "branch": "",
        "session_id": "s",
    }


def _mail(board: Path, mailbox: str, name: str) -> None:
    directory = board / "mail" / mailbox
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        "---\ncontract: mail/v1\nfrom: lead\nto: "
        + mailbox
        + "\ntime: 2020-01-01T00:00:00Z\n---\n\n"
        + _SECRET
        + "\n",
        encoding="utf-8",
    )


def _status(board: Path, text: str) -> None:
    board.mkdir(parents=True, exist_ok=True)
    (board / "status.md").write_text(text, encoding="utf-8")


def _fleet(repo: Path, fleet: str) -> Path:
    directory = foil_root(repo) / "fleets" / fleet
    (directory / "run").mkdir(parents=True, exist_ok=True)
    (directory / "board").mkdir(parents=True, exist_ok=True)
    return directory


def test_inbox_orders_and_folds_one_item_per_subject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Questions and mail, then seats, undelivered, blocked, then pause and done."""

    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    _alive(monkeypatch, {"a-paused", "helper"})
    aaa = _fleet(repo, "aaa")
    _status(
        aaa / "board",
        """---
contract: status/v1
state: blocked
updated: 2026-01-02T03:04:05Z
questions:
  - id: reading
    ask: Which reading?
    options: [first, second]
    recommended: first
    why: The tests match the first.
    evidence: board/results/t1.md
    owner: human
  - Ship the notes too?
decisions: []
---
""",
    )
    _mail(aaa / "board", "user", "20200101T000000Z-lead-11111111.md")
    _mail(aaa / "board", "user", "20190101T000000Z-lead-22222222.md")
    _mail(aaa / "board", "z-dead", "20180101T000000Z-lead-44444444.md")
    _mail(aaa / "board", "helper", "20200102T000000Z-lead-33333333.md")
    seen = aaa / "run" / "seats"
    seen.mkdir(parents=True)
    (seen / "user.json").write_text(
        json.dumps({"seen": ["mail/user/20190101T000000Z-lead-22222222.md"]}) + "\n",
        encoding="utf-8",
    )
    _registry(
        aaa / "run" / "registry.json",
        {
            "z-dead": _seat("z-dead", "@9"),
            "a-paused": _seat("a-paused", "@1"),
            "helper": _seat("helper", "@2"),
        },
    )
    (aaa / "run" / "turns").mkdir()
    (aaa / "run" / "turns" / "a-paused.json").write_text(
        json.dumps({"schema_version": 1, "paused": True}) + "\n",
        encoding="utf-8",
    )
    (aaa / "run" / "journal.log").write_text(
        '{"event":"pause","exit":1,"n":1,"seat":"a-paused","time":"2026-01-02T04:00:00Z"}\n',
        encoding="utf-8",
    )
    (aaa / "run" / "fleet.json").write_text(
        '{"paused": true, "told": true}\n', encoding="utf-8"
    )
    bbb = _fleet(repo, "bbb")
    _registry(bbb / "run" / "registry.json", {})
    _status(
        bbb / "board",
        "---\ncontract: status/v1\nstate: blocked\nupdated: 2026-01-03T00:00:00Z\n"
        "questions: []\ndecisions: []\n---\n",
    )
    ccc = _fleet(repo, "ccc")
    _registry(ccc / "run" / "registry.json", {})
    _status(
        ccc / "board",
        "---\ncontract: status/v1\nstate: done\nupdated: 2026-01-04T00:00:00Z\n"
        "questions: []\ndecisions: []\n---\n",
    )

    assert main(["inbox", "--all-fleets", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["kind"] for item in payload] == [
        "question",
        "question",
        "mail",
        "seat",
        "seat",
        "undelivered",
        "undelivered",
        "blocked",
        "paused",
        "done",
    ]
    assert [item["rank"] for item in payload] == [0, 0, 0, 1, 1, 2, 2, 3, 4, 4]
    assert [item["fleet"] for item in payload] == [
        "aaa",
        "aaa",
        "aaa",
        "aaa",
        "aaa",
        "aaa",
        "aaa",
        "bbb",
        "aaa",
        "ccc",
    ]
    assert [item["id"] for item in payload] == [
        "reading",
        "q2",
        "mail:mail/user/20200101T000000Z-lead-11111111.md",
        "seat:z-dead",
        "seat:a-paused",
        "mail:mail/user/20190101T000000Z-lead-22222222.md",
        "mail:mail/helper/20200102T000000Z-lead-33333333.md",
        "status",
        "fleet",
        "goal",
    ]
    assert len({item["subject"] for item in payload}) == len(payload)
    reading = payload[0]
    assert reading["options"] == ["first", "second"]
    assert reading["recommended"] == "first"
    assert reading["from"] == "human"
    assert reading["evidence"] == "board/results/t1.md"
    assert reading["since"] == "2026-01-02T03:04:05Z"
    assert "why" not in reading
    assert payload[1]["options"] == []
    assert payload[3]["evidence"] == "mail/z-dead/20180101T000000Z-lead-44444444.md"
    assert payload[3]["title"] == "z-dead is dead"
    assert payload[4]["since"] == "2026-01-02T04:00:00Z"
    assert payload[4]["title"] == "a-paused is paused"
    assert _SECRET not in json.dumps(payload)
    keys = {
        "id",
        "project",
        "fleet",
        "subject",
        "kind",
        "rank",
        "since",
        "title",
        "options",
        "recommended",
        "from",
        "evidence",
    }
    assert all(set(item) == keys for item in payload)

    assert main(["inbox", "--all-fleets"]) == 0
    human = capsys.readouterr().out
    assert human == (
        "aaa\tquestion\t2026-01-02T03:04:05Z\treading\tWhich reading?\n"
        "aaa\tquestion\t2026-01-02T03:04:05Z\tq2\tShip the notes too?\n"
        "aaa\tmail\t2020-01-01T00:00:00Z\t"
        "mail:mail/user/20200101T000000Z-lead-11111111.md\t"
        "mail/user/20200101T000000Z-lead-11111111.md\n"
        "aaa\tseat\t-\tseat:z-dead\tz-dead is dead\n"
        "aaa\tseat\t2026-01-02T04:00:00Z\tseat:a-paused\ta-paused is paused\n"
        "aaa\tundelivered\t2019-01-01T00:00:00Z\t"
        "mail:mail/user/20190101T000000Z-lead-22222222.md\t"
        "mail/user/20190101T000000Z-lead-22222222.md\n"
        "aaa\tundelivered\t2020-01-02T00:00:00Z\t"
        "mail:mail/helper/20200102T000000Z-lead-33333333.md\t"
        "mail/helper/20200102T000000Z-lead-33333333.md\n"
        "bbb\tblocked\t2026-01-03T00:00:00Z\tstatus\tblocked\n"
        "aaa\tpaused\t-\tfleet\tfleet paused\n"
        "ccc\tdone\t2026-01-04T00:00:00Z\tgoal\tdone\n"
    )
    assert _SECRET not in human

    monkeypatch.setenv("FOIL_FLEET", "bbb")
    assert main(["inbox"]) == 0
    assert capsys.readouterr().out == "blocked\t2026-01-03T00:00:00Z\tstatus\tblocked\n"


def test_a_refused_status_is_one_raw_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    board = foil_root(repo) / "fleets" / "main" / "board"
    _status(
        board,
        "---\ncontract: status/v1\nstate: blocked\nquestions:\n  - id: reading\n"
        "    nested:\n      inner: yes\n---\n",
    )
    assert main(["inbox", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["kind"] for item in payload] == ["question"]
    assert payload[0]["id"] == "raw"
    assert payload[0]["options"] == []
    assert payload[0]["since"] == ""
    assert "state: blocked" in payload[0]["title"]
    assert "nested:" in payload[0]["title"]


def test_a_duplicate_id_falls_back_and_stays_unique(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    board = foil_root(repo) / "fleets" / "main" / "board"
    _status(
        board,
        "---\nquestions:\n  - id: q2\n    ask: First\n    owner: lead\n"
        "  - id: q2\n    ask: Again\n  - And a string\n---\n",
    )
    assert main(["inbox", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in payload] == ["q2", "q3", "q4"]
    assert payload[0]["from"] == ""
    assert [item["title"] for item in payload] == ["First", "Again", "And a string"]


def test_workers_are_refused_and_a_lead_may_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["inbox"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["inbox"]) == 0
    assert capsys.readouterr().out == ""
    assert main(["brief"]) == 0


def test_all_projects_needs_no_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _repo(tmp_path, monkeypatch)
    second_dir = tmp_path / "other"
    second_dir.mkdir()
    from tests.test_init import _init_git_repository

    _init_git_repository(second_dir)
    monkeypatch.chdir(second_dir)
    assert main(["init"]) == 0
    for repo, ask in ((first, "From the first"), (second_dir, "From the second")):
        board = foil_root(repo) / "fleets" / "main" / "board"
        _status(board, f"---\nquestions:\n  - {ask}\n---\n")
    capsys.readouterr()
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    assert main(["inbox", "--all-projects"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert all(line.split("\t")[2] == "question" for line in lines)
    assert "From the first" in lines[0] or "From the first" in lines[1]
    assert "From the second" in lines[0] or "From the second" in lines[1]


def test_an_invalid_fleet_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    monkeypatch.setenv("FOIL_FLEET", "no.dot")
    assert main(["inbox"]) == 1
    assert "invalid fleet" in capsys.readouterr().err


def test_an_empty_inbox_prints_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    assert main(["inbox"]) == 0
    assert capsys.readouterr().out == ""
    assert main(["inbox", "--json"]) == 0
    assert capsys.readouterr().out == "[]\n"


def test_brief_output_from_the_fixture_is_byte_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    _alive(monkeypatch, set())
    fleet = foil_root(repo) / "fleets" / "main"
    shutil.copytree(FIXTURE, fleet, dirs_exist_ok=True)
    expected = (FIXTURE / "expected.txt").read_text(encoding="utf-8")
    records = collect_brief(repo, ["main"], "demo")
    assert format_brief(records, projects_named=False) + "\n" == expected
    rendered = json.dumps(records, ensure_ascii=False, sort_keys=True) + "\n"
    assert rendered == (FIXTURE / "expected.json").read_text(encoding="utf-8")
    assert main(["brief"]) == 0
    first = capsys.readouterr().out
    assert first == expected
    assert main(["brief"]) == 0
    assert capsys.readouterr().out == first
    assert main(["brief", "--json"]) == 0
    raw = capsys.readouterr().out
    assert main(["brief", "--json"]) == 0
    assert capsys.readouterr().out == raw
    assert _SECRET not in first
    assert _SECRET not in raw


def test_a_journal_symlink_leaves_the_rest_of_the_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(monkeypatch, set())
    fleet = foil_root(repo) / "fleets" / "main"
    shutil.copytree(FIXTURE, fleet, dirs_exist_ok=True)
    log = fleet / "run" / "journal.log"
    log.unlink()
    log.symlink_to(tmp_path / "elsewhere.log")
    (tmp_path / "elsewhere.log").write_text("password=hunter2\n", encoding="utf-8")
    record = collect_brief(repo, ["main"], "demo")[0]
    assert record["journal"] == []
    assert record["goal"]


def test_a_page_shows_a_mapping_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    board = foil_root(repo) / "fleets" / "main" / "board"
    _status(
        board,
        "---\nquestions:\n  - id: reading\n    ask: Which reading?\n"
        "    options: [first, second]\n---\n",
    )
    model = fleet_page({"project": "local", "fleet": "main"}, repo)
    html = evidence_html(model, "token").decode()
    assert "Which reading?" in html


def test_the_goal_is_the_mandate_done_when_then_its_first_line() -> None:
    mandate = "## Mandate\n\nDone when: the tests pass\n\n## Checklist\n\nDone when: no\n"
    assert _goal(mandate) == "the tests pass"
    assert _goal("## Mandate\n\nMay decide: order.\n") == "May decide: order."
    empty = "The real goal.\n\n## Mandate\n\n## Checklist\n\nDone when: no\n"
    assert _goal(empty) == "The real goal."
    assert _goal("First line.\n\nDone when: not the goal\n") == "First line."


def test_a_missing_checkout_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _repo(tmp_path, monkeypatch)
    second_dir = tmp_path / "other"
    second_dir.mkdir()
    from tests.test_init import _init_git_repository

    _init_git_repository(second_dir)
    monkeypatch.chdir(second_dir)
    assert main(["init"]) == 0
    for repo, ask in ((first, "From the first"), (second_dir, "From the second")):
        board = foil_root(repo) / "fleets" / "main" / "board"
        _status(board, f"---\nquestions:\n  - {ask}\n---\n")
    shutil.rmtree(first)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    capsys.readouterr()
    assert main(["inbox", "--all-projects"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "From the second" in lines[0]
    assert "From the first" not in lines[0]


def test_skills_write_the_mapping_form() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "foil" / "defaults" / "skills"
    for name in ("lead.md", "joined.md"):
        text = (root / name).read_text(encoding="utf-8")
        for key in ("id", "ask", "options", "recommended", "why", "evidence", "owner"):
            assert f"`{key}`" in text
        assert "human" in text
        assert "master" in text
        assert "plain string" in text
