"""Offline scenarios for `foil post` and the room nudge.

The fake harness and a stand-in tmux are the seats. No network and no agent CLI.
"""

from __future__ import annotations

import io
import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from foil.board_ops import split_front_matter
from foil.cli import main
from foil.lifecycle import _instruction
from foil.presets import load_template
from foil.project import common_dir, foil_root, project_key
from foil.store import SAFE_ID, SCHEMA_VERSION, create_exclusive
from foil.tmux import TmuxController
from tests.test_slice_b import _repo

_LATE = "List `board/room` and read threads this seat has not answered."
_COUNT = re.compile(r"This directory has (\d+) files, and \1 does not refuse the write\.")


def _seat(name: str, *, state: str = "alive", window: str) -> dict[str, str]:
    return {
        "name": name,
        "template": "lead" if name == "lead" else "implementer",
        "harness": "fake",
        "model": "",
        "window_id": window,
        "state": state,
        "worktree": "",
        "branch": "",
        "session_id": "",
    }


def _fleet(repo: Path, *seats: dict[str, str]) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "fleet_id": "fleet",
        "tmux_session": "sess",
        "lead": "lead",
        "seats": {seat["name"]: seat for seat in seats},
    }
    path = foil_root(repo) / "run" / "registry.json"
    path.write_text(json.dumps(payload), encoding="utf-8")


def _alive(repo: Path) -> None:
    _fleet(
        repo,
        _seat("reviewer-1", window="@3"),
        _seat("implementer-1", window="@2"),
        _seat("lead", window="@1"),
    )


def _install_tmux(
    monkeypatch: pytest.MonkeyPatch,
    *,
    foreign: frozenset[str] = frozenset(),
    fail_keys: frozenset[str] = frozenset(),
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(self: TmuxController, argv: list[str]) -> subprocess.CompletedProcess[str]:
        del self
        calls.append(list(argv))
        if argv and argv[0] == "display-message":
            window = argv[argv.index("-t") + 1]
            registry = json.loads(
                (foil_root(Path.cwd()) / "run" / "registry.json").read_text(encoding="utf-8")
            )
            seat = "other"
            if window not in foreign:
                seat = next(
                    (
                        name
                        for name, record in registry["seats"].items()
                        if record.get("window_id") == window
                    ),
                    "other",
                )
            stdout = f"$0\t{window}\t{registry['fleet_id']}\t{seat}\t0\n"
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        if argv[:2] == ["send-keys", "-l"]:
            window = argv[argv.index("-t") + 1]
            if window in fail_keys:
                return subprocess.CompletedProcess(argv, 1, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("foil.tmux.TmuxController._run", run)
    return calls


def _room(repo: Path) -> list[Path]:
    root = foil_root(repo) / "board" / "room"
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*.md") if path.is_file() and not path.is_symlink())


def _open(path: Path) -> tuple[dict[str, str | list[str]], str]:
    fields, body = split_front_matter(path.read_text(encoding="utf-8"))
    assert fields is not None
    return fields, body


def _nudge_lines(calls: list[list[str]]) -> list[str]:
    return [argv[-1] for argv in calls if argv[:2] == ["send-keys", "-l"]]


def _nudge_windows(calls: list[list[str]]) -> list[str]:
    return [argv[argv.index("-t") + 1] for argv in calls if argv[:2] == ["send-keys", "-l"]]


def _one_line(capsys: pytest.CaptureFixture[str]) -> str:
    err = capsys.readouterr().err
    assert err.endswith("\n")
    assert err.count("\n") == 1
    assert err.startswith("foil:")
    assert "Traceback" not in err
    return err


def _refused(capsys: pytest.CaptureFixture[str], repo: Path, argv: list[str]) -> None:
    before = [(path, path.read_bytes()) for path in _room(repo)]
    assert main(argv) == 1
    _one_line(capsys)
    assert [(path, path.read_bytes()) for path in _room(repo)] == before


def _post(
    repo: Path, monkeypatch: pytest.MonkeyPatch, text: str = "hello"
) -> tuple[Path, list[list[str]]]:
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    assert main(["post", text]) == 0
    files = _room(repo)
    assert len(files) == 1
    return files[0], calls


# --- Success ---


def test_success_post_fans_out_to_other_alive_seats_sorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["post", "a view"]) == 0
    assert capsys.readouterr().err == ""
    path = _room(repo)[0]
    fields, body = _open(path)
    assert fields["contract"] == "room/v1"
    assert fields["from"] == "lead"
    assert fields["nudged"] == ["implementer-1", "reviewer-1"]
    assert "re" not in fields
    assert "generation" not in fields
    assert "generation:" not in path.read_text(encoding="utf-8")
    assert path.name == f"{path.parent.name}.md"
    assert SAFE_ID.fullmatch(path.parent.name)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "a view" in body
    assert _nudge_windows(calls) == ["@2", "@3"]
    assert all(line.startswith("lead ") for line in _nudge_lines(calls))


def test_success_outside_user_reaches_every_alive_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, calls = _post(repo, monkeypatch, "from outside")
    fields, _body = _open(path)
    assert fields["from"] == "user"
    assert fields["nudged"] == ["implementer-1", "lead", "reviewer-1"]
    assert _nudge_windows(calls) == ["@2", "@1", "@3"]


def test_success_reply_wakes_the_same_set_in_the_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "root view")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root.resolve()), "a further fact"]) == 0
    files = _room(repo)
    assert len(files) == 2
    child = next(path for path in files if path != root)
    assert child.parent == root.parent
    fields, body = _open(child)
    assert fields["re"] == str(root.resolve())
    assert fields["from"] == "user"
    assert fields["nudged"] == ["implementer-1", "lead", "reviewer-1"]
    assert "a further fact" in body
    assert _nudge_windows(calls) == ["@2", "@1", "@3"]
    assert all(str(child.resolve()) in line for line in _nudge_lines(calls))


