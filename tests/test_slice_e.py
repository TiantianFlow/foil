"""Slice E: kill, resume, peek, and instruction files."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from foil.cli import main
from foil.lifecycle import _git
from foil.project import foil_root
from foil.store import load_registry
from foil.tmux import TmuxController, TmuxTarget
from tests.test_spawn import _commit, _launch, _repo


def _alive(monkeypatch: pytest.MonkeyPatch, windows: set[str]) -> None:
    def matches_window(
        self: TmuxController,
        fleet_id: str,
        seat_id: str,
        session_name: str,
        window_id: str,
    ) -> bool:
        del self, fleet_id, seat_id, session_name
        return window_id in windows

    monkeypatch.setattr("foil.lifecycle.TmuxController.matches_window", matches_window)


def _stop(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    stopped: list[str] = []

    def stop(self: TmuxController, fleet_id: str, seat_id: str, target: TmuxTarget) -> bool:
        del self, fleet_id, seat_id
        stopped.append(target.window_id or "")
        return True

    monkeypatch.setattr("foil.lifecycle.TmuxController.stop_verified", stop)
    return stopped


def _pane(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    seen: list[tuple[str, int]] = []

    def capture(self: TmuxController, window_id: str, lines: int) -> str:
        del self
        seen.append((window_id, lines))
        return "pane-text\n"

    monkeypatch.setattr("foil.lifecycle.TmuxController.capture_pane", capture)
    return seen


def _lesson(capsys: pytest.CaptureFixture[str], text: str) -> str:
    assert main(["memory", "add", text]) == 0
    lesson_id = capsys.readouterr().out.strip().splitlines()[-1]
    assert main(["memory", "accept", lesson_id]) == 0
    capsys.readouterr()
    return lesson_id


def test_spawn_instruction_lists_commands_templates_and_lessons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    first = _lesson(capsys, "prefer small diffs")
    assert main(["memory", "add", "still proposed"]) == 0
    proposed = capsys.readouterr().out.strip()
    assert main(["seat", "spawn", "lead"]) == 0
    text = (foil_root(repo) / "run" / "instructions" / "lead.md").read_text(encoding="utf-8")
    board = foil_root(repo) / "board"
    assert "You are seat `lead`. Lead is `lead`." in text
    assert f"Board: `{board.resolve()}`." in text
    assert "FOIL_SEAT_ID" in text
    assert "foil seat spawn TEMPLATE" in text
    assert "foil memory accept ID" in text
    assert "There is no ack command." in text
    assert "from path" in text
    assert "status/v1" in text and "task/v1" in text and "result/v1" in text
    assert "bootstrap.json" not in text
    assert "ack-message" not in text
    assert "one worktree per fleet" not in text
    assert f"- {first}: prefer small diffs" in text
    assert proposed not in text
    assert "Templates:" in text
    assert "- implementer: harness claude, worktree yes" in text
    assert "- reviewer: harness codex, worktree no" in text
    assert "untouched." in text
    assert "none yet" not in text

    second = _lesson(capsys, "re-read the diff")
    assert second not in (foil_root(repo) / "run" / "instructions" / "lead.md").read_text(
        encoding="utf-8"
    )


def test_worker_instruction_and_resume_refreshes_lessons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    calls = _launch(monkeypatch)
    _alive(monkeypatch, set())
    first = _lesson(capsys, "prefer small diffs")
    assert main(["seat", "spawn", "lead"]) == 0
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["seat", "spawn", "implementer"]) == 0
    monkeypatch.delenv("FOIL_SEAT_ID")
    text = (foil_root(repo) / "run" / "instructions" / "implementer-1.md").read_text(
        encoding="utf-8"
    )
    assert "Stay in your worktree." in text
    assert "Templates:" not in text
    assert "foil seat kill NAME" not in text
    assert f"- {first}: prefer small diffs" in text
    second = _lesson(capsys, "re-read the diff")
    assert main(["seat", "resume", "implementer-1"]) == 0
    refreshed = (foil_root(repo) / "run" / "instructions" / "implementer-1.md").read_text(
        encoding="utf-8"
    )
    assert f"- {second}: re-read the diff" in refreshed
    plan = json.loads(Path(calls[-1]["runner_argv"][3]).read_text(encoding="utf-8"))
    assert plan["cwd"] == load_registry(repo)["seats"]["implementer-1"]["worktree"]
    assert plan["env"] == {"FOIL_SEAT_ID": "implementer-1"}
    assert "--resume" in plan["argv"]
    assert "You were restarted" not in refreshed


def test_resume_uses_last_or_continue_and_restarts_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    calls = _launch(monkeypatch)
    _alive(monkeypatch, set())
    templates = foil_root(repo) / "templates"
    templates.joinpath("codex-role.toml").write_text(
        'harness = "codex"\npermission = "auto"\nworktree = true\n',
        encoding="utf-8",
    )
    templates.joinpath("fresh.toml").write_text(
        'harness = "gemini"\npermission = "auto"\nworktree = false\n',
        encoding="utf-8",
    )
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "codex-role", "--name", "coder"]) == 0
    assert main(["seat", "spawn", "fresh", "--name", "reader"]) == 0
    assert main(["seat", "resume"]) == 0
    assert capsys.readouterr().err == ""

    coder = json.loads(
        (foil_root(repo) / "run" / "plans" / "coder.json").read_text(encoding="utf-8")
    )
    reader = json.loads(
        (foil_root(repo) / "run" / "plans" / "reader.json").read_text(encoding="utf-8")
    )
    assert coder["argv"][:4] == ["codex", "resume", "--ask-for-approval", "never"]
    assert "--last" in coder["argv"]
    assert "--resume" not in reader["argv"]
    assert reader["argv"][0] == "gemini"
    assert "--approval-mode=yolo" in reader["argv"]
    reader_text = (foil_root(repo) / "run" / "instructions" / "reader.md").read_text(
        encoding="utf-8"
    )
    mail = foil_root(repo) / "board" / "mail" / "reader"
    assert "You were restarted." in reader_text
    assert f"Re-read `{mail.resolve()}/`." in reader_text
    assert Path(reader["cwd"]) == repo.resolve()
    assert len(calls) == 6
    assert Path(coder["cwd"]) == Path(load_registry(repo)["seats"]["coder"]["worktree"])


def test_resume_without_a_worktree_starts_in_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    _alive(monkeypatch, set())
    templates = foil_root(repo) / "templates"
    lead = templates / "lead.toml"
    text = lead.read_text(encoding="utf-8").replace('harness = "claude"', 'harness = "codex"')
    lead.write_text(text)
    templates.joinpath("plain.toml").write_text(
        'harness = "opencode"\nworktree = false\n',
        encoding="utf-8",
    )
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "plain", "--name", "clerk"]) == 0
    assert main(["seat", "resume"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Traceback" not in captured.out

    project = str(repo.resolve())
    for name in ("lead", "clerk"):
        plan = json.loads(
            (foil_root(repo) / "run" / "plans" / f"{name}.json").read_text(encoding="utf-8")
        )
        text = (foil_root(repo) / "run" / "instructions" / f"{name}.md").read_text(
            encoding="utf-8"
        )
        assert plan["cwd"] == project
        assert "--last" not in plan["argv"]
        assert "--continue" not in plan["argv"]
        assert "You were restarted." in text
        assert load_registry(repo)["seats"][name]["worktree"] == ""


def test_alive_resume_does_not_relaunch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    calls = _launch(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    window = load_registry(repo)["seats"]["lead"]["window_id"]
    _alive(monkeypatch, {window})
    before = (foil_root(repo) / "run" / "instructions" / "lead.md").read_text(encoding="utf-8")
    assert main(["seat", "resume", "lead"]) == 0
    captured = capsys.readouterr()
    assert captured.out.endswith("foil: seat 'lead' is alive\n")
    assert captured.err == ""
    assert len(calls) == 1
    after = (foil_root(repo) / "run" / "instructions" / "lead.md").read_text(encoding="utf-8")
    assert after == before


def test_kill_keeps_worktree_and_blocks_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    _launch(monkeypatch)
    stopped = _stop(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    seat = load_registry(repo)["seats"]["implementer-1"]
    note = Path(seat["worktree"]) / "note.txt"
    note.write_text("keep\n", encoding="utf-8")
    assert main(["seat", "kill", "implementer-1"]) == 0
    assert stopped == [seat["window_id"]]
    assert note.read_text(encoding="utf-8") == "keep\n"
    branch = ["show-ref", "--verify", "--quiet", "refs/heads/foil/implementer-1"]
    assert _git(repo, branch).returncode == 0
    assert Path(seat["worktree"]).is_dir()
    assert load_registry(repo)["seats"]["implementer-1"]["state"] == "killed"
    assert main(["seat", "spawn", "implementer", "--name", "implementer-1"]) == 1
    assert capsys.readouterr().err == "foil: seat 'implementer-1' already exists\n"
    assert main(["seat", "resume", "implementer-1"]) == 1
    assert capsys.readouterr().err == "foil: seat 'implementer-1' is killed\n"
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


def test_kill_authority_and_kill_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    _stop(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "reviewer", "--name", "helper"]) == 0
    monkeypatch.setenv("FOIL_SEAT_ID", "helper")
    assert main(["seat", "kill", "lead"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"
    assert main(["seat", "resume", "lead"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["seat", "kill", "lead"]) == 1
    assert capsys.readouterr().err == "foil: cannot kill the lead\n"
    assert main(["seat", "kill", "--all"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"
    assert main(["seat", "kill", "helper"]) == 0
    assert load_registry(repo)["seats"]["helper"]["state"] == "killed"
    assert load_registry(repo)["seats"]["lead"]["state"] != "killed"
    monkeypatch.delenv("FOIL_SEAT_ID")
    assert main(["seat", "kill", "--all"]) == 0
    seats = load_registry(repo)["seats"]
    assert seats["lead"]["state"] == "killed"
    assert seats["helper"]["state"] == "killed"
    assert main(["seat", "kill", "missing"]) == 1
    assert capsys.readouterr().err == "foil: unknown seat 'missing'\n"


def test_list_and_peek_report_owned_facts_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    _launch(monkeypatch)
    _stop(monkeypatch)
    seen = _pane(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    lead = load_registry(repo)["seats"]["lead"]["window_id"]
    worker = load_registry(repo)["seats"]["implementer-1"]
    _alive(monkeypatch, {lead})
    assert main(["seat", "list"]) == 0
    listed = capsys.readouterr().out
    assert "lead\tlead\talive\t\n" in listed
    assert f"implementer-1\timplementer\tdead\t{worker['worktree']}\n" in listed
    assert "busy" not in listed and "window" not in listed
    assert main(["seat", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [set(row) for row in rows] == [{"name", "state", "template", "worktree"}] * 2
    assert main(["seat", "peek", "lead"]) == 0
    assert capsys.readouterr().out == "pane-text\n"
    assert seen == [(lead, 40)]
    assert main(["seat", "peek", "lead", "--lines", "7"]) == 0
    assert seen[-1] == (lead, 7)
    assert main(["seat", "peek", "implementer-1"]) == 1
    assert capsys.readouterr().err == "foil: seat 'implementer-1' is dead\n"
    assert main(["seat", "kill", "lead"]) == 0
    assert main(["seat", "peek", "lead"]) == 1
    assert capsys.readouterr().err == "foil: seat 'lead' is killed\n"
    assert main(["seat", "peek", "lead", "--lines", "0"]) == 1
    assert capsys.readouterr().err == "foil: invalid lines\n"


def test_resume_uses_the_edited_template_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    _alive(monkeypatch, set())
    template = foil_root(repo) / "templates" / "worker.toml"
    template.write_text('harness = "claude"\nworktree = false\n', encoding="utf-8")
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "worker", "--name", "worker"]) == 0
    previous = load_registry(repo)["seats"]["worker"]["session_id"]
    assert previous
    template.write_text('harness = "gemini"\nworktree = false\n', encoding="utf-8")
    assert main(["seat", "resume", "worker"]) == 0
    assert capsys.readouterr().err == ""
    seat = load_registry(repo)["seats"]["worker"]
    plan = json.loads(
        (foil_root(repo) / "run" / "plans" / "worker.json").read_text(encoding="utf-8")
    )
    assert seat["harness"] == "gemini"
    assert seat["session_id"] == ""
    assert plan["argv"][0] == "gemini"
    assert "--resume" not in plan["argv"]
    assert previous not in plan["argv"]
