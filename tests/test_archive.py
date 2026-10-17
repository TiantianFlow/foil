"""Free a killed worker's name with `foil seat archive`."""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

import pytest

from foil.cli import main
from foil.errors import FoilError
from foil.lifecycle import _git, archive_seat, resume_seats
from foil.project import _replace_text, _swap_prefix, _texts_in_run, foil_root
from foil.store import (
    SCHEMA_VERSION,
    SEAT_FIELDS,
    exclusive_lock,
    load_registry,
    registry_path,
    save_registry,
    write_json,
)
from foil.tmux import ProbeResult, ProbeState, TmuxController, TmuxError, TmuxTarget
from tests.test_central_store import _ORIGIN, _git_env
from tests.test_central_store import _commit as _store_commit
from tests.test_central_store import _repo as _store_repo
from tests.test_slice_e import _stop
from tests.test_spawn import _commit, _launch, _repo

_REAL_PROBE = TmuxController.probe

_STAMP = "20260101T000000123456Z"


def _gone(monkeypatch: pytest.MonkeyPatch) -> None:
    def probe(
        self: TmuxController, fleet_id: str, seat_id: str, target: TmuxTarget
    ) -> ProbeResult:
        del self, fleet_id, seat_id, target
        return ProbeResult(ProbeState.DEAD, False)

    monkeypatch.setattr("foil.lifecycle.TmuxController.probe", probe)