def test_success_worker_may_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    _install_tmux(monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["post", "from the worker"]) == 0
    assert capsys.readouterr().err == ""
    fields, _body = _open(_room(repo)[0])
    assert fields["from"] == "implementer-1"
    assert fields["nudged"] == ["lead", "reviewer-1"]


def test_success_lead_may_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    _install_tmux(monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["post", "from the lead"]) == 0
    assert capsys.readouterr().err == ""
    fields, _body = _open(_room(repo)[0])
    assert fields["from"] == "lead"
    assert "lead" not in fields["nudged"]


def test_success_stdin_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    _install_tmux(monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin\n"))
    assert main(["post", "-"]) == 0
    _fields, body = _open(_room(repo)[0])
    assert "from stdin\n" in body


def test_success_board_read_and_list_see_the_room(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, _calls = _post(repo, monkeypatch, "visible")
    capsys.readouterr()
    assert main(["board", "read", str(path)]) == 0
    assert capsys.readouterr().out == path.read_text(encoding="utf-8")
    assert main(["board", "list", "room/*/*.md"]) == 0
    listed = capsys.readouterr().out.splitlines()
    relative = path.relative_to(foil_root(repo) / "board")
    assert str(relative) in listed
    assert main(["board", "read", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["contract"] == "room/v1"
    assert payload["nudged"] == ["implementer-1", "lead", "reviewer-1"]
    assert "generation" not in payload
    assert "visible" in payload["body"]


def test_success_credential_clean_text_is_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, _calls = _post(repo, monkeypatch, "a normal sentence about the design")
    assert capsys.readouterr().err == ""
    text = path.read_text(encoding="utf-8")
    assert "a normal sentence about the design" in text
    assert "credential-shaped" not in text


def test_success_preamble_count_does_not_block_another_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "first")
    _fields, body = _open(root)
    match = _COUNT.search(body)
    assert match is not None
    assert int(match.group(1)) == len(list(root.parent.iterdir())) == 1
    assert "Reply with `foil post --re" in body
    assert "--decline`" in body
    assert "Do not open a new `foil post` to continue this thread." in body
    assert "checkpoint" not in body.lower()
    assert "tiebreak" not in body.lower()
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "second"]) == 0
    second = next(path for path in _room(repo) if path != root)
    second_n = _COUNT.search(_open(second)[1])
    assert second_n is not None
    assert int(second_n.group(1)) == len(list(root.parent.iterdir())) == 2
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "third"]) == 0
    third = next(path for path in _room(repo) if path not in {root, second})
    third_n = _COUNT.search(_open(third)[1])
    assert third_n is not None
    assert int(third_n.group(1)) == len(list(root.parent.iterdir())) == 3


