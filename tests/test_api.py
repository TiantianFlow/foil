"""foil.api: one function per command, with an explicit project, fleet, and caller."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from foil import api
from foil.cli import _build_parser, main
from foil.errors import FoilError
from foil.lifecycle import _seat_name
from foil.project import foil_root
from tests.test_mail_board_ops import _create_board_file, _create_mail, _repo
from tests.test_room_post import _fleet, _seat


def test_version_is_one() -> None:
    assert api.VERSION == 1


def test_read_json_matches_the_api_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    mail = _create_mail(repo, "user", "lead", "hello from lead")
    _create_board_file(repo, "notes/shared.txt", "a note\n")

    listed = api.mail_list(repo, "main", "user")
    capsys.readouterr()
    assert main(["mail", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == listed

    read = api.mail_read(repo, "main", "user", str(mail))
    capsys.readouterr()
    assert main(["mail", "read", str(mail), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == read

    seats = api.seat_list(repo, "main", "user")
    capsys.readouterr()
    assert main(["seat", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == seats

    files = api.board_list(repo, "main", "user", "notes/*")
    capsys.readouterr()
    assert main(["board", "list", "notes/*", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == files

    note = api.board_read(repo, "main", "user", "notes/shared.txt")
    capsys.readouterr()
    assert main(["board", "read", "notes/shared.txt", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == note

    lessons = api.memory_list(repo, "main", "user")
    capsys.readouterr()
    assert main(["memory", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == lessons

    roster = api.roster_list(repo, "main", "user")
    capsys.readouterr()
    assert main(["roster", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == roster

    shown = api.roster_show(repo, "main", "user", "lead")
    capsys.readouterr()
    assert main(["roster", "show", "lead", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == shown

    capsys.readouterr()
    waited = api.mail_wait_for(repo, "main", "user", timeout=0, as_json=True)
    assert json.loads(capsys.readouterr().out) == waited
    assert main(["mail", "wait", "--timeout", "0", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"items": [], "quiet": 0}


def _overlap(calls: list[tuple[str, Path, str]]) -> dict[str, list[dict[str, str]]]:
    """Run the calls together. Each must see only the fleet it was given."""

    barrier = threading.Barrier(len(calls))
    found: dict[str, list[dict[str, str]]] = {}
    errors: list[BaseException] = []

    def call(name: str, project: Path, fleet: str) -> None:
        try:
            barrier.wait(timeout=5)
            assert os.environ.get("FOIL_FLEET") == "from-env"
            found[name] = api.mail_list(project, fleet, "user")
            assert os.environ.get("FOIL_FLEET") == "from-env"
        except BaseException as exc:  # noqa: BLE001 — reported once the threads join
            errors.append(exc)

    threads = [threading.Thread(target=call, args=item) for item in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]
    return found


def test_two_threads_keep_their_own_project_and_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _repo(tmp_path, monkeypatch)
    _create_mail(first, "user", "lead", "mail for the first project")
    side = foil_root(first) / "fleets" / "side" / "board" / "mail" / "user"
    side.mkdir(parents=True)
    (side / "20260930T120000Z-lead-0123abcd.md").write_text(
        "---\n"
        "contract: mail/v1\n"
        "from: lead\n"
        "to: user\n"
        "time: 2026-09-30T12:00:00Z\n"
        "---\n"
        "\n"
        "side fleet\n",
        encoding="utf-8",
    )
    other = tmp_path / "second"
    other.mkdir()
    monkeypatch.chdir(other)
    from tests.test_mail_board_ops import _init_git_repository

    _init_git_repository(other)
    assert main(["init"]) == 0
    _create_mail(other, "user", "lead", "mail for the second project")
    monkeypatch.setenv("FOIL_FLEET", "from-env")
    projects = _overlap([("main", first, "main"), ("other", other, "main")])
    fleets = _overlap([("main", first, "main"), ("side", first, "side")])
    assert os.environ.get("FOIL_FLEET") == "from-env"
    assert projects["main"][0]["path"] != projects["other"][0]["path"]
    assert "fleets/side/" in fleets["side"][0]["path"]
    assert "fleets/main/" in fleets["main"][0]["path"]
    assert "fleets/side/" not in fleets["main"][0]["path"]


def test_as_user_is_the_human_while_a_lead_is_joined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _fleet(repo, _seat("lead", state="joined", window=""))
    user_mail = _create_mail(repo, "user", "lead", "for the human")
    lead_dir = foil_root(repo) / "fleets" / "main" / "board" / "mail" / "lead"
    lead_dir.mkdir(parents=True)
    lead_mail = lead_dir / "20260930T120000Z-user-0123abcd.md"
    lead_mail.write_text(
        "---\n"
        "contract: mail/v1\n"
        "from: user\n"
        "to: lead\n"
        "time: 2026-09-30T12:00:00Z\n"
        "---\n"
        "\n"
        "for the lead\n",
        encoding="utf-8",
    )
    assert main(["mail", "list", "--json"]) == 0
    joined = json.loads(capsys.readouterr().out)
    assert [row["path"] for row in joined] == [str(lead_mail.resolve())]
    assert main(["--as", "user", "mail", "list", "--json"]) == 0
    human_out = capsys.readouterr()
    human = json.loads(human_out.out)
    assert [row["path"] for row in human] == [str(user_mail.resolve())]
    assert "new for lead" not in human_out.err
    assert api.mail_list(repo, "main", "user")[0]["path"] == str(user_mail.resolve())
    capsys.readouterr()
    assert main(["mail", "list", "--as", "lead", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == joined


def test_as_is_refused_inside_and_lead_needs_a_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(tmp_path, monkeypatch)
    assert main(["--as", "lead", "seat", "list"]) == 1
    assert capsys.readouterr().err == "foil: --as lead needs a joined lead\n"
    assert main(["--as", "guest", "seat", "list"]) == 1
    assert capsys.readouterr().err == "foil: --as is user or lead\n"
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["seat", "list", "--as", "user"]) == 1
    assert capsys.readouterr().err == "foil: --as is outside the fleet only\n"


def test_project_key_addresses_another_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _repo(tmp_path, monkeypatch)
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    monkeypatch.chdir(second_dir)
    from tests.test_mail_board_ops import _init_git_repository

    _init_git_repository(second_dir)
    assert main(["init"]) == 0
    mail = _create_mail(second_dir, "user", "lead", "the other project")
    key = foil_root(second_dir).name
    monkeypatch.chdir(first)
    capsys.readouterr()
    assert main(["--project", key, "mail", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["path"] for row in rows] == [str(mail.resolve())]
    assert main(["--project", "not-a-project", "mail", "list"]) == 1
    assert capsys.readouterr().err == "foil: unknown project 'not-a-project'\n"


def test_master_is_reserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _fleet(repo, _seat("lead", state="killed", window=""))
    assert main(["seat", "spawn", "implementer", "--name", "master"]) == 1
    assert capsys.readouterr().err == "foil: seat name 'master' is reserved\n"
    registry = {"seats": {"lead": {"state": "killed"}}}
    assert _seat_name(registry, "master", None) == "master-1"
    with pytest.raises(FoilError, match="seat name 'master' is reserved"):
        _seat_name(registry, "implementer", "master")
    monkeypatch.setenv("FOIL_SEAT_ID", "master")
    assert main(["mail", "list"]) == 1
    assert capsys.readouterr().err == "foil: unknown seat\n"


def _plant(repo: Path, fleet: str, seat: str, sender: str, body: str, stamp: str) -> Path:
    directory = foil_root(repo) / "fleets" / fleet / "board" / "mail" / seat
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}-{sender}-0123abcd.md"
    path.write_text(
        "---\n"
        "contract: mail/v1\n"
        f"from: {sender}\n"
        f"to: {seat}\n"
        "time: 2026-09-30T12:00:00Z\n"
        "---\n"
        "\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return path


def test_all_fleets_follows_each_fleets_joined_lead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No --as is lead only in a fleet whose lead is joined. --as user is the human."""

    repo = _repo(tmp_path, monkeypatch)
    _fleet(repo, _seat("lead", state="joined", window=""))
    side = foil_root(repo) / "fleets" / "side" / "run"
    side.mkdir(parents=True)
    (side / "registry.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fleet_id": "side",
                "tmux_session": "",
                "lead": "",
                "seats": {},
                "rotation": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    lead_mail = _plant(repo, "main", "lead", "user", "for the joined lead", "20260930T120000Z")
    side_user = _plant(repo, "side", "user", "lead", "for the human there", "20260930T120001Z")
    side_lead = _plant(repo, "side", "lead", "user", "not this fleet's caller", "20260930T120002Z")
    capsys.readouterr()
    assert main(["mail", "wait", "--timeout", "0", "--all-fleets", "--json"]) == 0
    paths = [item["path"] for item in json.loads(capsys.readouterr().out)["items"]]
    assert str(lead_mail.resolve()) in paths
    assert str(side_user.resolve()) in paths
    assert str(side_lead.resolve()) not in paths
    human = _plant(repo, "main", "user", "lead", "for the human here", "20260930T120003Z")
    assert main(["--as", "user", "mail", "wait", "--timeout", "0", "--all-fleets", "--json"]) == 0
    human_paths = [item["path"] for item in json.loads(capsys.readouterr().out)["items"]]
    assert human_paths == [str(human.resolve())]


def test_as_and_project_parse_on_the_root_and_the_leaf() -> None:
    parser = _build_parser()
    root = parser.parse_args(["--as", "user", "--project", "abc", "seat", "list"])
    leaf = parser.parse_args(["seat", "list", "--as", "user", "--project", "abc"])
    assert root.as_caller == leaf.as_caller == "user"
    assert root.project_key == leaf.project_key == "abc"
    roster = parser.parse_args(["--project", "abc", "roster", "add", "role", "--project"])
    assert roster.project_key == "abc"
    assert roster.project is True
