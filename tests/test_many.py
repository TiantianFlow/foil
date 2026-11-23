"""Two goals in two repositories run at once."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

from foil.cli import main
from foil.inbox import fleet_groups
from foil.project import foil_root, store_root
from foil.store import load_registry
from tests.test_mail_board_ops import _init_git_repository


def _cwd(repo: Path) -> Path:
    seat = load_registry(repo)["seats"]["lead"]
    work = seat.get("worktree") or ""
    return Path(work) if work else repo


def _slow(home: Path) -> None:
    script = home / "slow.py"
    script.write_text(
        "import pathlib, time\n"
        "pathlib.Path('ran-start').write_text('started\\n', encoding='utf-8')\n"
        "time.sleep(15)\n"
        "pathlib.Path('ran-done').write_text('done\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    folder = home / "harnesses"
    folder.mkdir(parents=True, exist_ok=True)
    start = ", ".join(json.dumps(part) for part in [sys.executable, str(script), "{prompt}"])
    turn = ", ".join(
        json.dumps(part) for part in [sys.executable, str(script), "{session_id}", "{prompt}"]
    )
    (folder / "slow.toml").write_text(
        "\n".join(
            [
                'id = "slow"',
                f"start = [{start}]",
                f"turn = [{turn}]",
                'session_id = "generated"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_two_repos_run_at_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(tempfile.mkdtemp(prefix="foil"))
    keys: list[str] = []
    repos: list[Path] = []
    try:
        for name in ("a", "b"):
            repo = root / name
            repo.mkdir()
            _init_git_repository(repo)
            monkeypatch.chdir(repo)
            assert main(["init"]) == 0
            repos.append(repo)
            keys.append(foil_root(repo).name)
        _slow(store_root())
        capsys.readouterr()
        assert main(["roster", "update", "lead", "harness=slow"]) == 0
        groups = {group["project"] for group in fleet_groups()}
        assert set(keys) <= groups
        try:
            for key in keys:
                assert main(["seat", "spawn", "lead", "--project", key, "--as", "user"]) == 0
            places = [_cwd(repo) for repo in repos]
            started = time.monotonic() + 10
            while time.monotonic() < started:
                if all((place / "ran-start").is_file() for place in places):
                    break
                time.sleep(0.1)
            else:
                plans = []
                for repo in repos:
                    path = foil_root(repo) / "fleets" / "main" / "run" / "plans" / "lead.json"
                    plans.append(path.read_text(encoding="utf-8") if path.is_file() else "missing")
                raise AssertionError(plans)
            assert not any((place / "ran-done").exists() for place in places)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if all((place / "ran-done").is_file() for place in places):
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("one goal did not finish")
        finally:
            for key in keys:
                main(["seat", "kill", "lead", "--project", key, "--as", "user"])
    finally:
        shutil.rmtree(root, ignore_errors=True)