def test_success_reply_to_a_child_stays_in_the_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "root")
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "child"]) == 0
    child = next(path for path in _room(repo) if path != root)
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(child), "grandchild"]) == 0
    files = _room(repo)
    assert len(files) == 3
    assert {path.parent for path in files} == {root.parent}
    grandchild = next(path for path in files if path not in {root, child})
    fields, _body = _open(grandchild)
    assert fields["re"] == str(child.resolve())


def test_success_text_decline_still_wakes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, calls = _post(repo, monkeypatch, "decline")
    fields, body = _open(path)
    assert fields["nudged"] == ["implementer-1", "lead", "reviewer-1"]
    assert "Reply with `foil post --re" in body
    assert calls


def test_success_seat_named_user_is_nudged_by_an_outside_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _fleet(repo, _seat("lead", window="@1"), _seat("user", window="@9"))
    calls = _install_tmux(monkeypatch)
    assert main(["post", "hello"]) == 0
    fields, _body = _open(_room(repo)[0])
    assert fields["from"] == "user"
    assert fields["nudged"] == ["lead", "user"]
    assert _nudge_windows(calls) == ["@1", "@9"]


def test_success_parent_front_matter_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "original")
    root.write_text(
        "---\ncontract: room/v1\nfrom: user\ngeneration: 99\nnudged: [nobody]\n---\n\ndecline\n",
        encoding="utf-8",
    )
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "a new fact"]) == 0
    child = next(path for path in _room(repo) if path != root)
    fields, body = _open(child)
    assert fields["re"] == str(root.resolve())
    assert "generation" not in fields
    assert fields["nudged"] == ["implementer-1", "lead", "reviewer-1"]
    assert "a new fact" in body
    assert "generation: 99" not in body
    assert _nudge_windows(calls) == ["@2", "@1", "@3"]


# --- Decline ---


def test_decline_writes_exact_body_and_types_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "proposal")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "--decline"]) == 0
    assert capsys.readouterr().err == ""
    child = next(path for path in _room(repo) if path != root)
    fields, body = _open(child)
    assert body == "decline\n"
    assert fields["nudged"] == []
    assert fields["re"] == str(root.resolve())
    assert "Reply with" not in body
    assert _nudge_lines(calls) == []
    assert all(argv[0] != "send-keys" for argv in calls)


def test_decline_does_not_wake_alive_seats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "still open")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--decline", "--re", str(root)]) == 0
    assert _nudge_windows(calls) == []
    fields, _body = _open(next(path for path in _room(repo) if path != root))
    assert fields["nudged"] == []


def test_decline_ignores_credential_text_in_the_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "clean")
    root.write_text("sk-secret in a hand edit\n", encoding="utf-8")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "--decline"]) == 0
    child = next(path for path in _room(repo) if path != root)
    assert _open(child)[1] == "decline\n"
    assert "sk-secret" not in child.read_text(encoding="utf-8")
    assert _nudge_lines(calls) == []


# --- Path refusal ---


def test_path_re_outside_board_room_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _post(repo, monkeypatch)
    outside = tmp_path / "outside.md"
    outside.write_text("x\n", encoding="utf-8")
    _refused(capsys, repo, ["post", "--re", str(outside), "nope"])


def test_path_re_missing_parent_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch)
    missing = root.parent / "missing.md"
    _refused(capsys, repo, ["post", "--re", str(missing), "nope"])


def test_path_re_symlink_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch)
    link = root.parent / "alias.md"
    link.symlink_to(root.name)
    _refused(capsys, repo, ["post", "--re", str(link), "nope"])
    assert link.is_symlink()


