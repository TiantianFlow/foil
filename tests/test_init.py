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


def test_init_prints_the_pointer_and_the_lead_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    pointer = "Read .foil/skills/operator.md and follow it. My goal: <goal>."
    ask = (
        'permission = "ask". ask stops the seat at its first approval prompt; '
        "auto lets it run unattended. Edit .foil/templates/lead.toml."
    )
    auto = ask.replace('permission = "ask"', 'permission = "auto"', 1)

    assert main(["init"]) == 0
    first = capsys.readouterr()
    assert first.err == ""
    assert pointer in first.out
    assert ask in first.out
    assert "Installed harnesses, in id order (a tiebreak, not a ranking):" in first.out
    for name in ("claude", "codex", "gemini", "grok", "opencode"):
        assert name in first.out
    assert "lead: claude (first installed id)" in first.out
    assert "implementer: claude (first installed id)" in first.out
    assert "reviewer: codex (second installed id)" in first.out
    assert "two harness ids, not two programs and not two models" in first.out
    assert "Wrote templates: lead, implementer, reviewer" in first.out
    assert "documentation-writer" in first.out
    lead = foil_root(repo) / "templates" / "lead.toml"
    original = lead.read_text(encoding="utf-8")
    assert 'permission = "ask"\n' in original

    assert main(["init"]) == 0
    second = capsys.readouterr()
    assert "Left templates: lead, implementer, reviewer" in second.out
    assert "Wrote templates: none" in second.out
    assert ask in second.out
    assert lead.read_text(encoding="utf-8") == original

    edited = original.replace('permission = "ask"', 'permission = "auto"')
    lead.write_text(edited, encoding="utf-8")
    assert main(["init"]) == 0
    third = capsys.readouterr()
    assert auto in third.out
    assert ask not in third.out
    assert lead.read_text(encoding="utf-8") == edited


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
