"""Archive with real foil, real git, real tmux, and the fake agent."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root
from foil.store import SEAT_FIELDS, load_registry, registry_path
from tests.e2e.test_scenarios import _close, _prepare, _wait

pytestmark = pytest.mark.e2e


def _windows(session: str) -> set[str]:
    listed = subprocess.run(
        ["tmux", "list-windows", "-t", session, "-F", "#{window_id}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        return set()
    return {line.strip() for line in listed.stdout.splitlines() if line.strip()}


def _peek(capsys: pytest.CaptureFixture[str], name: str) -> str:
    code = main(["seat", "peek", name])
    captured = capsys.readouterr()
    if code != 0:
        return ""
    return captured.out


def _ready(repo: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    needle = f"foil-fake {name} ready"
    _wait(repo, lambda: needle in _peek(capsys, name).replace("\n", ""))


def _list(capsys: pytest.CaptureFixture[str]) -> str:
    assert main(["seat", "list"]) == 0
    return capsys.readouterr().out


def _git_ref(repo: Path, ref: str) -> bool:
    shown = subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", ref],
        check=False,
        capture_output=True,
        text=True,
    )
    return shown.returncode == 0


def test_exited_fake_leaves_a_window_until_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        fake = foil_root(repo) / "fleets" / "main" / "run" / "fake"
        fake.mkdir(parents=True, exist_ok=True)
        (fake / "implementer-1.json").write_text(
            json.dumps({"exit_on_input": True}),
            encoding="utf-8",
        )
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        _ready(repo, capsys, "implementer-1")
        seat = load_registry(repo)["seats"]["implementer-1"]
        session = str(load_registry(repo)["tmux_session"])
        window = seat["window_id"]
        assert window in _windows(session)
        assert main(["send", "implementer-1", "exit now"]) == 0
        _wait(repo, lambda: "implementer-1\timplementer\tdead\t" in _list(capsys))
        assert window in _windows(session)
        assert "foil-fake implementer-1 ready" in _peek(capsys, "implementer-1").replace("\n", "")
        assert main(["seat", "archive", "implementer-1"]) == 1
        assert capsys.readouterr().err == "foil: seat 'implementer-1' is dead\n"
        assert "implementer-1" in load_registry(repo)["seats"]
        assert not (foil_root(repo) / "fleets" / "main" / "run" / "archive").exists()
        assert main(["seat", "kill", "implementer-1"]) == 0
        assert window not in _windows(session)
        assert main(["seat", "archive", "implementer-1"]) == 0
        assert "implementer-1" not in load_registry(repo)["seats"]
    finally:
        _close(repo)


def test_send_keeps_old_mail_across_archive_and_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        _ready(repo, capsys, "implementer-1")
        seat = load_registry(repo)["seats"]["implementer-1"]
        note = Path(seat["worktree"]) / "note.txt"
        note.write_text("keep\n", encoding="utf-8")
        assert main(["send", "implementer-1", "before-archive"]) == 0
        mailbox = foil_root(repo) / "fleets" / "main" / "board" / "mail" / "implementer-1"
        before = next(mailbox.glob("*.md"))
        before_bytes = before.read_bytes()
        assert before_bytes.endswith(b"before-archive\n")
        assert main(["seat", "kill", "implementer-1"]) == 0
        assert main(["seat", "archive", "implementer-1"]) == 0
        capsys.readouterr()
        assert main(["send", "implementer-1", "after-archive"]) == 1
        assert capsys.readouterr().err == "foil: unknown seat 'implementer-1'\n"
        assert list(mailbox.glob("*.md")) == [before]
        assert before.read_bytes() == before_bytes
        assert "after-archive" not in _peek(capsys, "lead")
        assert main(["seat", "spawn", "implementer", "--name", "implementer-1"]) == 0
        _ready(repo, capsys, "implementer-1")
        window = load_registry(repo)["seats"]["implementer-1"]["window_id"]
        subprocess.run(
            ["tmux", "resize-window", "-t", window, "-x", "400"],
            check=False,
            capture_output=True,
        )
        assert main(["send", "implementer-1", "after-reuse"]) == 0
        files = list(mailbox.glob("*.md"))
        assert len(files) == 2
        assert before.read_bytes() == before_bytes
        reused = next(path for path in files if path != before)
        assert reused.read_bytes().endswith(b"after-reuse\n")
        _wait(repo, lambda: str(reused.resolve()) in _peek(capsys, "implementer-1"))
        assert str(reused.resolve()) not in _peek(capsys, "lead")
        revived = load_registry(repo)["seats"]["implementer-1"]
        assert revived["worktree"] != seat["worktree"]
        assert note.read_text(encoding="utf-8") == "keep\n"
        assert not (Path(revived["worktree"]) / "note.txt").exists()
    finally:
        _close(repo)


def test_list_shows_the_live_lead_and_the_archived_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        seat = load_registry(repo)["seats"]["implementer-1"]
        note = Path(seat["worktree"]) / "note.txt"
        note.write_text("keep\n", encoding="utf-8")
        window = seat["window_id"]
        assert main(["seat", "kill", "implementer-1"]) == 0
        assert main(["seat", "archive", "implementer-1"]) == 0
        capsys.readouterr()
        listed = _list(capsys)
        assert "lead\tlead\talive\t" in listed
        assert "implementer-1" not in listed
        assert main(["seat", "list", "--archived"]) == 0
        archived = capsys.readouterr().out.strip()
        assert archived == (
            f"implementer-1\timplementer\tkilled\t{seat['worktree']}\tfake\tfoil/implementer-1"
        )
        assert window not in archived
        assert note.is_file()
        assert main(["seat", "list", "--archived", "--json"]) == 0
        rows = json.loads(capsys.readouterr().out)
        assert len(rows) == 1
        assert set(rows[0]) == set(SEAT_FIELDS) | {"archived"}
        assert rows[0]["worktree"] == seat["worktree"]
        assert rows[0]["harness"] == "fake"
        assert rows[0]["model"] == ""
        assert rows[0]["branch"] == "foil/implementer-1"
        assert rows[0]["archived"].endswith(".txt")
    finally:
        _close(repo)


def test_lead_is_never_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        _ready(repo, capsys, "lead")
        assert main(["seat", "archive", "lead"]) == 1
        assert capsys.readouterr().err == "foil: seat 'lead' cannot be archived\n"
        assert "foil-fake lead ready" in _peek(capsys, "lead")
        assert main(["seat", "kill", "lead"]) == 0
        assert main(["seat", "archive", "lead"]) == 1
        assert capsys.readouterr().err == "foil: seat 'lead' cannot be archived\n"
        listed = _list(capsys)
        assert "lead\tlead\tkilled\t" in listed
        archive = foil_root(repo) / "fleets" / "main" / "run" / "archive"
        assert not archive.exists()
        assert main(["seat", "spawn", "lead"]) == 0
        assert not archive.exists()
        text = (foil_root(repo) / "fleets" / "main" / "run" / "instructions" / "lead.md").read_text(
            encoding="utf-8"
        )
        assert "may be from an earlier goal" in text
    finally:
        _close(repo)


def test_a_worker_cannot_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        assert main(["seat", "kill", "implementer-1"]) == 0
        before = registry_path(repo).read_bytes()
        monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
        assert main(["seat", "archive", "implementer-1"]) == 1
        assert capsys.readouterr().err == "foil: not allowed\n"
        assert registry_path(repo).read_bytes() == before
        assert not (foil_root(repo) / "fleets" / "main" / "run" / "archive").exists()
        assert "lead\tlead\talive\t" in _list(capsys)
        monkeypatch.delenv("FOIL_SEAT_ID")
        assert main(["seat", "archive", "implementer-1"]) == 0
        assert "implementer-1" not in load_registry(repo)["seats"]
    finally:
        _close(repo)


def test_a_reviewer_without_a_worktree_archives_in_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "reviewer"]) == 0
        seat = load_registry(repo)["seats"]["reviewer-1"]
        assert seat["worktree"] == ""
        assert not _git_ref(repo, "refs/heads/foil/reviewer-1")
        assert main(["seat", "kill", "reviewer-1"]) == 0
        capsys.readouterr()
        assert main(["seat", "archive", "reviewer-1"]) == 0
        assert capsys.readouterr().out == "archived seat 'reviewer-1'\n"
        assert not _git_ref(repo, "refs/heads/foil/reviewer-1")
        listed = _list(capsys)
        assert "reviewer-1" not in listed
        assert main(["seat", "list", "--archived"]) == 0
        row = capsys.readouterr().out.splitlines()[0].split("\t")
        assert row == ["reviewer-1", "reviewer", "killed", "", "fake", ""]
    finally:
        _close(repo)


def test_spawn_without_a_name_reclaims_the_archived_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        assert main(["seat", "kill", "implementer-1"]) == 0
        assert main(["seat", "archive", "implementer-1"]) == 0
        capsys.readouterr()
        assert main(["seat", "spawn", "implementer"]) == 0
        seats = load_registry(repo)["seats"]
        assert "implementer-1" in seats
        assert seats["implementer-1"]["branch"] == "foil/implementer-1-2"
        assert seats["implementer-1"]["worktree"].endswith("/implementer-1-2")
        assert _git_ref(repo, "refs/heads/foil/implementer-1")
        text = (
            foil_root(repo) / "fleets" / "main" / "run" / "instructions" / "implementer-1.md"
        ).read_text(encoding="utf-8")
        assert "Run `foil mail list` and read your mail before you act." in text
        assert text.count("may be from an earlier goal") == 1
        assert main(["seat", "spawn", "implementer"]) == 0
        assert "implementer-2" in load_registry(repo)["seats"]
        fresh = (
            foil_root(repo) / "fleets" / "main" / "run" / "instructions" / "implementer-2.md"
        ).read_text(encoding="utf-8")
        assert "earlier goal" not in fresh
    finally:
        _close(repo)


def test_a_second_archive_does_not_overwrite_the_first_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        first_tree = load_registry(repo)["seats"]["implementer-1"]["worktree"]
        assert main(["seat", "kill", "implementer-1"]) == 0
        assert main(["seat", "archive", "implementer-1"]) == 0
        capsys.readouterr()
        archive = foil_root(repo) / "fleets" / "main" / "run" / "archive"
        first = next(archive.glob("*.txt"))
        kept = first.read_bytes()
        assert main(["seat", "spawn", "implementer", "--name", "implementer-1"]) == 0
        second_tree = load_registry(repo)["seats"]["implementer-1"]["worktree"]
        assert second_tree != first_tree
        assert main(["seat", "kill", "implementer-1"]) == 0
        assert main(["seat", "archive", "implementer-1"]) == 0
        capsys.readouterr()
        files = list(archive.glob("*.txt"))
        assert len(files) == 2
        assert first.read_bytes() == kept
        other = next(path for path in files if path != first)
        assert f"worktree: {second_tree}" in other.read_text(encoding="utf-8")
        assert main(["seat", "list", "--archived"]) == 0
        rows = [line.split("\t") for line in capsys.readouterr().out.splitlines()]
        assert [row[0] for row in rows] == ["implementer-1", "implementer-1"]
        assert rows[0][3] == second_tree
        assert rows[1][3] == first_tree
    finally:
        _close(repo)