def test_path_re_with_dotdot_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch)
    bad = root.parent / ".." / root.parent.name / root.name
    assert ".." in bad.parts
    _refused(capsys, repo, ["post", "--re", str(bad), "nope"])


def test_path_re_relative_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch)
    relative = f"room/{root.parent.name}/{root.name}"
    _refused(capsys, repo, ["post", "--re", relative, "nope"])


def test_path_re_directory_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch)
    _refused(capsys, repo, ["post", "--re", str(root.parent), "nope"])


def test_path_re_not_in_a_thread_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _post(repo, monkeypatch)
    rogue = foil_root(repo) / "board" / "room" / "rogue.md"
    rogue.write_text("x\n", encoding="utf-8")
    _refused(capsys, repo, ["post", "--re", str(rogue), "nope"])


def test_path_decline_without_re_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _post(repo, monkeypatch)
    _refused(capsys, repo, ["post", "--decline"])


def test_path_decline_with_text_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch)
    _refused(capsys, repo, ["post", "--re", str(root), "--decline", "extra"])
    _refused(capsys, repo, ["post", "--decline", "extra"])


# --- Lifecycle ---


def test_lifecycle_dead_seat_gets_no_keystroke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _fleet(
        repo,
        _seat("lead", window="@1"),
        _seat("implementer-1", state="dead", window="@2"),
    )
    calls = _install_tmux(monkeypatch)
    assert main(["post", "hello"]) == 0
    fields, _body = _open(_room(repo)[0])
    assert fields["nudged"] == ["lead"]
    assert _nudge_windows(calls) == ["@1"]
    assert all("@2" not in argv for argv in calls)


def test_lifecycle_killed_seat_gets_no_keystroke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _fleet(
        repo,
        _seat("lead", window="@1"),
        _seat("implementer-1", state="killed", window="@2"),
    )
    calls = _install_tmux(monkeypatch)
    assert main(["post", "hello"]) == 0
    fields, _body = _open(_room(repo)[0])
    assert fields["nudged"] == ["lead"]
    assert all("@2" not in argv for argv in calls)


def test_lifecycle_window_check_failure_omits_the_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch, foreign=frozenset({"@2"}))
    assert main(["post", "hello"]) == 0
    fields, _body = _open(_room(repo)[0])
    assert fields["nudged"] == ["lead", "reviewer-1"]
    assert "@2" not in _nudge_windows(calls)
    assert _room(repo)


def test_lifecycle_missing_window_id_omits_the_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _fleet(
        repo,
        _seat("lead", window="@1"),
        _seat("implementer-1", window=""),
        _seat("reviewer-1", window="@3"),
    )
    calls = _install_tmux(monkeypatch)
    assert main(["post", "hello"]) == 0
    fields, _body = _open(_room(repo)[0])
    assert fields["nudged"] == ["lead", "reviewer-1"]
    assert _nudge_windows(calls) == ["@1", "@3"]


def test_lifecycle_author_is_not_nudged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["post", "hello"]) == 0
    fields, _body = _open(_room(repo)[0])
    assert "lead" not in fields["nudged"]
    assert "@1" not in _nudge_windows(calls)
    assert all("@1" not in argv for argv in calls)


def test_lifecycle_older_project_post_creates_room(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    room = foil_root(repo) / "board" / "room"
    room.rmdir()
    _alive(repo)
    _install_tmux(monkeypatch)
    assert main(["post", "hello"]) == 0
    assert room.is_dir()
    assert _room(repo)


def test_lifecycle_init_creates_room_without_a_new_report_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    report = capsys.readouterr().out
    assert (foil_root(repo) / "board" / "room").is_dir()
    assert "board/room" not in report
    assert "room/v1" not in report


def test_lifecycle_exclusive_create_does_not_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "root")

    class Frozen(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            del tz
            return datetime(2026, 10, 16, 13, 57, tzinfo=UTC)

    tokens = iter(("aaaa", "bbbb"))

    def token_hex(nbytes: int = 16) -> str:
        assert nbytes == 4
        return next(tokens)

    monkeypatch.setattr("foil.board.datetime", Frozen)
    monkeypatch.setattr("foil.board.secrets.token_hex", token_hex)
    kept = root.parent / "20261016T135700Z-user-aaaa.md"
    kept.write_text("keep\n", encoding="utf-8")
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "new"]) == 0
    assert kept.read_text(encoding="utf-8") == "keep\n"
    written = root.parent / "20261016T135700Z-user-bbbb.md"
    assert written.is_file()
    assert "new" in written.read_text(encoding="utf-8")