def _fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    _launch(monkeypatch)
    _stop(monkeypatch)
    _gone(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    return repo


def _seat(repo: Path) -> dict[str, str]:
    return load_registry(repo)["seats"]["implementer-1"]


def test_archive_frees_a_killed_name_and_keeps_the_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    seat = _seat(repo)
    note = Path(seat["worktree"]) / "note.txt"
    note.write_text("keep\n", encoding="utf-8")
    assert main(["send", "implementer-1", "before"]) == 0
    capsys.readouterr()
    assert main(["seat", "kill", "implementer-1"]) == 0
    root = foil_root(repo)
    plan = root / "run" / "plans" / "implementer-1.json"
    instruction = root / "run" / "instructions" / "implementer-1.md"
    assert plan.is_file() and instruction.is_file()
    # A credential-shaped field must not block the prose write.
    raw = json.loads(registry_path(repo).read_text(encoding="utf-8"))
    raw["seats"]["implementer-1"]["model"] = "sk-test"
    registry_path(repo).write_text(json.dumps(raw), encoding="utf-8")

    assert main(["seat", "archive", "implementer-1"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "archived seat 'implementer-1'\nkept branch foil/implementer-1\n"
    )
    assert str(seat["worktree"]) not in captured.out
    assert "implementer-1" not in load_registry(repo)["seats"]
    stored = json.loads(registry_path(repo).read_text(encoding="utf-8"))
    assert stored["schema_version"] == SCHEMA_VERSION == 1
    assert "archive" not in stored
    assert load_registry(repo)["schema_version"] == 1
    archives = list((root / "run" / "archive").glob("*.txt"))
    assert len(archives) == 1
    body = archives[0].read_text(encoding="utf-8")
    assert f"worktree: {seat['worktree']}" in body
    assert "branch: foil/implementer-1" in body
    assert "model: sk-test" in body
    assert not archives[0].name.endswith(".json")
    assert note.read_text(encoding="utf-8") == "keep\n"
    assert Path(seat["worktree"]).is_dir()
    branch = ["show-ref", "--verify", "--quiet", "refs/heads/foil/implementer-1"]
    assert _git(repo, branch).returncode == 0
    assert plan.is_file() and instruction.is_file()
    mailbox = root / "board" / "mail" / "implementer-1"
    assert len(list(mailbox.glob("*.md"))) == 1

    assert main(["seat", "list"]) == 0
    assert "implementer-1" not in capsys.readouterr().out
    assert main(["send", "implementer-1", "nope"]) == 1
    assert capsys.readouterr().err == "foil: unknown seat 'implementer-1'\n"
    assert len(list(mailbox.glob("*.md"))) == 1

    def boom(
        self: TmuxController, fleet_id: str, seat_id: str, target: TmuxTarget
    ) -> ProbeResult:
        del self, fleet_id, seat_id, target
        raise AssertionError("archived list probed tmux")

    monkeypatch.setattr("foil.lifecycle.TmuxController.probe", boom)
    assert main(["seat", "list", "--archived"]) == 0
    listed = capsys.readouterr().out
    row = listed.strip().split("\t")
    assert row == [
        "implementer-1",
        "implementer",
        "killed",
        seat["worktree"],
        "claude/sk-test",
        "foil/implementer-1",
    ]
    assert seat["window_id"] not in listed
    assert "You make the change" not in listed
    assert main(["seat", "list", "--archived", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert set(payload[0]) == set(SEAT_FIELDS) | {"archived"}
    assert payload[0]["archived"] == archives[0].name
    assert payload[0]["worktree"] == seat["worktree"]
    assert "description" not in payload[0]

    assert main(["seat", "spawn", "implementer", "--name", "implementer-1", "--task", "next"]) == 0
    capsys.readouterr()
    revived = _seat(repo)
    assert revived["worktree"].endswith("/implementer-1-2")
    assert revived["branch"] == "foil/implementer-1-2"
    assert revived["worktree"] != seat["worktree"]
    assert "note.txt" not in json.dumps(revived)
    assert not (Path(revived["worktree"]) / "note.txt").exists()
    text = instruction.read_text(encoding="utf-8")
    assert text.count("may be from an earlier goal") == 1
    assert main(["send", "implementer-1", "after"]) == 0
    bodies = [path.read_text(encoding="utf-8") for path in mailbox.glob("*.md")]
    assert any(body.endswith("before\n") for body in bodies)
    assert any(body.endswith("after\n") for body in bodies)
    assert len(bodies) == 3


def test_archive_refuses_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    root = foil_root(repo)
    archive = root / "run" / "archive"

    assert main(["seat", "archive", "implementer-1"]) == 1
    assert capsys.readouterr().err == "foil: seat 'implementer-1' is alive\n"
    assert not archive.exists()

    def matches_window(
        self: TmuxController,
        fleet_id: str,
        seat_id: str,
        session_name: str,
        window_id: str,
    ) -> bool:
        del self, fleet_id, seat_id, session_name, window_id
        return False

    monkeypatch.setattr("foil.lifecycle.TmuxController.matches_window", matches_window)
    assert main(["seat", "archive", "implementer-1"]) == 1
    assert capsys.readouterr().err == "foil: seat 'implementer-1' is dead\n"

    registry = load_registry(repo)
    registry["seats"]["implementer-1"]["state"] = "alive"
    save_registry(repo, registry)
    assert main(["seat", "archive", "implementer-1"]) == 1
    assert capsys.readouterr().err == "foil: seat 'implementer-1' is alive\n"
    registry["seats"]["implementer-1"]["state"] = "dead"
    save_registry(repo, registry)
    assert main(["seat", "archive", "implementer-1"]) == 1
    assert capsys.readouterr().err == "foil: seat 'implementer-1' is dead\n"
    assert not archive.exists()

    assert main(["seat", "archive", "missing"]) == 1
    assert capsys.readouterr().err == "foil: unknown seat 'missing'\n"
    assert main(["seat", "archive", "bad name"]) == 1
    assert capsys.readouterr().err == "foil: unknown seat 'bad name'\n"
    assert main(["seat", "archive", "lead"]) == 1
    assert capsys.readouterr().err == "foil: seat 'lead' cannot be archived\n"
    assert main(["seat", "kill", "lead"]) == 0
    assert main(["seat", "archive", "lead"]) == 1
    assert capsys.readouterr().err == "foil: seat 'lead' cannot be archived\n"
    assert "lead" in load_registry(repo)["seats"]
    assert not archive.exists()

    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    before = registry_path(repo).read_bytes()
    assert main(["seat", "archive", "implementer-1"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"
    assert registry_path(repo).read_bytes() == before
    assert not archive.exists()


def test_archive_follows_the_probe_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    assert main(["seat", "kill", "implementer-1"]) == 0
    capsys.readouterr()
    original = dict(_seat(repo))
    archive = foil_root(repo) / "run" / "archive"

    def restore() -> None:
        registry = load_registry(repo)
        registry["seats"]["implementer-1"] = dict(original)
        save_registry(repo, registry)
        if archive.is_dir():
            for path in archive.glob("*.txt"):
                path.unlink()

    refused = [
        (ProbeResult(ProbeState.ALIVE, True), "window is still running"),
        (ProbeResult(ProbeState.DEAD, True), "window still exists"),
        (ProbeResult(ProbeState.ALIVE, False), "window is still running"),
        (ProbeResult(ProbeState.UNKNOWN, None), "window is unknown"),
        (ProbeResult(ProbeState.DEAD, None), "window is unknown"),
    ]
    for result, message in refused:
        restore()

        def probe(
            self: TmuxController,
            fleet_id: str,
            seat_id: str,
            target: TmuxTarget,
            result: ProbeResult = result,
        ) -> ProbeResult:
            del self, fleet_id, seat_id, target
            return result

        monkeypatch.setattr("foil.lifecycle.TmuxController.probe", probe)
        assert main(["seat", "archive", "implementer-1"]) == 1
        assert capsys.readouterr().err == f"foil: seat 'implementer-1' {message}\n"
        assert "implementer-1" in load_registry(repo)["seats"]
        assert not list(archive.glob("*.txt")) if archive.exists() else True

    restore()

    def broken(
        self: TmuxController, fleet_id: str, seat_id: str, target: TmuxTarget
    ) -> ProbeResult:
        del self, fleet_id, seat_id, target
        raise TmuxError("tmux command could not be executed")

    monkeypatch.setattr("foil.lifecycle.TmuxController.probe", broken)
    assert main(["seat", "archive", "implementer-1"]) == 1
    assert capsys.readouterr().err == "foil: seat 'implementer-1' window is unknown\n"
    assert not list(archive.glob("*.txt"))


def test_real_probe_proceeds_only_when_the_window_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    assert main(["seat", "kill", "implementer-1"]) == 0
    capsys.readouterr()
    original = dict(_seat(repo))
    log = tmp_path / "tmux-log"
    executable = tmp_path / "tmux"
    executable.write_text(
        """#!/usr/bin/env python3
import os, sys
from pathlib import Path
log = Path(os.environ["FAKE_TMUX_LOG"])
log.write_text(log.read_text() + " ".join(sys.argv[1:]) + "\\n")
mode = os.environ.get("FAKE_TMUX_MODE", "missing")
if mode == "empty":
    sys.exit(0)
if mode == "bad":
    print("not-a-probe")
    sys.exit(0)
sys.exit(1)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("FAKE_TMUX_LOG", str(log))

    class Bound(TmuxController):
        probe = _REAL_PROBE

        def __init__(self, executable: str = "tmux", timeout: float = 10.0) -> None:
            super().__init__(executable=str(executable_path), timeout=timeout)

    executable_path = executable
    monkeypatch.setattr("foil.lifecycle.TmuxController", Bound)
    archive = foil_root(repo) / "run" / "archive"

    def prepare(window_id: str) -> None:
        registry = load_registry(repo)
        record = dict(original)
        record["window_id"] = window_id
        registry["seats"]["implementer-1"] = record
        save_registry(repo, registry)
        if archive.is_dir():
            for path in archive.glob("*.txt"):
                path.unlink()
        log.write_text("", encoding="utf-8")

    for window_id, mode in (
        ("", "missing"),
        ("1", "missing"),
        ("@9", "missing"),
        ("@9", "empty"),
    ):
        prepare(window_id)
        monkeypatch.setenv("FAKE_TMUX_MODE", mode)
        assert main(["seat", "archive", "implementer-1"]) == 0, (window_id, mode)
        capsys.readouterr()
        assert "implementer-1" not in load_registry(repo)["seats"]
        if window_id.startswith("@"):
            assert log.read_text(encoding="utf-8")
        else:
            assert log.read_text(encoding="utf-8") == ""

    prepare("@9")
    monkeypatch.setenv("FAKE_TMUX_MODE", "bad")
    assert main(["seat", "archive", "implementer-1"]) == 1
    assert capsys.readouterr().err == "foil: seat 'implementer-1' window is unknown\n"
    assert "implementer-1" in load_registry(repo)["seats"]


def test_auto_numbering_reclaims_an_archived_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    assert main(["seat", "kill", "implementer-1"]) == 0
    assert main(["seat", "archive", "implementer-1"]) == 0
    capsys.readouterr()
    assert main(["seat", "spawn", "implementer"]) == 0
    seats = load_registry(repo)["seats"]
    assert "implementer-1" in seats
    assert seats["implementer-1"]["branch"] == "foil/implementer-1-2"
    text = (foil_root(repo) / "run" / "instructions" / "implementer-1.md").read_text(
        encoding="utf-8"
    )
    assert "Run `foil mail list` and read your mail before you act." in text
    assert text.count("may be from an earlier goal") == 1


def test_archive_of_a_seat_without_a_worktree_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    assert main(["seat", "spawn", "reviewer", "--name", "helper"]) == 0
    assert main(["seat", "kill", "helper"]) == 0
    capsys.readouterr()
    assert main(["seat", "archive", "helper"]) == 0
    assert capsys.readouterr().out == "archived seat 'helper'\n"
    body = next((foil_root(repo) / "run" / "archive").glob("*.txt")).read_text(encoding="utf-8")
    assert "worktree: \n" in body or body.endswith("worktree: \n") or "\nworktree: \n" in body


def test_the_lead_may_archive_a_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    assert main(["seat", "kill", "implementer-1"]) == 0
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    capsys.readouterr()
    assert main(["seat", "archive", "implementer-1"]) == 0
    assert capsys.readouterr().out.startswith("archived seat 'implementer-1'\n")
    assert "implementer-1" not in load_registry(repo)["seats"]


def test_lead_respawn_does_not_archive_the_old_lead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    assert main(["seat", "kill", "lead"]) == 0
    assert main(["seat", "spawn", "lead"]) == 0
    assert not (foil_root(repo) / "run" / "archive").exists()


def test_same_second_and_a_crash_leave_two_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    assert main(["seat", "kill", "implementer-1"]) == 0
    monkeypatch.setattr("foil.lifecycle._archive_stamp", lambda: _STAMP)
    real = write_json
    calls = {"n": 0}

    def fail_once(path: Path, payload: dict) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("crash")
        real(path, payload)

    monkeypatch.setattr("foil.lifecycle.write_json", fail_once)
    with pytest.raises(OSError, match="crash"):
        archive_seat(repo, "implementer-1")
    archive = foil_root(repo) / "run" / "archive"
    first = archive / f"{_STAMP}-implementer-1.txt"
    assert first.is_file()
    kept = first.read_bytes()
    assert load_registry(repo)["seats"]["implementer-1"]["state"] == "killed"
    monkeypatch.setattr("foil.lifecycle.write_json", real)
    archive_seat(repo, "implementer-1")
    second = archive / f"{_STAMP}-implementer-1-1.txt"
    assert second.is_file()
    assert first.read_bytes() == kept
    assert "implementer-1" not in load_registry(repo)["seats"]


def test_two_archives_do_not_put_the_key_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    assert main(["seat", "kill", "implementer-1"]) == 0
    results: list[str] = []

    def run() -> None:
        try:
            archive_seat(repo, "implementer-1")
        except FoilError as exc:
            results.append(str(exc))
        else:
            results.append("ok")

    first = threading.Thread(target=run)
    second = threading.Thread(target=run)
    first.start()
    second.start()
    first.join(5)
    second.join(5)
    assert sorted(results) == ["foil: unknown seat 'implementer-1'", "ok"]
    assert "implementer-1" not in load_registry(repo)["seats"]
    assert len(list((foil_root(repo) / "run" / "archive").glob("*.txt"))) == 1


def test_init_rewrites_archive_prose_and_leaves_compact_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _store_repo(tmp_path / "orig" / "project", monkeypatch, origin=_ORIGIN)
    _store_commit(repo)
    assert main(["init"]) == 0
    old = foil_root(repo)
    archive = old / "run" / "archive"
    archive.mkdir()
    old_tree = old / "worktrees" / "implementer-1"
    prose_name = f"{_STAMP}-implementer-1.txt"
    (archive / prose_name).write_text(
        f"name: implementer-1\nworktree: {old_tree}\nbranch: foil/implementer-1\n",
        encoding="utf-8",
    )
    compact = json.dumps({"worktree": str(old_tree)}, separators=(",", ":")) + "\n"
    (archive / "seat.json").write_text(compact, encoding="utf-8")
    (archive / "parsed.txt").write_text('{"worktree": "/only/parsed"}\n', encoding="utf-8")
    moved = tmp_path / "moved" / "project"
    moved.parent.mkdir()
    monkeypatch.chdir(tmp_path)
    shutil.move(str(repo), str(moved))
    monkeypatch.chdir(moved)
    capsys.readouterr()
    assert main(["init"]) == 0
    adopted = foil_root(moved)
    text = (adopted / "run" / "archive" / prose_name).read_text(encoding="utf-8")
    assert f"worktree: {adopted / 'worktrees' / 'implementer-1'}" in text
    assert str(old) not in text
    # A quote is already a boundary, so this compact document is rewritten too.
    # The archive is prose .txt so the walk does not parse it as JSON.
    rewritten = (adopted / "run" / "archive" / "seat.json").read_text(encoding="utf-8")
    assert str(old_tree) not in rewritten
    assert str(adopted / "worktrees" / "implementer-1") in rewritten
    texts = _texts_in_run(adopted)
    assert any("/only/parsed" in item for item in texts)
    assert "/only/parsed" not in texts

    legacy_repo = _store_repo(tmp_path / "legacy", monkeypatch)
    _store_commit(legacy_repo)
    legacy = legacy_repo / ".foil"
    (legacy / "board").mkdir(parents=True)
    (legacy / "templates").mkdir()
    (legacy / "run" / "archive").mkdir(parents=True)
    (legacy / "run" / "registry.json").write_text(
        json.dumps({"seats": {"lead": {"state": "killed"}}}) + "\n",
        encoding="utf-8",
    )
    legacy_tree = legacy / "worktrees" / "implementer-1"
    (legacy / "run" / "archive" / prose_name).write_text(
        f"name: implementer-1\nworktree: {legacy_tree}\nbranch: foil/implementer-1\n",
        encoding="utf-8",
    )
    legacy_compact = json.dumps({"worktree": str(legacy_tree)}, separators=(",", ":")) + "\n"
    (legacy / "run" / "archive" / "seat.json").write_text(legacy_compact, encoding="utf-8")
    capsys.readouterr()
    assert main(["init"]) == 0
    store = foil_root(legacy_repo)
    moved_text = (store / "run" / "archive" / prose_name).read_text(encoding="utf-8")
    assert f"worktree: {store / 'worktrees' / 'implementer-1'}" in moved_text
    assert str(legacy) not in moved_text
    legacy_json = (store / "run" / "archive" / "seat.json").read_text(encoding="utf-8")
    assert str(legacy_tree) not in legacy_json
    assert str(store / "worktrees" / "implementer-1") in legacy_json


def test_archive_prose_is_a_path_the_rewrite_already_matches() -> None:
    old, new = "/old/store", "/new/store"
    prose = "worktree: /old/store/worktrees/implementer-1\n"
    assert _swap_prefix(prose, old, new) == "worktree: /new/store/worktrees/implementer-1\n"
    quoted = '{"worktree":"/old/store/worktrees/implementer-1"}\n'
    assert "/new/store/worktrees/implementer-1" in _swap_prefix(quoted, old, new)
    colon = "worktree:/old/store/worktrees/implementer-1\n"
    assert _swap_prefix(colon, old, new) == colon


def test_archived_list_ignores_an_unknown_line_and_sorts_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    assert main(["seat", "kill", "implementer-1"]) == 0
    assert main(["seat", "archive", "implementer-1"]) == 0
    capsys.readouterr()
    archive = foil_root(repo) / "run" / "archive"
    current = next(archive.glob("*.txt"))
    current.write_text(current.read_text(encoding="utf-8") + "note: ignore\n", encoding="utf-8")
    older = archive / "19990101T000000000000Z-helper.txt"
    older.write_text(
        "name: helper\ntemplate: reviewer\nharness: codex\nmodel: \n"
        "window_id: @1\nstate: killed\nworktree: \nbranch: foil/helper\n"
        "session_id: \nextra: no\n",
        encoding="utf-8",
    )
    os.utime(older, (1, 1))
    assert main(["seat", "list", "--archived"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("implementer-1\t")
    assert lines[1] == "helper\treviewer\tkilled\t\tcodex\tfoil/helper"
    assert main(["seat", "list", "--json", "--archived"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["name"] for row in rows] == ["implementer-1", "helper"]
    assert "note" not in rows[0] and "extra" not in rows[1]


def test_native_resume_does_not_repeat_the_old_mail_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    instruction = foil_root(repo) / "run" / "instructions" / "lead.md"
    assert "may be from an earlier goal" in instruction.read_text(encoding="utf-8")

    def matches_window(
        self: TmuxController,
        fleet_id: str,
        seat_id: str,
        session_name: str,
        window_id: str,
    ) -> bool:
        del self, fleet_id, seat_id, session_name, window_id
        return False

    monkeypatch.setattr("foil.lifecycle.TmuxController.matches_window", matches_window)
    assert main(["seat", "resume", "lead"]) == 0
    capsys.readouterr()
    text = instruction.read_text(encoding="utf-8")
    assert "may be from an earlier goal" not in text
    assert "You were restarted" not in text


def test_resume_does_not_unkill_a_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _fleet(tmp_path, monkeypatch)

    def matches_window(
        self: TmuxController,
        fleet_id: str,
        seat_id: str,
        session_name: str,
        window_id: str,
    ) -> bool:
        del self, fleet_id, seat_id, session_name, window_id
        return False

    monkeypatch.setattr("foil.lifecycle.TmuxController.matches_window", matches_window)
    started = threading.Event()
    release = threading.Event()

    def launch(self: TmuxController, **kwargs: object) -> TmuxTarget:
        del self
        if kwargs.get("seat_id") == "implementer-1":
            started.set()
            assert release.wait(5)
        return TmuxTarget(
            session_name=str(kwargs["session_name"]),
            window_name=str(kwargs["window_name"]),
            session_id="$1",
            window_id="@77",
        )

    monkeypatch.setattr("foil.lifecycle.TmuxController.launch", launch)
    box: dict[str, str] = {}

    def run() -> None:
        try:
            resume_seats(repo, "implementer-1")
        except FoilError as exc:
            box["error"] = str(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(5)
    assert main(["seat", "kill", "implementer-1"]) == 0
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert box["error"] == "foil: seat 'implementer-1' is killed"
    assert load_registry(repo)["seats"]["implementer-1"]["state"] == "killed"


def test_operator_skill_says_archive_keeps_the_mailbox() -> None:
    text = (
        Path(__file__).resolve().parents[1] / "src" / "foil" / "defaults" / "skills" / "operator.md"
    ).read_text(encoding="utf-8")
    assert "does not delete the worktree, the branch, the mailbox" in text


def test_kill_does_not_restore_an_archived_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    assert main(["seat", "spawn", "reviewer", "--name", "helper"]) == 0
    assert main(["seat", "kill", "implementer-1"]) == 0
    capsys.readouterr()
    started = threading.Event()
    release = threading.Event()

    def remove(self: TmuxController, fleet_id: str, seat_id: str, target: TmuxTarget) -> bool:
        del self, fleet_id, target
        if seat_id == "helper":
            started.set()
            assert release.wait(5)
        return True

    monkeypatch.setattr("foil.lifecycle.TmuxController.remove_verified", remove)
    thread = threading.Thread(target=lambda: main(["seat", "kill", "helper"]))
    thread.start()
    assert started.wait(5)
    assert main(["seat", "archive", "implementer-1"]) == 0
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    seats = load_registry(repo)["seats"]
    assert "implementer-1" not in seats
    assert seats["helper"]["state"] == "killed"


def test_spawn_does_not_restore_an_archived_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    assert main(["seat", "kill", "implementer-1"]) == 0
    capsys.readouterr()
    started = threading.Event()
    release = threading.Event()

    def launch(self: TmuxController, **kwargs: object) -> TmuxTarget:
        del self
        if kwargs.get("seat_id") == "helper":
            started.set()
            assert release.wait(5)
        return TmuxTarget(
            session_name=str(kwargs["session_name"]),
            window_name=str(kwargs["window_name"]),
            session_id="$1",
            window_id="@99",
        )

    monkeypatch.setattr("foil.lifecycle.TmuxController.launch", launch)
    box: dict[str, int] = {}

    def run() -> None:
        box["code"] = main(["seat", "spawn", "reviewer", "--name", "helper"])

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(5)
    assert main(["seat", "archive", "implementer-1"]) == 0
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert box["code"] == 0
    seats = load_registry(repo)["seats"]
    assert "implementer-1" not in seats
    assert seats["helper"]["window_id"] == "@99"


def test_registry_rewrite_does_not_restore_an_archived_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _fleet(tmp_path, monkeypatch)
    assert main(["seat", "kill", "implementer-1"]) == 0
    capsys.readouterr()
    path = registry_path(repo)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["seats"]["implementer-1"]["worktree"] = "/old/store/worktrees/implementer-1"
    raw["seats"]["lead"]["worktree"] = "/old/store/keep"
    path.write_text(json.dumps(raw), encoding="utf-8")
    held = threading.Event()
    rewrite = threading.Event()
    entered = threading.Event()
    real_lock = exclusive_lock

    def spy(lock_path: Path):
        entered.set()
        return real_lock(lock_path)

    monkeypatch.setattr("foil.store.exclusive_lock", spy)

    def hold() -> None:
        with real_lock(path.with_suffix(".lock")):
            held.set()
            assert rewrite.wait(5)
            current = json.loads(path.read_text(encoding="utf-8"))
            current["seats"].pop("implementer-1")
            path.write_text(json.dumps(current), encoding="utf-8")
            archive = path.parent / "archive"
            archive.mkdir(exist_ok=True)
            prose = archive / "20260101T000000000000Z-implementer-1.txt"
            prose.write_text(
                "worktree: /old/store/worktrees/implementer-1\n",
                encoding="utf-8",
            )

    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(5)

    def run() -> None:
        _replace_text(foil_root(repo), [("/old/store", "/new/store")])

    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(5)
    rewrite.set()
    holder.join(5)
    worker.join(5)
    assert not holder.is_alive() and not worker.is_alive()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "implementer-1" not in saved["seats"]
    assert saved["seats"]["lead"]["worktree"] == "/new/store/keep"
    prose = path.parent / "archive" / "20260101T000000000000Z-implementer-1.txt"
    assert prose.read_text(encoding="utf-8") == "worktree: /new/store/worktrees/implementer-1\n"


def test_init_rewrite_does_not_follow_a_symlinked_archive(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "seat.txt"
    target.write_text("worktree: /old/store/worktrees/implementer-1\n", encoding="utf-8")
    archive = root / "run" / "archive"
    archive.parent.mkdir(parents=True)
    archive.symlink_to(outside, target_is_directory=True)
    _replace_text(root, [("/old/store", "/new/store")])
    assert target.read_text(encoding="utf-8") == "worktree: /old/store/worktrees/implementer-1\n"
