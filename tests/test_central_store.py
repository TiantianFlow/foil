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
# Split so the source has no userinfo@host address for the publish scan.
_WITH_USERINFO = "https://pat:secret-token@" + "github.com/TiantianFlow/foil.git"
_ORDER = "kill them with the old foil (foil seat kill --all), then upgrade, then foil init"
_OLD_FOIL = "UV_TOOL_DIR=DIR UV_TOOL_BIN_DIR=DIR/bin uv tool install"
_STALE = "A stale seat whose tmux window is already gone also counts"


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


def test_init_uses_the_foil_home_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    seat = json.loads(
        (root / "fleets" / "main" / "run" / "registry.json").read_text(encoding="utf-8")
    )
    worktree = Path(seat["seats"]["implementer-1"]["worktree"])
    plan = json.loads(
        (root / "fleets" / "main" / "run" / "plans" / "implementer-1.json").read_text(
            encoding="utf-8"
        )
    )
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
        json.loads(
            (old_root / "fleets" / "main" / "run" / "registry.json").read_text(encoding="utf-8")
        )["seats"]["implementer-1"]["worktree"]
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
    registry = json.loads(
        (adopted / "fleets" / "main" / "run" / "registry.json").read_text(encoding="utf-8")
    )
    assert registry["seats"]["implementer-1"]["worktree"] == str(worktree)
    plan = json.loads(
        (adopted / "fleets" / "main" / "run" / "plans" / "implementer-1.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan["cwd"] == str(worktree)
    instruction = (
        adopted / "fleets" / "main" / "run" / "instructions" / "implementer-1.md"
    ).read_text(encoding="utf-8")
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
        (adopted / "fleets" / "main" / "run" / "plans" / "implementer-1.json").read_text(
            encoding="utf-8"
        )
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
    (old_root / "fleets" / "main" / "board" / "notes" / "kept.md").write_text(
        "state\n", encoding="utf-8"
    )
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
    assert (adopted / "fleets" / "main" / "board" / "notes" / "kept.md").read_text(
        encoding="utf-8"
    ) == "state\n"
    record = json.loads((adopted / "project.json").read_text(encoding="utf-8"))
    assert record["path"] == str(moved.resolve())
    assert record["origin"] == ""
    assert record["root"] == root_commit
    projects = Path(os.environ["FOIL_HOME"]) / "projects"
    assert [child.name for child in projects.iterdir()] == [adopted.name]

    blank = adopted.parent / "blank"
    blank.mkdir()
    (blank / "project.json").write_text(
        json.dumps({"path": str(tmp_path / "removed"), "origin": "", "created": "old"}) + "\n",
        encoding="utf-8",
    )
    again = tmp_path / "again" / "project"
    again.parent.mkdir()
    monkeypatch.chdir(tmp_path)
    shutil.move(str(moved), str(again))
    monkeypatch.chdir(again)
    capsys.readouterr()
    assert main(["init"]) == 0
    second = capsys.readouterr().out
    assert f"Adopted {adopted.name} as {foil_root(again)}" in second
    assert "orphaned" not in second
    assert blank.is_dir()
    assert not adopted.exists()


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
    assert main(["init"]) == 0
    report = capsys.readouterr().out
    assert "orphaned store record was not adopted" in report
    assert "a new project folder was created" in report
    assert "no second project was created" not in report
    assert str(orphan) in report
    assert (orphan / "board" / "notes" / "kept.md").is_file()
    created = foil_root(repo)
    assert created.is_dir()
    assert created.name != "old"
    assert (created / "project.json").is_file()
    assert {child.name for child in projects.iterdir()} == {"old", created.name}


def test_init_warns_and_names_an_orphan_with_no_root_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 0.4.0 record stored no root, and this repository has no origin."""

    _git_env(monkeypatch)
    home = Path(os.environ["FOIL_HOME"])
    projects = home / "projects"
    orphan = projects / "old"
    orphan.mkdir(parents=True)
    (orphan / "project.json").write_text(
        json.dumps(
            {
                "path": str(tmp_path / "removed"),
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
    assert main(["init"]) == 0
    report = capsys.readouterr().out
    assert "orphaned store record" in report
    assert str(orphan) in report
    created = foil_root(repo)
    assert created.name != "old"
    assert (created / "project.json").is_file()
    assert (orphan / "project.json").is_file()


def test_init_of_a_fresh_repo_ignores_an_unrelated_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No commits, and the leftover record belongs to some other origin."""

    _git_env(monkeypatch)
    home = Path(os.environ["FOIL_HOME"])
    projects = home / "projects"
    orphan = projects / "other"
    orphan.mkdir(parents=True)
    (orphan / "project.json").write_text(
        json.dumps(
            {
                "path": str(tmp_path / "deleted"),
                "origin": _ORIGIN,
                "root": "abc123",
                "created": "2026-10-10T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _repo(tmp_path / "fresh", monkeypatch)
    capsys.readouterr()
    assert main(["init"]) == 0
    report = capsys.readouterr().out
    assert "orphaned" not in report
    assert (orphan / "project.json").is_file()
    assert (foil_root(Path.cwd()) / "project.json").is_file()


def test_init_drops_http_userinfo_and_adopts_a_record_that_kept_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    home = Path(os.environ["FOIL_HOME"])
    folder = home / "projects" / "old"
    folder.mkdir(parents=True)
    (folder / "project.json").write_text(
        json.dumps(
            {
                "path": str(tmp_path / "gone"),
                "origin": _WITH_USERINFO,
                "created": "2026-10-10T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    repo = _repo(
        tmp_path / "project",
        monkeypatch,
        origin=_WITH_USERINFO,
    )
    _commit(repo)
    capsys.readouterr()
    assert main(["init"]) == 0
    report = capsys.readouterr().out
    adopted = foil_root(repo)
    assert f"Adopted {folder.name} as {adopted}" in report
    text = (adopted / "project.json").read_text(encoding="utf-8")
    assert "secret-token" not in text
    assert json.loads(text)["origin"] == _ORIGIN


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
    lead_plan = json.loads(
        (old_root / "fleets" / "main" / "run" / "plans" / "lead.json").read_text(encoding="utf-8")
    )
    assert lead_plan["cwd"] == old_checkout
    moved = tmp_path / "moved" / "project"
    moved.parent.mkdir()
    monkeypatch.chdir(tmp_path)
    shutil.move(str(repo), str(moved))
    monkeypatch.chdir(moved)
    capsys.readouterr()
    assert main(["init"]) == 0
    adopted = foil_root(moved)
    plan = json.loads(
        (adopted / "fleets" / "main" / "run" / "plans" / "lead.json").read_text(encoding="utf-8")
    )
    assert plan["cwd"] == str(moved.resolve())
    instruction = (adopted / "fleets" / "main" / "run" / "instructions" / "lead.md").read_text(
        encoding="utf-8"
    )
    assert str(old_root) not in instruction
    assert str(adopted) in instruction
    registry = json.loads(
        (adopted / "fleets" / "main" / "run" / "registry.json").read_text(encoding="utf-8")
    )
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
    moved_instruction = (store / "fleets" / "main" / "run" / "instructions" / "lead.md").read_text(
        encoding="utf-8"
    )
    assert ".foil/board" not in moved_instruction
    assert str(store / "fleets" / "main" / "board") in moved_instruction
    moved_plan = json.loads(
        (store / "fleets" / "main" / "run" / "plans" / "lead.json").read_text(encoding="utf-8")
    )
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
    kept = foil_root(quiet) / "fleets" / "main" / "board" / "notes" / "keep.txt"
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
    assert _STALE in error
    assert _ORDER in error
    assert "move it aside" not in error
    assert "move the" not in error
    assert kept.is_dir()
    assert not foil_root(busy).exists()
    assert not (foil_root(busy) / "fleets" / "main" / "board" / "marker.txt").exists()


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
    assert _STALE in error
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
    assert (foil_root(repo) / "fleets" / "main" / "board" / "marker.txt").read_text(
        encoding="utf-8"
    ) == "stay\n"


def test_a_command_names_the_upgrade_when_a_legacy_folder_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Issue 29 item 6: PATH already points at 0.4 while .foil/ is still in-repo."""

    _git_env(monkeypatch)
    repo = _repo(tmp_path / "stranded", monkeypatch)
    _commit(repo)
    legacy = repo / ".foil"
    (legacy / "board").mkdir(parents=True)
    (legacy / "run").mkdir()
    (legacy / "templates").mkdir()
    (legacy / "run" / "registry.json").write_text(
        json.dumps(
            {
                "seats": {
                    "lead": {"state": ""},
                    "implementer-1": {"state": "dead"},
                    "reviewer-1": {"state": "killed"},
                }
            }
        ),
        encoding="utf-8",
    )

    assert main(["seat", "list"]) == 1
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["mail", "list"]) == 1
    assert main(["board", "list", "*"]) == 1
    monkeypatch.delenv("FOIL_SEAT_ID")
    error = capsys.readouterr().err
    assert error.count("foil: an old .foil/") == 3
    assert "not initialized" not in error
    assert "unknown seat" not in error
    assert "lead" in error
    assert "implementer-1" in error
    assert "reviewer-1" not in error
    assert _STALE in error
    assert _ORDER in error
    assert "move it aside" not in error
    assert "move the" not in error
    assert legacy.is_dir()
    assert not foil_root(repo).exists()

    registry = json.loads((legacy / "run" / "registry.json").read_text(encoding="utf-8"))
    for record in registry["seats"].values():
        record["state"] = "killed"
    (legacy / "run" / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    assert main(["send", "lead", "still here"]) == 1
    quiet = capsys.readouterr().err
    assert quiet.startswith("foil: an old .foil/")
    assert "not initialized" not in quiet
    assert "Run foil init." in quiet
    assert "kill them" not in quiet
    assert "move it aside" not in quiet
    assert "move the" not in quiet
    assert legacy.is_dir()

    assert main(["init"]) == 0
    assert f"Moved {legacy} into {foil_root(repo)}" in capsys.readouterr().out
    assert not legacy.exists()


def test_an_unreadable_legacy_registry_names_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "garbled", monkeypatch)
    _commit(repo)
    legacy = repo / ".foil"
    (legacy / "board").mkdir(parents=True)
    (legacy / "run").mkdir()
    (legacy / "templates").mkdir()
    (legacy / "run" / "registry.json").write_text("{", encoding="utf-8")
    assert main(["init"]) == 1
    error = capsys.readouterr().err
    assert "registry that cannot be read" in error
    assert "unknown" not in error
    assert not foil_root(repo).exists()


def test_a_command_inside_a_legacy_seat_worktree_finds_the_main_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 0.3.x seat runs in .foil/worktrees/<label>, whose toplevel has no .foil/."""

    _git_env(monkeypatch)
    repo = _repo(tmp_path / "stranded", monkeypatch)
    _commit(repo)
    seat, _recorded = _legacy_seat(repo, "implementer-1")
    legacy = repo / ".foil"
    (legacy / "run" / "registry.json").write_text(
        json.dumps({"seats": {"lead": {"state": ""}, "implementer-1": {"state": "dead"}}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(seat)
    assert main(["seat", "list"]) == 1
    assert main(["board", "list", "*"]) == 1
    error = capsys.readouterr().err
    assert error.count("foil: an old .foil/") == 2
    assert "not initialized" not in error
    assert "lead" in error
    assert "implementer-1" in error
    assert legacy.is_dir()
    assert not foil_root(repo).exists()


def _legacy_seat(repo: Path, label: str) -> tuple[Path, str]:
    legacy = repo / ".foil"
    (legacy / "board").mkdir(parents=True, exist_ok=True)
    (legacy / "run" / "plans").mkdir(parents=True, exist_ok=True)
    (legacy / "run" / "instructions").mkdir(exist_ok=True)
    (legacy / "templates").mkdir(exist_ok=True)
    seat = legacy / "worktrees" / label
    _git(repo, "worktree", "add", "--quiet", "-b", f"foil/{label}", str(seat))
    recorded = (repo / ".git" / "worktrees" / label / "gitdir").read_text(encoding="utf-8")
    return seat, recorded.strip().removesuffix("/.git")


def test_a_user_worktree_and_a_foil_home_are_not_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A user's own worktree and a FOIL_HOME containing .foil- stay put."""

    _git_env(monkeypatch)
    home = tmp_path / ".foil-home"
    monkeypatch.setenv("FOIL_HOME", str(home))
    repo = _repo(tmp_path / "project", monkeypatch)
    _commit(repo)
    user_tree = repo / "worktrees" / "feat"
    _git(repo, "worktree", "add", "--quiet", "-b", "feat", str(user_tree))
    legacy = repo / ".foil"
    (legacy / "board").mkdir(parents=True)
    (legacy / "run" / "plans").mkdir(parents=True)
    (legacy / "templates").mkdir()
    checkout = str(repo.resolve())
    assert ".foil-" in str(home)
    (legacy / "run" / "registry.json").write_text(
        json.dumps({"seats": {"lead": {"state": "killed", "worktree": ""}}}) + "\n",
        encoding="utf-8",
    )
    (legacy / "run" / "plans" / "lead.json").write_text(
        json.dumps({"cwd": checkout, "note": str(home / "projects")}) + "\n",
        encoding="utf-8",
    )
    assert main(["init"]) == 0
    store = foil_root(repo)
    plan = json.loads(
        (store / "fleets" / "main" / "run" / "plans" / "lead.json").read_text(encoding="utf-8")
    )
    assert plan["cwd"] == checkout
    assert plan["note"] == str(home / "projects")
    assert str(store) not in plan["cwd"]
    assert user_tree.is_dir()
    assert "prunable" not in _git(repo, "worktree", "list", "--porcelain").stdout


def test_a_renamed_repository_with_no_seat_worktree_rewrites_the_old_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No .foil/worktrees seat, so the gitdir text cannot supply the old prefix."""

    _git_env(monkeypatch)
    repo = _repo(tmp_path / "project", monkeypatch)
    _commit(repo)
    legacy = repo / ".foil"
    (legacy / "board").mkdir(parents=True)
    (legacy / "run" / "plans").mkdir(parents=True)
    (legacy / "run" / "instructions").mkdir()
    (legacy / "templates").mkdir()
    old = str(repo.resolve())
    (legacy / "run" / "registry.json").write_text(
        json.dumps({"seats": {"lead": {"state": "killed", "worktree": ""}}}) + "\n",
        encoding="utf-8",
    )
    (legacy / "run" / "plans" / "lead.json").write_text(
        json.dumps({"cwd": old}) + "\n", encoding="utf-8"
    )
    (legacy / "run" / "instructions" / "lead.md").write_text(
        f"Board: `{legacy / 'board'}`.\n", encoding="utf-8"
    )
    renamed = tmp_path / "renamed"
    monkeypatch.chdir(tmp_path)
    shutil.move(str(repo), str(renamed))
    monkeypatch.chdir(renamed)

    capsys.readouterr()
    assert main(["init"]) == 0
    store = foil_root(renamed)
    stored = "".join(
        path.read_text(encoding="utf-8")
        for path in (store / "fleets" / "main" / "run").rglob("*")
        if path.is_file()
    )
    assert old not in stored
    assert json.dumps(old)[1:-1] not in stored
    assert str(legacy) not in stored
    plan = json.loads(
        (store / "fleets" / "main" / "run" / "plans" / "lead.json").read_text(encoding="utf-8")
    )
    assert plan["cwd"] == str(renamed.resolve())
    instruction = (store / "fleets" / "main" / "run" / "instructions" / "lead.md").read_text(
        encoding="utf-8"
    )
    assert str(store / "fleets" / "main" / "board") in instruction


def test_a_renamed_repository_rewrites_the_old_worktree_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "project", monkeypatch)
    _commit(repo)
    _seat, old_seat = _legacy_seat(repo, "implementer-1")
    legacy = repo / ".foil"
    (legacy / "run" / "registry.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fleet_id": "fleet",
                "tmux_session": "",
                "lead": "",
                "seats": {
                    "implementer-1": {
                        "name": "implementer-1",
                        "template": "implementer",
                        "harness": "fake",
                        "model": "",
                        "window_id": "",
                        "state": "killed",
                        "worktree": old_seat,
                        "branch": "foil/implementer-1",
                        "session_id": "",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (legacy / "run" / "plans" / "implementer-1.json").write_text(
        json.dumps({"cwd": old_seat}) + "\n", encoding="utf-8"
    )
    (legacy / "run" / "instructions" / "implementer-1.md").write_text(
        f"Worktree: `{old_seat}`.\nBoard: `{legacy / 'board'}`.\n", encoding="utf-8"
    )
    (legacy / "run" / "plans" / "lead.json").write_text(
        json.dumps({"cwd": str(repo.resolve())}, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    renamed = tmp_path / "重命名"
    monkeypatch.chdir(tmp_path)
    shutil.move(str(repo), str(renamed))
    monkeypatch.chdir(renamed)

    capsys.readouterr()
    assert main(["init"]) == 0
    store = foil_root(renamed)
    moved = store / "worktrees" / "implementer-1"
    capsys.readouterr()
    assert main(["seat", "list"]) == 0
    listed = capsys.readouterr().out
    assert str(moved) in listed
    assert old_seat not in listed
    stored = "".join(
        path.read_text(encoding="utf-8")
        for path in (store / "fleets" / "main" / "run").rglob("*")
        if path.is_file()
    )
    assert old_seat not in stored
    assert str(moved) in stored
    old_repo = str(repo.resolve())
    assert old_repo not in stored
    assert json.dumps(old_repo)[1:-1] not in stored
    lead = json.loads(
        (store / "fleets" / "main" / "run" / "plans" / "lead.json").read_text(encoding="utf-8")
    )
    assert lead["cwd"] == str(renamed.resolve())


def test_adopt_rewrites_a_stale_foil_project_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "orig" / "project", monkeypatch, origin=_ORIGIN)
    _commit(repo)
    assert main(["init"]) == 0
    old = foil_root(repo)
    stale = "old-project-key"
    (old / "fleets" / "main" / "run" / "plans").mkdir(parents=True, exist_ok=True)
    (old / "fleets" / "main" / "run" / "plans" / "lead.json").write_text(
        json.dumps({"cwd": str(repo.resolve()), "env": {"FOIL_PROJECT": stale}}) + "\n",
        encoding="utf-8",
    )
    moved = tmp_path / "moved" / "project"
    moved.parent.mkdir()
    monkeypatch.chdir(tmp_path)
    shutil.move(str(repo), str(moved))
    monkeypatch.chdir(moved)

    capsys.readouterr()
    assert main(["init"]) == 0
    adopted = foil_root(moved)
    plan = json.loads(
        (adopted / "fleets" / "main" / "run" / "plans" / "lead.json").read_text(encoding="utf-8")
    )
    assert plan["env"]["FOIL_PROJECT"] == project_key(common_dir(moved))
    assert stale not in (adopted / "fleets" / "main" / "run" / "plans" / "lead.json").read_text(
        encoding="utf-8"
    )


def _stored_run(root: Path) -> str:
    return "".join(
        path.read_text(encoding="utf-8")
        for path in (root / "fleets" / "main" / "run").rglob("*")
        if path.is_file()
    )


def test_a_second_init_finishes_an_adopt_repair_the_first_run_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "orig" / "project", monkeypatch, origin=_ORIGIN)
    _commit(repo)
    _launch(monkeypatch)
    assert main(["init"]) == 0
    assert main(["seat", "spawn", "lead"]) == 0
    old = foil_root(repo)
    # Spawn records the instruction's own path. Replace that with the board
    # and mail paths a later resume would read, then add a worktree seat.
    (old / "fleets" / "main" / "run" / "instructions" / "lead.md").write_text(
        "Board: `{board}`.\nMail: `{mail}`.\n".format(
            board=old / "fleets" / "main" / "board",
            mail=old / "fleets" / "main" / "board" / "mail",
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["seat", "spawn", "implementer"]) == 0
    monkeypatch.delenv("FOIL_SEAT_ID")
    # Spawn records each instruction's own path. A resume reads the board
    # and mail paths, so those are what the retry has to rewrite.
    (old / "fleets" / "main" / "run" / "instructions" / "implementer-1.md").write_text(
        "Board: `{board}`.\nMail: `{mail}`.\n".format(
            board=old / "fleets" / "main" / "board",
            mail=old / "fleets" / "main" / "board" / "mail",
        ),
        encoding="utf-8",
    )
    (old / "fleets" / "main" / "run" / "plans" / "lead.json").write_text(
        json.dumps(
            {
                "cwd": str(repo.resolve()),
                "board": str(old / "fleets" / "main" / "board"),
                "mail": str(old / "fleets" / "main" / "board" / "mail"),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    moved = tmp_path / "moved" / "project"
    moved.parent.mkdir()
    monkeypatch.chdir(tmp_path)
    shutil.move(str(repo), str(moved))
    monkeypatch.chdir(moved)
    real_run = subprocess.run
    calls = {"repair": 0}

    def broken(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "repair" in command:
            calls["repair"] += 1
            if calls["repair"] == 1:
                return subprocess.CompletedProcess(command, 1, "", "repair failed")
        return real_run(command, **kwargs)

    monkeypatch.setattr("foil.project.subprocess.run", broken)
    capsys.readouterr()
    assert main(["init"]) == 1
    adopted = foil_root(moved)
    stale = (adopted / "fleets" / "main" / "run" / "registry.json").read_text(encoding="utf-8")
    assert str(old) in stale
    capsys.readouterr()
    assert main(["init"]) == 0
    repaired = _stored_run(adopted)
    assert str(old) not in repaired
    assert str(adopted / "worktrees") in repaired
    assert str(adopted / "fleets" / "main" / "board") in repaired
    plan = json.loads(
        (adopted / "fleets" / "main" / "run" / "plans" / "lead.json").read_text(encoding="utf-8")
    )
    assert plan["board"] == str(adopted / "fleets" / "main" / "board")
    assert plan["mail"] == str(adopted / "fleets" / "main" / "board" / "mail")


def test_a_second_init_finishes_a_repair_the_first_run_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The move already happened, so only a leftover path can trigger the retry."""

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
        json.dumps({"seats": {"implementer-1": {"state": "killed", "worktree": str(seat)}}}),
        encoding="utf-8",
    )
    (legacy / "run" / "plans").mkdir()
    (legacy / "run" / "plans" / "implementer-1.json").write_text(
        json.dumps({"cwd": str(seat)}) + "\n", encoding="utf-8"
    )
    (legacy / "run" / "instructions").mkdir()
    (legacy / "run" / "instructions" / "implementer-1.md").write_text(
        f"Board: `{legacy / 'board'}`.\nMail: `{legacy / 'board' / 'mail'}`.\n",
        encoding="utf-8",
    )
    real_run = subprocess.run
    calls = {"repair": 0}

    def broken(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "repair" in command:
            calls["repair"] += 1
            if calls["repair"] == 1:
                return subprocess.CompletedProcess(command, 1, "", "repair failed")
        return real_run(command, **kwargs)

    monkeypatch.setattr("foil.project.subprocess.run", broken)
    assert main(["init"]) == 1
    assert "foil: could not repair worktrees" in capsys.readouterr().err
    store = foil_root(repo)
    moved = store / "worktrees" / "implementer-1"
    stored = (store / "fleets" / "main" / "run" / "registry.json").read_text(encoding="utf-8")
    assert str(seat) in stored
    assert str(moved) not in stored

    capsys.readouterr()
    assert main(["init"]) == 0
    repaired = _stored_run(store)
    assert str(moved) in repaired
    assert str(seat) not in repaired
    assert str(legacy) not in repaired
    assert str(store / "fleets" / "main" / "board") in repaired
    plan = json.loads(
        (store / "fleets" / "main" / "run" / "plans" / "implementer-1.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan["cwd"] == str(moved)
    assert "prunable" not in _git(repo, "worktree", "list", "--porcelain").stdout


def test_a_plain_reinit_leaves_a_missing_seat_plan_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A deleted seat worktree is not a leftover for a healthy re-init."""

    _git_env(monkeypatch)
    repo = _repo(tmp_path / "project", monkeypatch)
    _commit(repo)
    assert main(["init"]) == 0
    store = foil_root(repo)
    missing = store / "worktrees" / "implementer-1"
    (store / "fleets" / "main" / "run" / "plans").mkdir(parents=True, exist_ok=True)
    (store / "fleets" / "main" / "run" / "plans" / "implementer-1.json").write_text(
        json.dumps({"cwd": str(missing)}) + "\n", encoding="utf-8"
    )
    before = {
        path.relative_to(store / "fleets" / "main" / "run"): path.read_bytes()
        for path in (store / "fleets" / "main" / "run").rglob("*")
        if path.is_file()
    }
    capsys.readouterr()
    assert main(["init"]) == 0
    after = {
        path.relative_to(store / "fleets" / "main" / "run"): path.read_bytes()
        for path in (store / "fleets" / "main" / "run").rglob("*")
        if path.is_file()
    }
    assert after == before
    plan = json.loads(
        (store / "fleets" / "main" / "run" / "plans" / "implementer-1.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan["cwd"] == str(missing)


def test_a_failed_adopt_retries_from_the_checkout_recorded_before_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed adopt leaves the old checkout in project.json.

    The implementer worktree makes repair run. After it fails, the gitdir
    is repaired by hand, so the second init can only notice the checkout
    that was recorded before the write.
    """

    _git_env(monkeypatch)
    repo = _repo(tmp_path / "orig" / "project", monkeypatch, origin=_ORIGIN)
    _commit(repo)
    _launch(monkeypatch)
    assert main(["init"]) == 0
    assert main(["seat", "spawn", "lead"]) == 0
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["seat", "spawn", "implementer"]) == 0
    monkeypatch.delenv("FOIL_SEAT_ID")
    old = foil_root(repo)
    (old / "fleets" / "main" / "run" / "plans").mkdir(parents=True, exist_ok=True)
    (old / "fleets" / "main" / "run" / "instructions").mkdir(exist_ok=True)
    (old / "fleets" / "main" / "run" / "plans" / "lead.json").write_text(
        json.dumps({"cwd": str(repo.resolve())}) + "\n", encoding="utf-8"
    )
    (old / "fleets" / "main" / "run" / "instructions" / "lead.md").write_text(
        f"Board: `{old / 'fleets' / 'main' / 'board'}`.\n", encoding="utf-8"
    )
    moved = tmp_path / "moved" / "project"
    moved.parent.mkdir()
    monkeypatch.chdir(tmp_path)
    shutil.move(str(repo), str(moved))
    monkeypatch.chdir(moved)
    real_run = subprocess.run

    def fail_repair(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "repair" in command:
            return subprocess.CompletedProcess(command, 1, "", "repair failed")
        return real_run(command, **kwargs)

    monkeypatch.setattr("foil.project.subprocess.run", fail_repair)
    capsys.readouterr()
    assert main(["init"]) == 1
    adopted = foil_root(moved)
    record = json.loads((adopted / "project.json").read_text(encoding="utf-8"))
    assert record["path"] == str(repo.resolve())
    assert str(old) in _stored_run(adopted)

    seat = adopted / "worktrees" / "implementer-1"
    monkeypatch.setattr("foil.project.subprocess.run", real_run)
    # The worktree .git file still names the old checkout. Point it at the
    # moved checkout's admin entry, then repair, which rewrites gitdir.
    common = Path(_git(moved, "rev-parse", "--git-common-dir").stdout.strip())
    if not common.is_absolute():
        common = (moved / common).resolve()
    (seat / ".git").write_text(
        f"gitdir: {common / 'worktrees' / 'implementer-1'}\n", encoding="utf-8"
    )
    _git(moved, "worktree", "repair", str(seat))
    common = Path(_git(moved, "rev-parse", "--git-common-dir").stdout.strip())
    if not common.is_absolute():
        common = (moved / common).resolve()
    gitdir = common / "worktrees" / "implementer-1" / "gitdir"
    assert gitdir.read_text(encoding="utf-8").strip() == str(seat / ".git")
    capsys.readouterr()
    assert main(["init"]) == 0
    stored = _stored_run(adopted)
    assert str(repo.resolve()) not in stored
    assert str(old) not in stored
    assert str(adopted / "fleets" / "main" / "board") in stored
    plan = json.loads(
        (adopted / "fleets" / "main" / "run" / "plans" / "lead.json").read_text(encoding="utf-8")
    )
    assert plan["cwd"] == str(moved.resolve())
    again = json.loads((adopted / "project.json").read_text(encoding="utf-8"))
    assert again["path"] == str(moved.resolve())


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
        (foil_root(repo) / "fleets" / "main" / "run" / "registry.json").read_text(encoding="utf-8")
    )
    assert registry["seats"]["implementer-1"]["worktree"] == str(moved)
    plan = json.loads(
        (foil_root(repo) / "fleets" / "main" / "run" / "plans" / "implementer-1.json").read_text(
            encoding="utf-8"
        )
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
        json.dumps({"seats": {"implementer-1": {"state": "killed", "worktree": str(seat)}}}),
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
    stored = (foil_root(repo) / "fleets" / "main" / "run" / "registry.json").read_text(
        encoding="utf-8"
    )
    assert str(seat) in stored
    assert str(moved) not in stored


def test_a_legacy_move_replaces_a_fresh_skeleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 0.4.0 refusal left empty skeleton directories and project.json."""

    _git_env(monkeypatch)
    repo = _repo(tmp_path / "skeleton", monkeypatch)
    _commit(repo)
    legacy = repo / ".foil"
    (legacy / "board" / "notes").mkdir(parents=True)
    (legacy / "run").mkdir()
    (legacy / "templates").mkdir()
    (legacy / "board" / "notes" / "kept.md").write_text("state\n", encoding="utf-8")
    (legacy / "run" / "registry.json").write_text(
        json.dumps({"seats": {"lead": {"state": "killed"}}}) + "\n",
        encoding="utf-8",
    )
    store = foil_root(repo)
    for relative in (
        "templates/personas",
        "harnesses",
        "memory",
        "board/mail",
        "board/notes",
        "board/tasks",
        "board/results",
        "run/instructions",
        "run/plans",
        "run/fake",
        "skills",
    ):
        (store / relative).mkdir(parents=True)
    (store / "project.json").write_text(
        json.dumps(
            {
                "path": str(repo.resolve()),
                "origin": "",
                "created": "2026-10-10T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert main(["init"]) == 0
    report = capsys.readouterr().out
    assert f"Moved {legacy} into {store}" in report
    assert "already taken" not in report
    assert not legacy.exists()
    assert (store / "fleets" / "main" / "board" / "notes" / "kept.md").read_text(
        encoding="utf-8"
    ) == "state\n"
    record = json.loads((store / "project.json").read_text(encoding="utf-8"))
    assert record["path"] == str(repo.resolve())


def _seed_store(repo: Path) -> Path:
    """A store folder that already has project.json, as a refused 0.4.0 init left."""

    store = foil_root(repo)
    (store / "fleets" / "main" / "board").mkdir(parents=True)
    (store / "project.json").write_text(
        json.dumps(
            {
                "path": str(repo.resolve()),
                "origin": "",
                "created": "2026-10-10T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return store


def test_a_premature_store_still_refuses_a_legacy_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """0.4.0 wrote project.json before it refused, so the store is already initialized."""

    _git_env(monkeypatch)
    repo = _repo(tmp_path / "premature", monkeypatch)
    _commit(repo)
    legacy = repo / ".foil"
    (legacy / "board").mkdir(parents=True)
    (legacy / "run").mkdir()
    (legacy / "templates").mkdir()
    (legacy / "run" / "registry.json").write_text(
        json.dumps({"seats": {"lead": {"state": ""}}}), encoding="utf-8"
    )
    store = _seed_store(repo)
    assert main(["seat", "list"]) == 1
    error = capsys.readouterr().err
    assert "foil: an old .foil/" in error
    assert "lead" in error
    assert _STALE in error
    assert _OLD_FOIL in error
    assert "unknown seat" not in error
    assert "not initialized" not in error
    assert legacy.is_dir()
    assert (store / "project.json").is_file()


def test_a_live_fleet_in_an_initialized_store_is_still_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "collision", monkeypatch)
    _commit(repo)
    legacy = repo / ".foil"
    (legacy / "board").mkdir(parents=True)
    (legacy / "run").mkdir()
    (legacy / "templates").mkdir()
    (legacy / "run" / "registry.json").write_text(
        json.dumps({"seats": {"implementer-1": {"state": "dead"}}}),
        encoding="utf-8",
    )
    store = _seed_store(repo)
    (store / "fleets" / "main" / "board" / "notes").mkdir(parents=True)
    assert main(["init"]) == 1
    collision = capsys.readouterr()
    assert "foil: an old .foil/" in collision.out + collision.err
    assert "implementer-1" in collision.out + collision.err
    assert legacy.is_dir()
    assert (store / "project.json").is_file()
    assert main(["board", "list", "*"]) == 1
    again = capsys.readouterr().err
    assert "foil: an old .foil/" in again
    assert "implementer-1" in again
    assert _STALE in again
    assert "unknown seat" not in again


def test_a_non_simple_legacy_still_blocks_later_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git_env(monkeypatch)
    repo = _repo(tmp_path / "odd", monkeypatch)
    _commit(repo)
    legacy = repo / ".foil"
    (legacy / "board").mkdir(parents=True)
    (legacy / "templates").mkdir()
    (legacy / "run").symlink_to(legacy / "board")
    (legacy / "run" / "registry.json").write_text(
        json.dumps({"seats": {"lead": {"state": "killed"}}}), encoding="utf-8"
    )
    assert main(["init"]) == 0
    report = capsys.readouterr().out
    assert "not a simple directory tree" in report
    assert "real directories" in report
    assert "kill them" not in report
    assert (foil_root(repo) / "project.json").is_file()
    assert legacy.is_dir()
    assert main(["seat", "list"]) == 1
    error = capsys.readouterr().err
    assert "foil: an old .foil/" in error
    assert "not initialized" not in error
    assert "unknown seat" not in error


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
    (store / "fleets" / "main" / "run").mkdir(parents=True)
    (store / "templates").mkdir()
    (store / "fleets" / "main" / "run" / "owned.txt").write_text("store\n", encoding="utf-8")

    assert main(["init"]) == 0
    collision = capsys.readouterr().out
    assert "already taken" not in collision
    assert "the store already has '" in collision
    assert "kill them" not in collision
    assert "move it aside" not in collision
    assert (legacy / "board" / "notes" / "keep.txt").read_text(encoding="utf-8") == "kept\n"
    assert (legacy / "run").is_dir()
    assert (legacy / "templates").is_dir()
    assert (seat / ".git").read_text(encoding="utf-8") == gitdir
    assert not (store / "fleets" / "main" / "board" / "notes" / "keep.txt").exists()
    assert not (store / "worktrees").exists()
    assert (store / "fleets" / "main" / "run" / "owned.txt").read_text(
        encoding="utf-8"
    ) == "store\n"
    assert not (store / "templates").exists()
    listed = _git(repo, "worktree", "list", "--porcelain")
    assert str(seat) in listed.stdout
    assert "prunable" not in listed.stdout
    assert _git(seat, "status", "--porcelain").stdout == ""

    capsys.readouterr()
    assert main(["init"]) == 0
    again = capsys.readouterr().out
    assert f"Moved {legacy} into {store}" in again
    assert "the store already has '" not in again
    assert "kill them" not in again
    assert not legacy.exists()
    assert (
        store / "fleets" / "main" / "board" / "notes" / "keep.txt"
    ).read_text(encoding="utf-8") == "kept\n"
    moved = store / "worktrees" / "implementer-1"
    assert (moved / ".git").is_file()
    assert (store / "fleets" / "main" / "run" / "owned.txt").read_text(
        encoding="utf-8"
    ) == "store\n"
    listed = _git(repo, "worktree", "list", "--porcelain")
    assert str(moved) in listed.stdout
    assert "prunable" not in listed.stdout
    assert _git(moved, "status", "--porcelain").stdout == ""