def test_lifecycle_file_exists_error_retries_with_a_new_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    _install_tmux(monkeypatch)
    seen: list[Path] = []

    def wrapped(path: Path, payload: bytes) -> None:
        seen.append(path)
        if len(seen) == 1:
            raise FileExistsError(path)
        create_exclusive(path, payload)

    monkeypatch.setattr("foil.board.create_exclusive", wrapped)
    assert main(["post", "hello"]) == 0
    assert len(seen) == 2
    assert not seen[0].exists()
    assert seen[1].is_file()
    assert seen[0] != seen[1]
    assert "hello" in seen[1].read_text(encoding="utf-8")


def test_lifecycle_nudge_line_is_sender_and_path_not_the_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, calls = _post(repo, monkeypatch, "unique-body-text")
    lines = _nudge_lines(calls)
    assert lines
    for line in lines:
        sender, separator, raw = line.partition(" ")
        assert separator == " "
        assert sender == "user"
        assert raw == str(path.resolve())
        assert "unique-body-text" not in line
        assert "\n" not in line


def test_lifecycle_failed_send_keys_is_one_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch, fail_keys=frozenset({"@2"}))
    assert main(["post", "hello"]) == 0
    literal = [argv for argv in calls if argv[:2] == ["send-keys", "-l"] and "@2" in argv]
    assert len(literal) == 1
    assert not any(argv[0] == "send-keys" and "-l" not in argv and "@2" in argv for argv in calls)
    fields, _body = _open(_room(repo)[0])
    assert fields["nudged"] == ["lead", "reviewer-1"]
    assert _room(repo)


# --- Credentials and empty input ---


def test_input_credential_text_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    before = _room(repo)
    assert main(["post", "sk-secret"]) == 1
    assert capsys.readouterr().err == "foil: credential-shaped text refused\n"
    assert main(["post", "hello\nsk-secret"]) == 1
    assert capsys.readouterr().err == "foil: credential-shaped text refused\n"
    assert _room(repo) == before


def test_input_empty_text_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    assert main(["post", ""]) == 1
    assert _one_line(capsys) == "foil: empty text\n"
    assert _room(repo) == []
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["post", "-"]) == 1
    assert _one_line(capsys) == "foil: empty text\n"
    assert _room(repo) == []


def test_input_whitespace_text_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    assert main(["post", "   "]) == 1
    assert _one_line(capsys) == "foil: empty text\n"
    assert main(["post", "\n"]) == 1
    assert _room(repo) == []


def test_input_missing_text_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    assert main(["post"]) == 1
    assert _one_line(capsys) == "foil: TEXT is required\n"
    assert _room(repo) == []


# --- Harness and instruction ---


def _run_fake(repo: Path, seat: str, stdin: str) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parent / "harness" / "foil_fake.py"
    env = os.environ.copy()
    env["FOIL_SEAT_ID"] = seat
    env["FOIL_PROJECT"] = project_key(common_dir(repo))
    return subprocess.run(
        [sys.executable, str(script), "--prompt", ""],
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        cwd=repo,
        check=False,
    )


