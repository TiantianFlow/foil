"""Requirement scenarios for `foil post` and the seats a post wakes.

The fake harness and a stand-in tmux are the seats. No network and no agent CLI.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from foil.board import items_for
from foil.board_ops import split_front_matter
from foil.cli import main
from foil.lifecycle import _instruction
from foil.presets import load_template
from foil.project import foil_root
from foil.store import SCHEMA_VERSION, create_exclusive
from foil.tmux import TmuxController, TmuxTarget
from tests.test_slice_b import _repo

_LATE = (
    "List `board/room` and read the open rooms this seat is a member of or that name it "
    "as @name, where it has not answered."
)
_VIEW = (
    "only to add a view the thread does not contain or to object to a close proposal"
)
_OTHERWISE = "Otherwise `foil post --re` with `--decline`."
_PREAMBLE_CONTINUE = "Do not open a new `foil post` to continue this thread."
_SKILL_CONTINUE = "Do not open a new `foil post` to continue the thread."
_NOTES = "Notes: write files under `board/notes/`. They wake no one."
_CHOICE = (
    "Send is one seat. Post is a room's members. "
    "The task and result files are the durable record. A note wakes nobody."
)
_ROOM_FIELDS = "room/v2 (from, time, optional re, members, closed, wakes)."
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
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
    path = foil_root(repo) / "fleets" / "main" / "run" / "registry.json"
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
    dead_panes: frozenset[str] = frozenset(),
    on_literal: Callable[[str], None] | None = None,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(self: TmuxController, argv: list[str]) -> subprocess.CompletedProcess[str]:
        del self
        calls.append(list(argv))
        if argv[:2] == ["send-keys", "-l"] and on_literal is not None:
            on_literal(argv[-1])
        if argv and argv[0] == "display-message":
            window = argv[argv.index("-t") + 1]
            registry_path = foil_root(Path.cwd()) / "fleets" / "main" / "run" / "registry.json"
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
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
            pane_dead = "1" if window in dead_panes else "0"
            stdout = f"$0\t{window}\t{registry['fleet_id']}\t{seat}\t{pane_dead}\n"
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        if argv[:2] == ["send-keys", "-l"]:
            window = argv[argv.index("-t") + 1]
            if window in fail_keys:
                return subprocess.CompletedProcess(argv, 1, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("foil.tmux.TmuxController._run", run)
    return calls


def _room(repo: Path) -> list[Path]:
    root = foil_root(repo) / "fleets" / "main" / "board" / "room"
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*.md") if path.is_file() and not path.is_symlink())


def _open(path: Path) -> tuple[dict[str, str | list[str]], str]:
    fields, body = split_front_matter(path.read_text(encoding="utf-8"))
    assert fields is not None
    return fields, body


def _typed(calls: list[list[str]]) -> list[list[str]]:
    return [argv for argv in calls if argv and argv[0] == "send-keys"]


def _woken(repo: Path, calls: list[list[str]] | None = None) -> list[str]:
    """The `wakes` of the newest room file. Given the tmux calls, nothing was typed."""

    if calls is not None:
        assert _typed(calls) == []
    newest = max(_room(repo), key=lambda path: path.stat().st_mtime_ns)
    wakes = _open(newest)[0]["wakes"]
    assert isinstance(wakes, list)
    return wakes


def _one_line(capsys: pytest.CaptureFixture[str]) -> str:
    err = capsys.readouterr().err
    assert err.endswith("\n")
    assert err.count("\n") == 1
    assert err.startswith("foil:")
    assert "Traceback" not in err
    return err


def _no_pane_scrape(calls: list[list[str]]) -> None:
    assert all(argv[0] != "capture-pane" for argv in calls)


def _assert_preamble(body: str, path: Path, count: int) -> None:
    target = str(path.resolve())
    assert body.startswith(f"Reply with `foil post --re {target}` {_VIEW}.")
    assert f"Otherwise `foil post --re {target} --decline`." in body
    assert _PREAMBLE_CONTINUE in body
    assert f"This directory has {count} files, and {count} does not refuse the write." in body


def _board_files(repo: Path) -> list[str]:
    board = foil_root(repo) / "fleets" / "main" / "board"
    return sorted(
        path.relative_to(board).as_posix()
        for path in board.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def _refused(
    capsys: pytest.CaptureFixture[str],
    repo: Path,
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_tmux(monkeypatch)
    before = [(path, path.read_bytes()) for path in _room(repo)]
    assert main(argv) == 1
    _one_line(capsys)
    assert [(path, path.read_bytes()) for path in _room(repo)] == before
    assert _typed(calls) == []


def _post(
    repo: Path, monkeypatch: pytest.MonkeyPatch, text: str = "hello"
) -> tuple[Path, list[list[str]]]:
    _alive(repo)
    monkeypatch.setenv("FOIL_SEAT_ID", "")
    calls = _install_tmux(monkeypatch)
    assert main(["post", text]) == 0
    files = _room(repo)
    assert len(files) == 1
    return files[0], calls


# --- Success ---


def test_a_post_stays_in_the_fleet_that_wrote_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    monkeypatch.setenv("FOIL_FLEET", "side")
    assert main(["post", "only side"]) == 0
    assert _room(repo) == []
    side = foil_root(repo) / "fleets" / "side" / "board" / "room"
    files = sorted(path for path in side.rglob("*.md") if path.is_file())
    assert len(files) == 1
    assert "only side" in files[0].read_text(encoding="utf-8")
    assert _typed(calls) == []


def test_f33_waking_post_writes_one_room_file_and_nudges_every_other_alive_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["post", "a view"]) == 0
    assert capsys.readouterr().err == ""
    room = foil_root(repo) / "fleets" / "main" / "board" / "room"
    files = _room(repo)
    assert len(files) == 1
    path = files[0]
    assert path.parent.parent == room
    assert path.name == f"{path.parent.name}.md"
    assert [item.name for item in path.parent.iterdir()] == [path.name]
    fields, body = _open(path)
    assert fields["contract"] == "room/v2"
    assert fields["from"] == "lead"
    assert _TIME.fullmatch(str(fields["time"]))
    assert fields["wakes"] == ["implementer-1", "reviewer-1"]
    assert "re" not in fields
    assert "generation" not in fields
    assert "generation:" not in path.read_text(encoding="utf-8")
    assert "a view" in body
    f"lead {path.resolve()}"
    assert _typed(calls) == []
    assert _woken(repo) == ["implementer-1", "reviewer-1"]
    _no_pane_scrape(calls)


def test_f33_empty_seat_id_sends_as_user_and_reaches_every_alive_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "")
    path, calls = _post(repo, monkeypatch, "from outside")
    fields, _body = _open(path)
    assert fields["from"] == "user"
    assert fields["wakes"] == ["implementer-1", "lead", "reviewer-1"]
    assert _typed(calls) == []
    assert _woken(repo) == ["implementer-1", "lead", "reviewer-1"]


def test_f33_a_reply_wakes_the_members_except_a_member_whose_last_file_is_a_decline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "root view")
    assert _open(root)[0]["members"] == ["implementer-1", "lead", "reviewer-1", "user"]
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["post", "--re", str(root.resolve()), "--decline"]) == 0
    # implementer-1 declined last, so only reviewer-1 is woken.
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root.resolve()), "a further fact"]) == 0
    child = max(_room(repo), key=lambda path: path.stat().st_mtime_ns)
    assert child.parent == root.parent
    assert child.name != f"{child.parent.name}.md"
    fields, body = _open(child)
    assert fields["contract"] == "room/v2"
    assert _TIME.fullmatch(str(fields["time"]))
    assert fields["re"] == str(root.resolve())
    assert fields["from"] == "lead"
    assert "members" not in fields
    assert fields["wakes"] == ["reviewer-1"]
    assert "a further fact" in body
    assert _woken(repo) == ["reviewer-1"]
    # @implementer-1 wakes the member that declined.
    monkeypatch.setenv("FOIL_SEAT_ID", "reviewer-1")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(child.resolve()), "@implementer-1, an objection"]) == 0
    reply = max(_room(repo), key=lambda path: path.stat().st_mtime_ns)
    fields, _body = _open(reply)
    assert fields["wakes"] == ["implementer-1", "lead"]
    f"reviewer-1 {reply.resolve()}"
    assert _typed(calls) == []
    assert _woken(repo) == ["implementer-1", "lead"]
    # A post after the decline makes implementer-1's last file a waking one again.
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["post", "--re", str(root.resolve()), "my view after all"]) == 0
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root.resolve()), "noted"]) == 0
    assert _woken(repo) == ["implementer-1", "reviewer-1"]

def test_s6_a_worker_replies_but_does_not_open_a_room(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    _refused(capsys, repo, ["post", "from the worker"], monkeypatch)
    assert _room(repo) == []
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    _install_tmux(monkeypatch)
    assert main(["post", "--members", "implementer-1", "a room"]) == 0
    root = _room(repo)[0]
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "from the worker"]) == 0
    assert capsys.readouterr().err == ""
    path = max(_room(repo), key=lambda item: item.stat().st_mtime_ns)
    fields, _body = _open(path)
    assert fields["from"] == "implementer-1"
    assert fields["wakes"] == ["lead"]
    assert _woken(repo) == ["lead"]
    for flags in (["--members", "reviewer-1"], ["--close"]):
        _refused(capsys, repo, ["post", "--re", str(root), *flags, "text"], monkeypatch)

def test_s6_lead_may_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["post", "from the lead"]) == 0
    assert capsys.readouterr().err == ""
    path = _room(repo)[0]
    fields, _body = _open(path)
    assert fields["from"] == "lead"
    assert fields["wakes"] == ["implementer-1", "reviewer-1"]
    assert "lead" not in _woken(repo, calls)


def test_f33_text_dash_reads_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "")
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin\n"))
    assert main(["post", "-"]) == 0
    path = _room(repo)[0]
    fields, body = _open(path)
    assert fields["contract"] == "room/v2"
    assert body.endswith("does not refuse the write.\n\nfrom stdin\n")
    assert _typed(calls) == []


def test_f31_board_read_prints_the_room_file_and_list_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, calls = _post(repo, monkeypatch, "visible")
    before = len(calls)
    capsys.readouterr()
    assert main(["board", "read", str(path)]) == 0
    assert capsys.readouterr().out == path.read_text(encoding="utf-8")
    assert len(calls) == before
    assert main(["board", "list", "room/*/*.md"]) == 0
    listed = capsys.readouterr().out.splitlines()
    relative = path.relative_to(foil_root(repo) / "fleets" / "main" / "board")
    assert str(relative) in listed
    assert main(["board", "read", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["contract"] == "room/v2"
    assert payload["from"] == "user"
    assert _TIME.fullmatch(payload["time"])
    assert payload["wakes"] == ["implementer-1", "lead", "reviewer-1"]
    assert "generation" not in payload
    assert "re" not in payload
    assert payload["body"].endswith("does not refuse the write.\n\nvisible\n")
    assert len(calls) == before


def test_f33_preamble_counts_the_directory_and_does_not_refuse_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "first")
    _fields, body = _open(root)
    _assert_preamble(body, root, 1)
    assert int(_COUNT.search(body).group(1)) == len(list(root.parent.iterdir())) == 1
    assert body.endswith("does not refuse the write.\n\nfirst\n")
    (root.parent / "extra.txt").write_text("not a room file\n", encoding="utf-8")
    root.write_text(
        root.read_text(encoding="utf-8").replace(
            "contract: room/v1\n", "contract: room/v1\nfiles: 99\n", 1
        ),
        encoding="utf-8",
    )
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "second"]) == 0
    second = next(path for path in _room(repo) if path != root)
    _fields, second_body = _open(second)
    count = len(list(root.parent.iterdir()))
    assert count == 3
    _assert_preamble(second_body, second, count)
    assert int(_COUNT.search(second_body).group(1)) == count
    assert "files: 99" not in second_body
    assert second_body.endswith("does not refuse the write.\n\nsecond\n")


def test_f33_re_to_a_child_stays_in_that_thread_directory(
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
    assert grandchild.parent == child.parent


def test_f33_decline_word_in_the_body_still_wakes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, calls = _post(repo, monkeypatch, "decline")
    fields, body = _open(path)
    assert fields["wakes"] == ["implementer-1", "lead", "reviewer-1"]
    assert body.endswith("does not refuse the write.\n\ndecline\n")
    assert body != "decline\n"
    assert _typed(calls) == []


def test_f33_a_hand_written_file_does_not_change_the_room(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "original")
    (root.parent / "notes.md").write_text(
        "---\ncontract: mail/v1\nfrom: reviewer-1\nmembers: [lead]\nclosed: true\n---\n\n"
        "decline\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["post", "--re", str(root), "--decline"]) == 0
    monkeypatch.setenv("FOIL_SEAT_ID", "")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "a new fact @nobody"]) == 0
    child = max(_room(repo), key=lambda path: path.stat().st_mtime_ns)
    fields, body = _open(child)
    assert fields["re"] == str(root.resolve())
    assert fields["wakes"] == ["lead", "reviewer-1"]
    assert body.endswith("does not refuse the write.\n\na new fact @nobody\n")
    assert _woken(repo, calls) == ["lead", "reviewer-1"]

def test_f33_decline_writes_the_line_decline_and_types_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "proposal")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "--decline"]) == 0
    assert capsys.readouterr().err == ""
    child = next(path for path in _room(repo) if path != root)
    fields, body = _open(child)
    assert fields["contract"] == "room/v2"
    assert fields["from"] == "user"
    assert _TIME.fullmatch(str(fields["time"]))
    assert body == "decline\n"
    assert fields["wakes"] == []
    assert fields["re"] == str(root.resolve())
    assert "generation" not in fields
    assert "Reply with" not in body
    assert _typed(calls) == []
    assert all(argv[0] != "send-keys" for argv in calls)
    _no_pane_scrape(calls)


def test_f33_decline_flag_before_re_types_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "still open")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--decline", "--re", str(root)]) == 0
    assert _woken(repo, calls) == []
    fields, body = _open(next(path for path in _room(repo) if path != root))
    assert body == "decline\n"
    assert fields["wakes"] == []


def test_f33_decline_does_not_copy_the_parent_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "clean")
    root.write_text("sk-secret in a hand edit\n", encoding="utf-8")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "--decline"]) == 0
    captured = capsys.readouterr()
    assert "sk-secret" not in captured.err
    assert "sk-secret" not in captured.out
    child = next(path for path in _room(repo) if path != root)
    assert _open(child)[1] == "decline\n"
    assert "sk-secret" not in child.read_text(encoding="utf-8")
    assert _typed(calls) == []


# --- Path refusal ---


def test_f33_re_outside_board_room_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _post(repo, monkeypatch)
    outside = tmp_path / "outside.md"
    outside.write_text("x\n", encoding="utf-8")
    _refused(capsys, repo, ["post", "--re", str(outside), "nope"], monkeypatch)


def test_f33_re_missing_file_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch)
    missing = root.parent / "missing.md"
    _refused(capsys, repo, ["post", "--re", str(missing), "nope"], monkeypatch)


def test_f33_re_symlink_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch)
    link = root.parent / "alias.md"
    link.symlink_to(root.name)
    _refused(capsys, repo, ["post", "--re", str(link), "nope"], monkeypatch)
    assert link.is_symlink()


def test_f33_re_with_dotdot_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch)
    bad = root.parent / ".." / root.parent.name / root.name
    assert ".." in bad.parts
    _refused(capsys, repo, ["post", "--re", str(bad), "nope"], monkeypatch)


def test_f33_re_relative_path_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch)
    relative = f"room/{root.parent.name}/{root.name}"
    _refused(capsys, repo, ["post", "--re", relative, "nope"], monkeypatch)


def test_f33_re_directory_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch)
    _refused(capsys, repo, ["post", "--re", str(root.parent), "nope"], monkeypatch)


def test_f33_re_not_under_a_thread_id_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _post(repo, monkeypatch)
    rogue = foil_root(repo) / "fleets" / "main" / "board" / "room" / "rogue.md"
    rogue.write_text("x\n", encoding="utf-8")
    _refused(capsys, repo, ["post", "--re", str(rogue), "nope"], monkeypatch)


def test_f33_decline_without_re_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _post(repo, monkeypatch)
    _refused(capsys, repo, ["post", "--decline"], monkeypatch)


def test_f33_decline_with_text_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch)
    _refused(capsys, repo, ["post", "--re", str(root), "--decline", "extra"], monkeypatch)
    _refused(capsys, repo, ["post", "--decline", "extra"], monkeypatch)


# --- Lifecycle ---


def test_f33_empty_stored_state_still_reaches_every_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _fleet(
        repo,
        _seat("reviewer-1", state="", window="@3"),
        _seat("implementer-1", state="", window="@2"),
        _seat("lead", state="", window="@1"),
    )
    monkeypatch.setenv("FOIL_SEAT_ID", "")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "from outside"]) == 0
    fields, _body = _open(_room(repo)[0])
    assert fields["from"] == "user"
    assert fields["wakes"] == ["implementer-1", "lead", "reviewer-1"]
    assert _woken(repo, calls) == ["implementer-1", "lead", "reviewer-1"]


def test_f33_empty_stored_state_still_wakes_a_dead_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _fleet(
        repo,
        _seat("lead", state="", window="@1"),
        _seat("implementer-1", state="", window="@2"),
    )
    monkeypatch.setenv("FOIL_SEAT_ID", "")
    calls = _install_tmux(monkeypatch, dead_panes=frozenset({"@2"}))
    assert main(["post", "hello"]) == 0
    path = _room(repo)[0]
    fields, body = _open(path)
    assert "hello" in body
    assert fields["wakes"] == ["implementer-1", "lead"]
    assert _typed(calls) == []
    assert _woken(repo) == ["implementer-1", "lead"]


def test_f33_a_dead_seat_is_woken_for_its_next_launch(
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
    fields, body = _open(_room(repo)[0])
    assert "hello" in body
    assert fields["wakes"] == ["implementer-1", "lead"]
    assert _woken(repo) == ["implementer-1", "lead"]
    assert all(argv[0] != "send-keys" or "@2" not in argv for argv in calls)


def test_f33_killed_seat_is_omitted_and_the_file_remains(
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
    fields, body = _open(_room(repo)[0])
    assert "hello" in body
    assert fields["wakes"] == ["lead"]
    assert _woken(repo) == ["lead"]
    assert all(argv[0] != "send-keys" or "@2" not in argv for argv in calls)


def test_f33_no_window_check_runs_and_the_seat_is_woken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch, foreign=frozenset({"@2"}))
    assert main(["post", "hello"]) == 0
    assert calls == []
    fields, body = _open(_room(repo)[0])
    assert "hello" in body
    assert fields["wakes"] == ["implementer-1", "lead", "reviewer-1"]


def test_f33_missing_window_id_still_wakes_the_seat(
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
    fields, body = _open(_room(repo)[0])
    assert "hello" in body
    assert fields["wakes"] == ["implementer-1", "lead", "reviewer-1"]
    assert _woken(repo, calls) == ["implementer-1", "lead", "reviewer-1"]


def test_f33_author_is_not_nudged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["post", "hello"]) == 0
    fields, body = _open(_room(repo)[0])
    assert "hello" in body
    assert fields["wakes"] == ["implementer-1", "reviewer-1"]
    assert _woken(repo, calls) == ["implementer-1", "reviewer-1"]


def test_f33_post_creates_board_room_when_the_project_lacks_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    room = foil_root(repo) / "fleets" / "main" / "board" / "room"
    room.rmdir()
    _alive(repo)
    _install_tmux(monkeypatch)
    assert main(["post", "hello"]) == 0
    assert room.is_dir()
    files = _room(repo)
    assert len(files) == 1
    fields, body = _open(files[0])
    assert fields["contract"] == "room/v2"
    assert "hello" in body


def test_s74_an_existing_name_is_not_overwritten(
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
    kept = root.parent / "20261016T135700000000Z-user-aaaa.md"
    kept.write_text("keep\n", encoding="utf-8")
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "new"]) == 0
    assert kept.read_text(encoding="utf-8") == "keep\n"
    written = root.parent / "20261016T135700000000Z-user-bbbb.md"
    assert written.is_file()
    assert "new" in written.read_text(encoding="utf-8")


def test_s74_a_colliding_create_uses_another_name(
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
    assert seen[0] != seen[-1]
    assert not seen[0].exists()
    assert seen[-1].is_file()
    assert "hello" in seen[-1].read_text(encoding="utf-8")
    assert len(_room(repo)) == 1


def test_f33_a_post_types_nothing_into_any_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, calls = _post(repo, monkeypatch, "unique-body-text")
    assert _typed(calls) == []
    _no_pane_scrape(calls)
    assert _open(path)[0]["wakes"] == ["implementer-1", "lead", "reviewer-1"]


# --- Credentials and empty input ---


def test_n5_post_refuses_the_same_credential_text_as_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    mail = foil_root(repo) / "fleets" / "main" / "board" / "mail"
    for text in ("sk-secret", "hello\nsk-secret"):
        assert main(["send", "lead", text]) == 1
        send_err = capsys.readouterr().err
        assert main(["post", text]) == 1
        post_err = capsys.readouterr().err
        assert send_err == post_err == "foil: credential-shaped text refused\n"
        assert "sk-secret" not in send_err
        assert _room(repo) == []
        assert list(mail.rglob("*.md")) == []
        assert _typed(calls) == []


def test_f33_empty_waking_text_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    assert main(["post", ""]) == 1
    assert _one_line(capsys) == "foil: empty text\n"
    assert _room(repo) == []
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["post", "-"]) == 1
    assert _one_line(capsys) == "foil: empty text\n"
    assert _room(repo) == []
    assert _typed(calls) == []


def test_f33_whitespace_waking_text_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    assert main(["post", "   "]) == 1
    assert _one_line(capsys) == "foil: empty text\n"
    assert main(["post", "\n"]) == 1
    assert _one_line(capsys) == "foil: empty text\n"
    assert _room(repo) == []
    assert _typed(calls) == []


def test_f33_empty_re_writes_nothing_and_types_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", "", "hello"]) == 1
    assert _one_line(capsys) == "foil: room path must be absolute\n"
    assert _room(repo) == []
    assert _typed(calls) == []
    assert main(["post", "--re", "", "--decline"]) == 1
    assert _one_line(capsys) == "foil: --decline requires --re\n"
    assert _room(repo) == []
    assert _typed(calls) == []


def test_f33_missing_text_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    assert main(["post"]) == 1
    assert _one_line(capsys) == "foil: TEXT is required\n"
    assert _room(repo) == []
    assert _typed(calls) == []


# --- Harness and instruction ---


def _given(repo: Path, seat: str) -> list[str]:
    """What the runner would give the seat next: its items not yet delivered."""

    board = (foil_root(repo) / "fleets" / "main" / "board").resolve()
    return [item["path"] for item in items_for(board, seat, set())]


def test_s10_a_post_is_an_item_for_each_seat_it_wakes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, _calls = _post(repo, monkeypatch, "room-marker-alpha")
    for seat in ("lead", "implementer-1", "reviewer-1"):
        assert _given(repo, seat) == [str(path.resolve())]


def test_s10_a_decline_is_an_item_for_nobody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "offer")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "--decline"]) == 0
    assert _typed(calls) == []
    child = next(path for path in _room(repo) if path != root)
    for seat in ("lead", "implementer-1", "reviewer-1"):
        assert str(child.resolve()) not in _given(repo, seat)


def test_f33_hand_written_room_file_wakes_nobody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    thread = foil_root(repo) / "fleets" / "main" / "board" / "room" / "hand"
    thread.mkdir()
    hand = thread / "note.md"
    hand.write_text("hand written\n", encoding="utf-8")
    assert main(["board", "list", "room/*/*.md"]) == 0
    board = foil_root(repo) / "fleets" / "main" / "board"
    assert str(hand.relative_to(board)) in capsys.readouterr().out
    assert _typed(calls) == []
    for seat in ("lead", "implementer-1", "reviewer-1"):
        assert _given(repo, seat) == []


def test_f24_instruction_offers_unanswered_room_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    lead = _instruction(repo, "lead", load_template(repo, "lead"), restarted=False)
    worker = _instruction(
        repo, "implementer-1", load_template(repo, "implementer"), restarted=False
    )
    skills = Path(__file__).resolve().parents[1] / "skills"
    skill_text = {
        "lead": (skills / "lead.md").read_text(encoding="utf-8"),
        "worker": (skills / "worker.md").read_text(encoding="utf-8"),
    }
    for text in (lead, worker, skill_text["lead"], skill_text["worker"]):
        assert _LATE in text
        assert _VIEW in text
        assert _OTHERWISE in text
        assert _SKILL_CONTINUE in text
    assert "foil post [TEXT] [--re PATH] [--decline] [--members NAMES] [--close]" in lead
    assert "foil post --re PATH [TEXT] [--decline]" in worker
    assert "--members" not in worker
    for text in (lead, worker):
        assert "A path under `board/room/` is `foil board read`." in text
        assert _CHOICE in text
        assert _ROOM_FIELDS in text
        assert _NOTES in text
    assert "foil seat spawn" not in worker
    assert "foil post" in skill_text["lead"]
    assert "foil post" in skill_text["worker"]


def test_s8_lead_skill_says_a_post_without_members_reaches_every_seat() -> None:
    text = (Path(__file__).resolve().parents[1] / "skills" / "lead.md").read_text(
        encoding="utf-8"
    )
    members = "Without `--members`, its members are every seat that is not killed at that moment."
    assert members in text


def test_f24_a_later_launch_wakes_no_one_and_writes_no_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, calls = _post(repo, monkeypatch, "already posted")
    before_bytes = path.read_bytes()
    room_file = f"room/{path.parent.name}/{path.name}"
    assert _board_files(repo) == [room_file]
    capsys.readouterr()

    def launch(self: TmuxController, **kwargs: object) -> TmuxTarget:
        del self
        return TmuxTarget(
            session_name=str(kwargs["session_name"]),
            window_name=str(kwargs["window_name"]),
            session_id="$1",
            window_id="@40",
        )

    monkeypatch.setattr("foil.lifecycle.TmuxController.launch", launch)
    assert main(["seat", "spawn", "reviewer", "--name", "late"]) == 0
    assert capsys.readouterr().err == ""
    assert _typed(calls) == []
    assert path.read_bytes() == before_bytes
    assert _board_files(repo) == [room_file]
    instruction = foil_root(repo) / "fleets" / "main" / "run" / "instructions" / "late.md"
    assert _LATE in instruction.read_text(encoding="utf-8")


def test_f15_a_note_wakes_no_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    note = foil_root(repo) / "fleets" / "main" / "board" / "notes" / "shared.txt"
    note.write_text("a note\n", encoding="utf-8")
    assert _typed(calls) == []
    capsys.readouterr()
    assert main(["board", "read", "notes/shared.txt"]) == 0
    assert capsys.readouterr().out == "a note\n"
    assert main(["board", "list", "notes/*"]) == 0
    assert "notes/shared.txt" in capsys.readouterr().out.splitlines()
    assert _typed(calls) == []
    assert _room(repo) == []


def test_f29_mail_read_refuses_a_room_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    path, _calls = _post(repo, monkeypatch, "not mail")
    assert main(["mail", "read", str(path)]) == 1
    err = _one_line(capsys)
    assert "not mail" not in err


# --- Regressions ---


def test_s6_worker_cannot_spawn_kill_or_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    registry = foil_root(repo) / "fleets" / "main" / "run" / "registry.json"
    before = registry.read_text(encoding="utf-8")
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["seat", "spawn", "implementer"]) == 1
    assert _one_line(capsys) == "foil: not allowed\n"
    assert main(["seat", "kill", "lead"]) == 1
    assert _one_line(capsys) == "foil: not allowed\n"
    assert main(["seat", "resume"]) == 1
    assert _one_line(capsys) == "foil: not allowed\n"
    assert registry.read_text(encoding="utf-8") == before


def test_f13_send_writes_one_mail_types_nothing_and_does_not_write_re(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    calls = _install_tmux(monkeypatch)
    assert main(["send", "lead", "only the lead"]) == 0
    assert capsys.readouterr().err == ""
    mail = foil_root(repo) / "fleets" / "main" / "board" / "mail" / "lead"
    files = list(mail.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "re:" not in text
    assert "to: lead\n" in text
    assert _typed(calls) == []
    assert _room(repo) == []


# --- Scoped rooms (issue 88) ---


def _newest(repo: Path) -> Path:
    return max(_room(repo), key=lambda path: path.stat().st_mtime_ns)


def test_f33_the_lead_opens_a_room_for_the_members_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    _install_tmux(monkeypatch)
    assert main(["post", "--members", " reviewer-1 ,", "the question"]) == 0
    root = _room(repo)[0]
    fields, body = _open(root)
    assert fields["members"] == ["lead", "reviewer-1"]
    assert fields["wakes"] == ["reviewer-1"]
    assert _woken(repo) == ["reviewer-1"]
    assert body.endswith("\n\nthe question\n")
    # A reply wakes the members only, not every seat that is alive.
    monkeypatch.setenv("FOIL_SEAT_ID", "reviewer-1")
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "a view"]) == 0
    assert _woken(repo) == ["lead"]
    for members in ("nobody", "", ",", "../x"):
        monkeypatch.setenv("FOIL_SEAT_ID", "lead")
        _refused(capsys, repo, ["post", "--members", members, "text"], monkeypatch)


def test_f33_members_on_a_reply_wake_only_the_seats_they_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    _install_tmux(monkeypatch)
    assert main(["post", "--members", "reviewer-1", "the question"]) == 0
    root = _room(repo)[0]
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "--members", "implementer-1,reviewer-1", "add"]) == 0
    fields, _body = _open(_newest(repo))
    assert fields["members"] == ["implementer-1", "lead", "reviewer-1"]
    assert fields["wakes"] == ["implementer-1"]
    assert _woken(repo) == ["implementer-1"]
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "--members", "implementer-1", "remove"]) == 0
    fields, _body = _open(_newest(repo))
    assert fields["members"] == ["implementer-1", "lead"]
    assert fields["wakes"] == []
    # reviewer-1 left the room, so a later reply does not wake it unless named.
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "next"]) == 0
    assert _woken(repo) == ["implementer-1"]
    _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "@reviewer-1 one fact from you"]) == 0
    assert _woken(repo) == ["implementer-1", "reviewer-1"]
    assert "members" not in _open(_newest(repo))[0]


def test_f33_a_closed_room_refuses_every_later_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "the question")
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    _refused(capsys, repo, ["post", "--close", "no re"], monkeypatch)
    _refused(
        capsys, repo, ["post", "--re", str(root), "--close", "--members", "lead", "x"], monkeypatch
    )
    _refused(capsys, repo, ["post", "--re", str(root), "--decline", "--close"], monkeypatch)
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "--close", "Continued elsewhere @reviewer-1"]) == 0
    fields, body = _open(_newest(repo))
    assert fields["closed"] == "true"
    assert fields["wakes"] == ["reviewer-1"]
    assert body.startswith("This room is closed. Foil refuses a reply here.\n")
    assert _woken(repo) == ["reviewer-1"]
    for seat, argv in (
        ("reviewer-1", ["post", "--re", str(root), "late view"]),
        ("reviewer-1", ["post", "--re", str(root), "--decline"]),
        ("lead", ["post", "--re", str(root), "--close", "again"]),
    ):
        monkeypatch.setenv("FOIL_SEAT_ID", seat)
        calls = _install_tmux(monkeypatch)
        before = _room(repo)
        assert main(argv) == 1
        assert capsys.readouterr().err == "foil: room is closed\n"
        assert _room(repo) == before
        assert _typed(calls) == []


def test_f33_room_refusals_name_the_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root, _calls = _post(repo, monkeypatch, "the question")
    re_root = ["--re", str(root)]
    for seat, argv, message in (
        ("implementer-1", ["post", "x"], "foil: only the lead opens a room"),
        ("implementer-1", ["post", *re_root, "--members", "lead", "x"],
         "foil: only the lead sets members or closes a room"),
        ("implementer-1", ["post", *re_root, "--close", "x"],
         "foil: only the lead sets members or closes a room"),
        ("lead", ["post", "--members", ",", "x"], "foil: --members names no seat"),
        ("lead", ["post", "--members", "lead,ghost", "x"], "foil: unknown seat 'ghost'"),
        ("lead", ["post", "--close", "x"], "foil: --close needs --re and takes no --members"),
        ("lead", ["post", *re_root, "--decline", "--members", "lead"],
         "foil: --decline takes no --members or --close"),
    ):
        monkeypatch.setenv("FOIL_SEAT_ID", seat)
        calls = _install_tmux(monkeypatch)
        before = [(path, path.read_bytes()) for path in _room(repo)]
        assert main(argv) == 1
        assert capsys.readouterr().err == message + "\n"
        assert [(path, path.read_bytes()) for path in _room(repo)] == before
        assert _typed(calls) == []


def test_f33_a_room_with_no_members_wakes_only_named_seats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _alive(repo)
    directory = foil_root(repo) / "fleets" / "main" / "board" / "room" / "abcd"
    directory.mkdir(parents=True)
    root = directory / "abcd.md"
    root.write_text(
        "---\ncontract: room/v1\nfrom: lead\ntime: 2026-01-01T00:00:00Z\nnudged: []\n---\n\nold\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    calls = _install_tmux(monkeypatch)
    assert main(["post", "--re", str(root), "a view, @reviewer-1"]) == 0
    assert _woken(repo, calls) == ["reviewer-1"]


def test_mail_from_foil_counts_only_with_foils_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F14: a hand-written or replaced file named as from `foil` is skipped."""
    from foil.board import mail_from, write_mail_at

    repo = _repo(tmp_path, monkeypatch)
    board = foil_root(repo) / "fleets" / "main" / "board"
    real = write_mail_at(board, "lead", "implementer-1 died.", "foil")
    forged = real.with_name(real.name[:-12] + "0badf00d.md")
    forged.write_text(real.read_text(encoding="utf-8"), encoding="utf-8")
    assert mail_from(board, "lead", real.name) is not None
    assert mail_from(board, "lead", forged.name) is None
    assert [item["path"] for item in items_for(board, "lead", set())] == [str(real.resolve())]
    # Replacing the body keeps the name but breaks the digest.
    real.write_text(
        real.read_text(encoding="utf-8").replace("died.", "forged."), encoding="utf-8"
    )
    assert mail_from(board, "lead", real.name) is None
    assert items_for(board, "lead", set()) == []
    record = board.parent / "run" / "foil" / "lead" / real.name
    record.unlink()
    record.symlink_to(tmp_path)
    assert items_for(board, "lead", set()) == []
