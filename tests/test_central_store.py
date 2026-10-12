"""Central per-user store: one project folder for every checkout of a repo."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from foil.cli import main
from foil.project import common_dir, foil_root, project_key, store_root

_ORIGIN = "https://github.com/TiantianFlow/foil.git"
_ORDER = (
    "kill them with the old foil (foil seat kill --all), then upgrade, "
    "then foil init"
)


def _git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Foil Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "foil-test@localhost")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Foil Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "foil-test@localhost")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(path: Path, monkeypatch: pytest.MonkeyPatch, *, origin: str = "") -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "--quiet")
    if origin:
        _git(path, "remote", "add", "origin", origin)
    monkeypatch.chdir(path)
    return path


def _commit(repo: Path) -> None:
    (repo / "README.md").write_text("store\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "--quiet", "-m", "start")


def _launch(monkeypatch: pytest.MonkeyPatch) -> None:
    def launch(self: object, **kwargs: object) -> SimpleNamespace:
        del self, kwargs
        return SimpleNamespace(window_id="@7")

    monkeypatch.setattr("foil.lifecycle.TmuxController.launch", launch)


def _exclude(repo: Path) -> str:
    result = _git(repo, "rev-parse", "--git-path", "info/exclude")
    raw = result.stdout.strip()
    path = Path(raw) if Path(raw).is_absolute() else repo / raw
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_store_root_uses_foil_home_then_xdg_then_the_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    custom = tmp_path / "custom"
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("FOIL_HOME", str(custom))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    assert store_root() == custom
    monkeypatch.setenv("FOIL_HOME", "")
    assert store_root() == xdg / "foil"
    monkeypatch.delenv("FOIL_HOME")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert store_root() == Path.home() / ".local" / "share" / "foil"

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOIL_HOME", "relative-home")
    resolved = store_root()
    assert resolved == (tmp_path / "relative-home").resolve()
    monkeypatch.chdir(elsewhere)
    assert store_root() == resolved


def test_init_uses_the_foil_home_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git_env(monkeypatch)
    first = os.environ["FOIL_HOME"]
    override = tmp_path / "override"
    monkeypatch.setenv("FOIL_HOME", str(override))
    repo = _repo(tmp_path / "project", monkeypatch)
    assert main(["init"]) == 0
    root = foil_root(repo)
    assert root.is_relative_to(override / "projects")
    assert not root.is_relative_to(Path(first))
    assert (root / "project.json").is_file()
    assert not (repo / ".foil").exists()


def test_main_linked_and_seat_worktrees_share_one_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "project", monkeypatch, origin=_ORIGIN)
    _commit(repo)
    _launch(monkeypatch)
    assert main(["init"]) == 0
    root = foil_root(repo)
    key = project_key(common_dir(repo))
    assert root.name == key
    record = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert record["path"] == str(repo.resolve())
    assert record["origin"] == _ORIGIN
    assert record["created"]

    assert main(["seat", "spawn", "lead"]) == 0
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["seat", "spawn", "implementer"]) == 0
    monkeypatch.delenv("FOIL_SEAT_ID")
    seat = json.loads((root / "run" / "registry.json").read_text(encoding="utf-8"))
    worktree = Path(seat["seats"]["implementer-1"]["worktree"])
    plan = json.loads((root / "run" / "plans" / "implementer-1.json").read_text(encoding="utf-8"))
    assert worktree.is_dir()
    assert root in worktree.parents
    assert plan["env"]["FOIL_PROJECT"] == key
    assert plan["env"]["FOIL_HOME"] == os.environ["FOIL_HOME"]
    status = _git(repo, "status", "--porcelain")
    assert status.stdout == ""
    assert "/.foil/" not in _exclude(repo).splitlines()

    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "--quiet", str(linked), "HEAD")
    monkeypatch.chdir(linked)
    assert foil_root(Path.cwd()) == root
    assert main(["seat", "list"]) == 0

    monkeypatch.chdir(worktree)
    assert common_dir(Path.cwd()) == common_dir(repo)
    assert foil_root(Path.cwd()) == root
    assert main(["board", "list", "notes/*"]) == 0

    monkeypatch.chdir(linked)
    assert main(["init"]) == 0
    again = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert again["path"] == str(repo.resolve())

    fresh = tmp_path / "fresh"
    _git(repo, "worktree", "add", "--quiet", "-b", "foil/side", str(fresh), "HEAD")
    side_home = tmp_path / "side-home"
    monkeypatch.setenv("FOIL_HOME", str(side_home))
    monkeypatch.chdir(fresh)
    assert main(["init"]) == 0
    side = json.loads((foil_root(fresh) / "project.json").read_text(encoding="utf-8"))
    assert side["path"] == str(repo.resolve())


def test_init_adopts_an_orphaned_record_and_repairs_worktrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "orig" / "project", monkeypatch, origin=_ORIGIN)
    _commit(repo)
    _launch(monkeypatch)
    assert main(["init"]) == 0
    assert main(["seat", "spawn", "lead"]) == 0
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["seat", "spawn", "implementer"]) == 0
    monkeypatch.delenv("FOIL_SEAT_ID")
    old_root = foil_root(repo)
    label = Path(
        json.loads((old_root / "run" / "registry.json").read_text(encoding="utf-8"))["seats"][
            "implementer-1"
        ]["worktree"]
    ).name
    moved = tmp_path / "moved" / "project"
    moved.parent.mkdir()
    monkeypatch.chdir(tmp_path)
    shutil.move(str(repo), str(moved))
    monkeypatch.chdir(moved)
    capsys.readouterr()
    assert main(["init"]) == 0
    report = capsys.readouterr().out
    adopted = foil_root(moved)
    assert f"Adopted {old_root.name} as {adopted}" in report
    assert not old_root.exists()
    record = json.loads((adopted / "project.json").read_text(encoding="utf-8"))
    assert record["path"] == str(moved.resolve())
    assert record["origin"] == _ORIGIN
    worktree = adopted / "worktrees" / label
    listed = _git(moved, "worktree", "list", "--porcelain")
    assert str(worktree) in listed.stdout
    assert _git(worktree, "status", "--porcelain").stdout == ""
    registry = json.loads((adopted / "run" / "registry.json").read_text(encoding="utf-8"))
    assert registry["seats"]["implementer-1"]["worktree"] == str(worktree)
    plan = json.loads(
        (adopted / "run" / "plans" / "implementer-1.json").read_text(encoding="utf-8")
    )
    assert plan["cwd"] == str(worktree)
    instruction = (adopted / "run" / "instructions" / "implementer-1.md").read_text(
        encoding="utf-8"
    )
    assert str(old_root) not in instruction
    assert str(adopted) in instruction

    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    monkeypatch.setattr(
        "foil.lifecycle.TmuxController.matches_window",
        lambda self, fleet_id, seat_id, session_name, window_id: False,
    )
    monkeypatch.setattr(
        "foil.lifecycle.TmuxController.remove_verified",
        lambda self, fleet_id, seat_id, target: True,
    )
    capsys.readouterr()
    assert main(["seat", "resume", "implementer-1"]) == 0
    resumed = json.loads(
        (adopted / "run" / "plans" / "implementer-1.json").read_text(encoding="utf-8")
    )
    assert Path(resumed["cwd"]) == worktree
    assert worktree.is_dir()


def test_init_adopts_a_moved_repo_with_no_origin_by_root_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "orig" / "project", monkeypatch)
    _commit(repo)
    (repo / "notes.txt").write_text("keep\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    _git(repo, "commit", "--quiet", "-m", "notes")
    root_commit = _git(repo, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
    assert main(["init"]) == 0
    old_root = foil_root(repo)
    (old_root / "board" / "notes" / "kept.md").write_text("state\n", encoding="utf-8")
    moved = tmp_path / "moved" / "project"
    moved.parent.mkdir()
    monkeypatch.chdir(tmp_path)
    shutil.move(str(repo), str(moved))
    monkeypatch.chdir(moved)
    capsys.readouterr()
    assert main(["init"]) == 0
    report = capsys.readouterr().out
    adopted = foil_root(moved)
    assert f"Adopted {old_root.name} as {adopted}" in report
    assert not old_root.exists()
    assert (adopted / "board" / "notes" / "kept.md").read_text(encoding="utf-8") == "state\n"
    record = json.loads((adopted / "project.json").read_text(encoding="utf-8"))
    assert record["path"] == str(moved.resolve())
    assert record["origin"] == ""
    assert record["root"] == root_commit
    projects = Path(os.environ["FOIL_HOME"]) / "projects"
    assert [child.name for child in projects.iterdir()] == [adopted.name]


def test_init_adopts_none_when_several_root_commits_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "orig" / "project", monkeypatch)
    _commit(repo)
    assert main(["init"]) == 0
    old = foil_root(repo)
    other = old.parent / "other"
    shutil.copytree(old, other)
    moved = tmp_path / "moved" / "project"
    moved.parent.mkdir()
    monkeypatch.chdir(tmp_path)
    shutil.move(str(repo), str(moved))
    monkeypatch.chdir(moved)
    capsys.readouterr()
    assert main(["init"]) == 0
    report = capsys.readouterr().out
    assert "several stored projects match this repository" in report
    assert old.is_dir()
    assert other.is_dir()
    created = foil_root(moved)
    assert created.name not in {old.name, other.name}
    assert (created / "project.json").is_file()


def test_init_warns_instead_of_creating_a_second_project_for_an_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    home = Path(os.environ["FOIL_HOME"])
    projects = home / "projects"
    orphan = projects / "old"
    (orphan / "board" / "notes").mkdir(parents=True)
    (orphan / "board" / "notes" / "kept.md").write_text("state\n", encoding="utf-8")
    (orphan / "project.json").write_text(
        json.dumps(
            {
                "path": str(tmp_path / "missing"),
                "origin": "",
                "created": "2026-10-10T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    repo = _repo(tmp_path / "project", monkeypatch)
    _commit(repo)
    capsys.readouterr()
    assert main(["init"]) == 1
    report = capsys.readouterr()
    assert "orphaned store record" in report.err
    assert (orphan / "board" / "notes" / "kept.md").is_file()
    assert [child.name for child in projects.iterdir()] == ["old"]


def test_init_says_when_several_orphans_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    home = Path(os.environ["FOIL_HOME"])
    projects = home / "projects"
    for name in ("one", "two"):
        folder = projects / name
        (folder / "board").mkdir(parents=True)
        (folder / "project.json").write_text(
            json.dumps(
                {
                    "path": str(tmp_path / "gone" / name),
                    "origin": _ORIGIN,
                    "created": "2026-10-10T00:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
    repo = _repo(tmp_path / "project", monkeypatch, origin=_ORIGIN)
    _commit(repo)
    assert main(["init"]) == 0
    report = capsys.readouterr().out
    assert "several stored projects match this origin" in report
    assert (projects / "one").is_dir()
    assert (projects / "two").is_dir()
    created = foil_root(repo)
    assert created.name not in {"one", "two"}
    assert (created / "project.json").is_file()


def test_lead_only_adopt_and_legacy_move_rewrite_stored_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "orig" / "project", monkeypatch, origin=_ORIGIN)
    _commit(repo)
    _launch(monkeypatch)
    assert main(["init"]) == 0
    assert main(["seat", "spawn", "lead"]) == 0
    old_root = foil_root(repo)
    old_checkout = str(repo.resolve())
    lead_plan = json.loads((old_root / "run" / "plans" / "lead.json").read_text(encoding="utf-8"))
    assert lead_plan["cwd"] == old_checkout
    moved = tmp_path / "moved" / "project"
    moved.parent.mkdir()
    monkeypatch.chdir(tmp_path)
    shutil.move(str(repo), str(moved))
    monkeypatch.chdir(moved)
    capsys.readouterr()
    assert main(["init"]) == 0
    adopted = foil_root(moved)
    plan = json.loads((adopted / "run" / "plans" / "lead.json").read_text(encoding="utf-8"))
    assert plan["cwd"] == str(moved.resolve())
    instruction = (adopted / "run" / "instructions" / "lead.md").read_text(encoding="utf-8")
    assert str(old_root) not in instruction
    assert str(adopted) in instruction
    registry = json.loads((adopted / "run" / "registry.json").read_text(encoding="utf-8"))
    assert str(old_root) not in json.dumps(registry)

    legacy_repo = _repo(tmp_path / "legacy", monkeypatch)
    _commit(legacy_repo)
    legacy = legacy_repo / ".foil"
    (legacy / "board").mkdir(parents=True)
    (legacy / "run" / "plans").mkdir(parents=True)
    (legacy / "run" / "instructions").mkdir()
    (legacy / "templates").mkdir()
    (legacy / "run" / "registry.json").write_text(
        json.dumps({"seats": {"lead": {"state": "killed", "worktree": ""}}}) + "\n",
        encoding="utf-8",
    )
    (legacy / "run" / "plans" / "lead.json").write_text(
        json.dumps({"cwd": str(legacy_repo.resolve())}) + "\n", encoding="utf-8"
    )
    (legacy / "run" / "instructions" / "lead.md").write_text(
        f"Board: `{legacy / 'board'}`.\n", encoding="utf-8"
    )
    capsys.readouterr()
    assert main(["init"]) == 0
    store = foil_root(legacy_repo)
    assert not legacy.exists()
    moved_instruction = (store / "run" / "instructions" / "lead.md").read_text(encoding="utf-8")
    assert ".foil/board" not in moved_instruction
    assert str(store / "board") in moved_instruction
    moved_plan = json.loads((store / "run" / "plans" / "lead.json").read_text(encoding="utf-8"))
    assert moved_plan["cwd"] == str(legacy_repo.resolve())


def test_init_moves_a_quiet_legacy_folder_and_instructs_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    quiet = _repo(tmp_path / "quiet", monkeypatch)
    legacy = quiet / ".foil"
    (legacy / "board" / "notes").mkdir(parents=True)
    (legacy / "run").mkdir()
    (legacy / "templates").mkdir()
    (legacy / "board" / "notes" / "keep.txt").write_text("kept\n", encoding="utf-8")
    assert main(["init"]) == 0
    moved = capsys.readouterr().out
    assert f"Moved {legacy} into {foil_root(quiet)}" in moved
    assert not legacy.exists()
    kept = foil_root(quiet) / "board" / "notes" / "keep.txt"
    assert kept.read_text(encoding="utf-8") == "kept\n"

    busy = _repo(tmp_path / "busy", monkeypatch)
    kept = busy / ".foil"
    (kept / "board").mkdir(parents=True)
    (kept / "run").mkdir()
    (kept / "templates").mkdir()
    (kept / "run" / "registry.json").write_text(
        json.dumps({"seats": {"lead": {"state": ""}}}), encoding="utf-8"
    )
    (kept / "board" / "marker.txt").write_text("stay\n", encoding="utf-8")
    assert main(["init"]) == 1
    error = capsys.readouterr().err
    assert "lead" in error
    assert "stale seat" in error
    assert _ORDER in error
    assert "move it aside" not in error
    assert "move the" not in error
    assert kept.is_dir()
    assert not foil_root(busy).exists()
    assert not (foil_root(busy) / "board" / "marker.txt").exists()


def test_init_refuses_a_stale_seat_without_creating_a_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "stale", monkeypatch)
    legacy = repo / ".foil"
    (legacy / "board").mkdir(parents=True)
    (legacy / "run").mkdir()
    (legacy / "templates").mkdir()
    (legacy / "run" / "registry.json").write_text(
        json.dumps(
            {
                "seats": {
                    "lead": {"state": ""},
                    "implementer-1": {"state": "killed"},
                }
            }
        ),
        encoding="utf-8",
    )
    (legacy / "board" / "marker.txt").write_text("stay\n", encoding="utf-8")

    assert main(["init"]) == 1
    error = capsys.readouterr().err
    assert "lead" in error
    assert "implementer-1" not in error
    assert "stale seat" in error
    assert _ORDER in error
    assert "move it aside" not in error
    assert "move the" not in error
    assert legacy.is_dir()
    assert not foil_root(repo).exists()

    registry = json.loads((legacy / "run" / "registry.json").read_text(encoding="utf-8"))
    registry["seats"]["lead"]["state"] = "killed"
    (legacy / "run" / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    assert main(["init"]) == 0
    assert f"Moved {legacy} into {foil_root(repo)}" in capsys.readouterr().out
    assert not legacy.exists()
    assert (foil_root(repo) / "board" / "marker.txt").read_text(encoding="utf-8") == "stay\n"


def test_init_repairs_a_killed_seat_worktree_during_a_legacy_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "project", monkeypatch)
    _commit(repo)
    legacy = repo / ".foil"
    (legacy / "board").mkdir(parents=True)
    (legacy / "run").mkdir()
    (legacy / "templates").mkdir()
    seat = legacy / "worktrees" / "implementer-1"
    _git(repo, "worktree", "add", "--quiet", "-b", "foil/implementer-1", str(seat))
    (legacy / "run" / "registry.json").write_text(
        json.dumps(
            {
                "seats": {
                    "implementer-1": {
                        "state": "killed",
                        "worktree": str(seat),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (legacy / "run" / "plans").mkdir()
    (legacy / "run" / "plans" / "implementer-1.json").write_text(
        json.dumps({"cwd": str(seat)}) + "\n", encoding="utf-8"
    )

    assert main(["init"]) == 0
    assert f"Moved {legacy} into {foil_root(repo)}" in capsys.readouterr().out
    assert not legacy.exists()
    moved = foil_root(repo) / "worktrees" / "implementer-1"
    listed = _git(repo, "worktree", "list", "--porcelain")
    assert str(moved) in listed.stdout
    assert "prunable" not in listed.stdout
    assert _git(moved, "status", "--porcelain").stdout == ""
    registry = json.loads(
        (foil_root(repo) / "run" / "registry.json").read_text(encoding="utf-8")
    )
    assert registry["seats"]["implementer-1"]["worktree"] == str(moved)
    plan = json.loads(
        (foil_root(repo) / "run" / "plans" / "implementer-1.json").read_text(encoding="utf-8")
    )
    assert plan["cwd"] == str(moved)


def test_init_reports_a_failed_worktree_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "project", monkeypatch)
    _commit(repo)
    legacy = repo / ".foil"
    (legacy / "board").mkdir(parents=True)
    (legacy / "run").mkdir()
    (legacy / "templates").mkdir()
    seat = legacy / "worktrees" / "implementer-1"
    _git(repo, "worktree", "add", "--quiet", "-b", "foil/implementer-1", str(seat))
    (legacy / "run" / "registry.json").write_text(
        json.dumps({"seats": {"implementer-1": {"state": "killed"}}}),
        encoding="utf-8",
    )
    real_run = subprocess.run

    def broken(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "repair" in command:
            return subprocess.CompletedProcess(command, 1, "", "repair failed")
        return real_run(command, **kwargs)

    monkeypatch.setattr("foil.project.subprocess.run", broken)
    assert main(["init"]) == 1
    assert "foil: could not repair worktrees" in capsys.readouterr().err
    moved = foil_root(repo) / "worktrees" / "implementer-1"
    assert moved.is_dir()
    assert not legacy.exists()


def test_a_colliding_legacy_move_leaves_the_old_directory_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "project", monkeypatch)
    _commit(repo)
    legacy = repo / ".foil"
    (legacy / "board" / "notes").mkdir(parents=True)
    (legacy / "run").mkdir()
    (legacy / "templates").mkdir()
    (legacy / "board" / "notes" / "keep.txt").write_text("kept\n", encoding="utf-8")
    seat = legacy / "worktrees" / "implementer-1"
    _git(repo, "worktree", "add", "--quiet", "-b", "foil/implementer-1", str(seat))
    gitdir = (seat / ".git").read_text(encoding="utf-8")
    store = foil_root(repo)
    (store / "run").mkdir(parents=True)
    (store / "templates").mkdir()
    (store / "run" / "owned.txt").write_text("store\n", encoding="utf-8")

    assert main(["init"]) == 0
    collision = capsys.readouterr().out
    assert _ORDER in collision
    assert "already taken" in collision
    assert "move it aside" not in collision
    assert (legacy / "board" / "notes" / "keep.txt").read_text(encoding="utf-8") == "kept\n"
    assert (legacy / "run").is_dir()
    assert (legacy / "templates").is_dir()
    assert (seat / ".git").read_text(encoding="utf-8") == gitdir
    assert not (store / "board" / "notes" / "keep.txt").exists()
    assert not (store / "worktrees").exists()
    assert (store / "run" / "owned.txt").read_text(encoding="utf-8") == "store\n"
    listed = _git(repo, "worktree", "list", "--porcelain")
    assert str(seat) in listed.stdout
    assert "prunable" not in listed.stdout
    assert _git(seat, "status", "--porcelain").stdout == ""

    capsys.readouterr()
    assert main(["init"]) == 0
    assert _ORDER in capsys.readouterr().out
    assert (legacy / "board" / "notes" / "keep.txt").is_file()
    assert (seat / ".git").is_file()