def test_harness_fake_opens_a_room_path_from_the_nudge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, calls = _post(repo, monkeypatch, "room-marker-alpha")
    line = _nudge_lines(calls)[0]
    assert line == f"user {path.resolve()}"
    fake = foil_root(repo) / "run" / "fake"
    fake.mkdir(exist_ok=True)
    (fake / "implementer-1.json").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "when": {"mail_contains": "room-marker-alpha"},
                        "do": [{"write": "board/notes/opened.txt", "text": "yes\n"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = _run_fake(repo, "implementer-1", line + "\n")
    assert result.returncode == 0, result.stderr
    note = foil_root(repo) / "board" / "notes" / "opened.txt"
    assert note.read_text(encoding="utf-8") == "yes\n"


def test_harness_decline_types_nothing_so_the_fake_sees_no_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "offer")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "--decline"]) == 0
    assert _nudge_lines(calls) == []
    child = next(path for path in _room(repo) if path != root)
    fake = foil_root(repo) / "run" / "fake"
    fake.mkdir(exist_ok=True)
    (fake / "implementer-1.json").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "when": {"mail_contains": "decline"},
                        "do": [{"write": "board/notes/saw-decline.txt", "text": "yes\n"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = _run_fake(repo, "implementer-1", "")
    assert result.returncode == 0, result.stderr
    assert not (foil_root(repo) / "board" / "notes" / "saw-decline.txt").exists()
    handled = fake / "implementer-1.handled"
    if handled.is_file():
        assert str(child.resolve()) not in handled.read_text(encoding="utf-8")


def test_harness_hand_written_room_file_wakes_nobody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    thread = foil_root(repo) / "board" / "room" / "hand"
    thread.mkdir()
    hand = thread / "note.md"
    hand.write_text("hand written\n", encoding="utf-8")
    assert main(["board", "list", "room/*/*.md"]) == 0
    assert str(hand.relative_to(foil_root(repo) / "board")) in capsys.readouterr().out
    assert all(argv[0] != "send-keys" for argv in calls)
    fake = foil_root(repo) / "run" / "fake"
    fake.mkdir(exist_ok=True)
    (fake / "lead.json").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "when": {"mail_contains": "hand written"},
                        "do": [{"write": "board/notes/woke.txt", "text": "yes\n"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = _run_fake(repo, "lead", "")
    assert result.returncode == 0, result.stderr
    assert not (foil_root(repo) / "board" / "notes" / "woke.txt").exists()
    assert _nudge_lines(calls) == []


def test_harness_instruction_offers_unanswered_room_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    lead = _instruction(repo, "lead", load_template(repo, "lead"), restarted=False)
    worker = _instruction(
        repo, "implementer-1", load_template(repo, "implementer"), restarted=False
    )
    for text in (lead, worker):
        assert _LATE in text
        assert "foil post [TEXT] [--re PATH] [--decline]" in text
        assert "A path under `board/room/` is `foil board read`." in text
        assert "room/v1" in text
    skills = Path(__file__).resolve().parents[1] / "skills"
    for name in ("lead.md", "worker.md"):
        assert _LATE in (skills / name).read_text(encoding="utf-8")
    assert "foil seat spawn" not in worker


def test_harness_mail_read_refuses_a_room_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, _calls = _post(repo, monkeypatch, "not mail")
    assert main(["mail", "read", str(path)]) == 1
    err = _one_line(capsys)
    assert "not mail" not in err


# --- Regressions ---


def test_regression_worker_cannot_spawn_kill_or_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    before = (foil_root(repo) / "run" / "registry.json").read_text(encoding="utf-8")
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["seat", "spawn", "implementer"]) == 1
    assert _one_line(capsys) == "foil: not allowed\n"
    assert main(["seat", "kill", "lead"]) == 1
    assert _one_line(capsys) == "foil: not allowed\n"
    assert main(["seat", "resume"]) == 1
    assert _one_line(capsys) == "foil: not allowed\n"
    assert (foil_root(repo) / "run" / "registry.json").read_text(encoding="utf-8") == before


def test_regression_send_is_one_recipient_and_has_no_re(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    assert main(["send", "lead", "only the lead"]) == 0
    assert capsys.readouterr().err == ""
    mail = foil_root(repo) / "board" / "mail" / "lead"
    files = list(mail.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "re:" not in text
    assert "to: lead\n" in text
    assert _nudge_windows(calls) == ["@1"]
    assert "only the lead" not in _nudge_lines(calls)[0]
    assert _room(repo) == []
