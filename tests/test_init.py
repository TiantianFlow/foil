"""Skeleton init and one-line error contracts for slice A."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from foil import __version__
from foil.cli import main
from foil.project import SKELETON, foil_root, store_root


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
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_init_creates_the_store_and_leaves_git_status_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)

    assert main(["init"]) == 0
    root = foil_root(repo)
    assert root.is_dir()
    assert not (repo / ".foil").exists()
    assert root.resolve() != (repo / ".foil").resolve()
    for relative in SKELETON:
        assert (root / relative).is_dir()
    assert "/.foil/" not in _exclude_text(repo).splitlines()

    tracked = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert tracked.stdout == ""

    assert main(["init"]) == 0
    assert "/.foil/" not in _exclude_text(repo).splitlines()


def test_init_prints_the_pointer_and_the_lead_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    from importlib.resources import files

    package = Path(str(files("foil").joinpath("defaults", "skills", "operator.md")))
    pointer = f"Read {package} and follow it. My goal: <goal>."
    ask = "permission: lead auto, implementer auto, reviewer auto."
    rule = (
        "ask stops a seat at its first approval prompt; auto asks the harness to skip "
        "approval prompts, though some harnesses still ask for some commands. "
        "Set permission in roles.toml. "
        "An omitted permission is auto. "
        "auto on the lead alone does not "
        "let the workers run unattended."
    )
    auto = "permission: lead ask, implementer auto, reviewer auto."

    assert main(["init"]) == 0
    first = capsys.readouterr()
    assert first.err == ""
    assert first.out.startswith(f"foil {__version__}\n")
    assert f"Skill: packaged {package}" in first.out
    assert f"Project folder: {foil_root(repo)}" in first.out
    assert pointer in first.out
    assert ask in first.out
    assert rule in first.out
    assert "Installed harnesses, in id order (a tiebreak, not a ranking):" in first.out
    for name in ("claude", "codex", "gemini", "grok", "opencode"):
        assert name in first.out
    assert "lead: claude (first installed id)" in first.out
    assert "implementer: claude (first installed id)" in first.out
    assert "reviewer: codex (second installed id)" in first.out
    assert (
        "The three default templates use two different harness ids. "
        "Two ids can still run the same program or model; set model on a template to choose one."
    ) in first.out
    assert "Wrote templates: lead, implementer, reviewer" in first.out
    assert "documentation-writer" in first.out
    lead = store_root() / "roles.toml"
    original = lead.read_text(encoding="utf-8")
    assert 'permission = "auto"\n' in original

    assert main(["init"]) == 0
    second = capsys.readouterr()
    assert "Left templates: lead, implementer, reviewer" in second.out
    assert "Wrote templates: none" in second.out
    assert second.out.startswith(f"foil {__version__}\n")
    assert f"Skill: packaged {package}" in second.out
    assert ask in second.out
    assert lead.read_text(encoding="utf-8") == original

    edited = original.replace('permission = "auto"', 'permission = "ask"', 1)
    lead.write_text(edited, encoding="utf-8")
    assert main(["init"]) == 0
    third = capsys.readouterr()
    assert auto in third.out
    assert rule in third.out
    assert ask not in third.out
    assert lead.read_text(encoding="utf-8") == edited


def test_init_rerun_reports_the_harness_stored_after_an_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    real = shutil.which
    hidden = {"grok", "opencode", "gemini", "fake", "foil-fake"}

    def which(name: str, *args: object, **kwargs: object) -> str | None:
        if name in hidden:
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", which)
    assert main(["init"]) == 0
    capsys.readouterr()
    reviewer = store_root() / "mapping.toml"
    original = reviewer.read_text(encoding="utf-8")
    assert 'harness = "codex"' in original
    edited = original.replace('harness = "codex"', 'harness = "claude"', 1)
    reviewer.write_text(edited, encoding="utf-8")
    assert main(["init"]) == 0
    report = capsys.readouterr().out
    assert "reviewer: claude (left alone)" in report
    assert "The three default templates use one harness id." in report
    assert "different" not in report
    assert "Two ids" not in report
    assert "guarantees two harness ids" not in report
    assert reviewer.read_text(encoding="utf-8") == edited


def test_init_report_uses_three_for_three_stored_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0
    capsys.readouterr()
    mapping = store_root() / "mapping.toml"
    text = mapping.read_text(encoding="utf-8")
    text = text.replace(
        'role = "implementer"\nharness = "claude"',
        'role = "implementer"\nharness = "codex"',
        1,
    )
    text = text.replace(
        'role = "reviewer"\nharness = "codex"',
        'role = "reviewer"\nharness = "gemini"',
        1,
    )
    mapping.write_text(text, encoding="utf-8")
    assert main(["init"]) == 0
    report = capsys.readouterr().out
    assert (
        "The three default templates use three different harness ids. "
        "Two ids can still run the same program or model; set model on a template to choose one."
    ) in report


def test_init_skips_one_invalid_user_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0
    capsys.readouterr()
    harnesses = store_root() / "harnesses"
    harnesses.mkdir(parents=True, exist_ok=True)
    (harnesses / "zz.toml").write_text('id = "zz"\n', encoding="utf-8")

    assert main(["init"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Skipped harnesses/zz.toml: invalid preset" in captured.out


def test_init_closed_pipe_prints_nothing_on_stderr(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    env = _identity_environ()
    root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join([str(root / "src"), env.get("PYTHONPATH", "")])
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "foil", "init"],
            cwd=repo,
            stdout=write_fd,
            stderr=subprocess.PIPE,
            env=env,
        )
    finally:
        os.close(write_fd)
    assert proc.stderr is not None
    err = proc.stderr.read()
    code = proc.wait(timeout=30)
    assert err == b""
    assert code == 1


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


def test_init_rerun_deletes_named_project_paths_and_a_refusal_deletes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A same-checkout re-run still has the named project paths. A refused init deletes nothing."""

    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0
    capsys.readouterr()
    root = foil_root(repo)
    named = (
        root / "roles.toml",
        root / "mapping.toml",
        root / "harnesses.toml",
        root / "harnesses",
        root / "templates",
        root / "skills" / "operator.md",
        root / "skills" / "lead.md",
        root / "skills" / "worker.md",
    )
    marker = root / "harnesses" / "kept.toml"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('id = "kept"\n', encoding="utf-8")
    (root / "skills").mkdir(parents=True, exist_ok=True)
    (root / "skills" / "operator.md").write_text("old operator\n", encoding="utf-8")
    (root / "skills" / "lead.md").write_text("old lead\n", encoding="utf-8")
    (root / "skills" / "worker.md").write_text("old worker\n", encoding="utf-8")
    (root / "roles.toml").write_text("roles\n", encoding="utf-8")
    (root / "mapping.toml").write_text("maps\n", encoding="utf-8")
    (root / "harnesses.toml").write_text("catalog\n", encoding="utf-8")
    (root / "templates").mkdir(parents=True, exist_ok=True)
    assert all(path.exists() for path in named)

    monkeypatch.setenv("FOIL_SEAT_ID", "implementer")
    assert main(["init"]) == 1
    refused = capsys.readouterr()
    assert refused.err == "foil: not allowed\n"
    assert all(path.exists() or path.is_symlink() for path in named)
    assert marker.read_text(encoding="utf-8") == 'id = "kept"\n'
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)

    outside = tmp_path / "outside.toml"
    outside.write_text("untouched\n", encoding="utf-8")
    (root / "roles.toml").unlink()
    (root / "roles.toml").symlink_to(outside)
    assert main(["init"]) == 0
    assert not any(path.exists() or path.is_symlink() for path in named)
    assert (root / "project.json").is_file()
    assert (root / "fleets").is_dir()
    assert (root / "memory").is_dir()
    assert (root / "skills").is_dir()
    assert outside.read_text(encoding="utf-8") == "untouched\n"


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
