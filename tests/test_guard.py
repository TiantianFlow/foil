"""Seat-only git hooks: private text and the worktree boundary."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from foil.guard import hook_environment
from foil.project import foil_root
from tests.test_slice_b import _repo


def _seat_git(env: dict[str, str], cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)


@pytest.fixture
def seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, str]]:
    repo = _repo(tmp_path, monkeypatch)
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "FOIL_SEAT_ID": "implementer-1",
        "GIT_AUTHOR_NAME": "Foil Test",
        "GIT_AUTHOR_EMAIL": "foil-test@localhost",
        "GIT_COMMITTER_NAME": "Foil Test",
        "GIT_COMMITTER_EMAIL": "foil-test@localhost",
    }
    env.update(hook_environment(repo, repo, ""))
    _seat_git(env, repo, "config", "user.email", "alice@mail.test")
    return repo, env


def _stage(repo: Path, env: dict[str, str], text: str) -> None:
    (repo / "notes.txt").write_text(text, encoding="utf-8")
    assert _seat_git(env, repo, "add", "notes.txt").returncode == 0


def test_hooks_are_written_in_the_store_and_not_the_repository(
    seat: tuple[Path, dict[str, str]],
) -> None:
    repo, env = seat
    hooks = foil_root(repo) / "hooks"
    assert env["GIT_CONFIG_VALUE_0"] == str(hooks.resolve())
    for name in ("pre-commit", "pre-push"):
        assert os.access(hooks / name, os.X_OK)
    assert _seat_git(env, repo, "status", "--porcelain").stdout == ""


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("see {home}/project\n", "a home directory path"),
        ("mail alice@mail.test\n", "a personal email address"),
        ("token ghp_" + "a" * 36 + "\n", "credential-shaped text"),
    ],
)
def test_a_commit_that_adds_private_text_is_refused(
    seat: tuple[Path, dict[str, str]], text: str, kind: str
) -> None:
    repo, env = seat
    _stage(repo, env, text.format(home=env["HOME"]))
    done = _seat_git(env, repo, "commit", "-m", "add notes")
    assert done.returncode != 0
    assert f"foil: commit refused: the staged change adds {kind} in notes.txt" in done.stderr
    assert _seat_git(env, repo, "rev-list", "--all").stdout == ""


def test_a_clean_commit_runs_the_repository_hook_too(seat: tuple[Path, dict[str, str]]) -> None:
    repo, env = seat
    marker = repo.parent / "ran"
    hook = Path(env["FOIL_REPO_HOOKS"]) / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    _stage(repo, env, "plain text\n")
    assert _seat_git(env, repo, "commit", "-m", "add notes").returncode == 0
    assert marker.exists()
    hook.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    _stage(repo, env, "more text\n")
    assert _seat_git(env, repo, "commit", "-m", "more").returncode != 0


def test_a_seat_commit_outside_its_worktree_is_refused(
    seat: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, env = seat
    env = {**env, "FOIL_WORKTREE": str(tmp_path / "elsewhere")}
    _stage(repo, env, "plain text\n")
    done = _seat_git(env, repo, "commit", "-m", "add notes")
    assert done.returncode != 0
    assert "foil: commit refused: seat implementer-1 works in " in done.stderr


def test_a_push_of_a_commit_with_private_text_is_refused(
    seat: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, env = seat
    remote = tmp_path / "remote.git"
    assert _seat_git(env, tmp_path, "init", "--bare", "--quiet", str(remote)).returncode == 0
    _seat_git(env, repo, "remote", "add", "origin", str(remote))
    _stage(repo, env, "plain text\n")
    message = f"fix the path {env['HOME']}/project"
    assert _seat_git(env, repo, "commit", "-m", message).returncode == 0
    done = _seat_git(env, repo, "push", "origin", "HEAD:refs/heads/topic")
    assert done.returncode != 0
    assert "foil: push refused: commit " in done.stderr
    assert "adds a home directory path in the commit message" in done.stderr
    assert _seat_git(env, remote, "branch", "--list").stdout == ""
    assert _seat_git(env, repo, "commit", "--amend", "-m", "fix the path").returncode == 0
    assert _seat_git(env, repo, "push", "origin", "HEAD:refs/heads/topic").returncode == 0


def test_the_guard_is_only_in_the_seat_environment(seat: tuple[Path, dict[str, str]]) -> None:
    repo, env = seat
    seat_only = ("GIT_CONFIG", "FOIL_")
    outside = {key: value for key, value in env.items() if not key.startswith(seat_only)}
    _stage(repo, outside, f"see {env['HOME']}/project\n")
    assert _seat_git(outside, repo, "commit", "-m", "the human's own commit").returncode == 0
