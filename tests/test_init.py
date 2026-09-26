"""Skeleton init and one-line error contracts for slice A."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import EXCLUDE_PATTERN, SKELETON, foil_root


@pytest.fixture(autouse=True)
def _outside_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)


def _identity_environ() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "Foil Test",
            "GIT_AUTHOR_EMAIL": "foil-test@localhost",
            "GIT_COMMITTER_NAME": "Foil Test",
            "GIT_COMMITTER_EMAIL": "foil-test@localhost",
        }
    )
    environment.pop("FOIL_SEAT_ID", None)
    return environment


def _init_git_repository(path: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(path)],
        check=True,
        capture_output=True,
        text=True,
        env=_identity_environ(),
    )


def _exclude_text(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "info/exclude"],
        check=True,
        capture_output=True,
        text=True,
    )
    raw = result.stdout.strip()
    path = Path(raw) if Path(raw).is_absolute() else repo / raw
    return path.read_text(encoding="utf-8")


def test_init_creates_foil_folder_and_exclude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)

    assert main(["init"]) == 0
    root = foil_root(repo)
    assert root.is_dir()
    for relative in SKELETON:
        assert (root / relative).is_dir()
    assert EXCLUDE_PATTERN in _exclude_text(repo).splitlines()

    tracked = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert ".foil" not in tracked.stdout

    assert main(["init"]) == 0
    assert EXCLUDE_PATTERN in _exclude_text(repo).splitlines()


def test_init_fails_outside_a_git_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: not a git repository\n"
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_spawn_and_send_are_one_line_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0

    assert main(["seat", "spawn", "nope"]) == 1
    first = capsys.readouterr()
    assert first.err == "foil: unknown template 'nope'\n"
    assert "Traceback" not in first.err

    assert main(["send", "operator", "hello"]) == 1
    second = capsys.readouterr()
    assert second.err == "foil: unknown seat 'operator'\n"
    assert "Traceback" not in second.err


def test_unexpected_error_is_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(_argv: list[str] | None = None) -> int:
        raise RuntimeError("secret internals")

    monkeypatch.setattr("foil.cli._run", boom)
    assert main(["init"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: unexpected error\n"
    assert "Traceback" not in captured.err
    assert "secret internals" not in captured.err


def test_worker_cannot_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer")
    assert main(["init"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: not allowed\n"
