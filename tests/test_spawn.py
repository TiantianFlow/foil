"""Slice D: spawn names, worktrees, runner plans, and identity."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from foil.cli import main
from foil.lifecycle import _git, _refused_worktree
from foil.presets import BUILTIN_IDS, load_preset
from foil.project import foil_root
from foil.store import load_registry, save_registry
from foil.tmux import TmuxController, TmuxError, TmuxTarget
from tests.test_init import _identity_environ, _init_git_repository


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0
    return repo


def _commit(repo: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "base"],
        check=True,
        capture_output=True,
        text=True,
        env=_identity_environ(),
    )


def _launch(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []
    counter = {"n": 20}

    def launch(self: TmuxController, **kwargs: object) -> TmuxTarget:
        del self
        calls.append(kwargs)
        counter["n"] += 1
        return TmuxTarget(
            session_name=str(kwargs["session_name"]),
            window_name=str(kwargs["window_name"]),
            session_id="$1",
            window_id=f"@{counter['n']}",
        )

    monkeypatch.setattr("foil.lifecycle.TmuxController.launch", launch)
    return calls


def _mail(repo: Path, seat: str) -> list[Path]:
    directory = foil_root(repo) / "board" / "mail" / seat
    return sorted(directory.glob("*.md")) if directory.is_dir() else []


def test_unknown_template_and_worker_spawn_are_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(tmp_path, monkeypatch)
    assert main(["seat", "spawn", "nope"]) == 1
    assert capsys.readouterr().err == "foil: unknown template 'nope'\n"
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer")
    assert main(["seat", "spawn", "lead"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"


def test_lead_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    assert main(["seat", "spawn", "implementer"]) == 1
    assert capsys.readouterr().err == "foil: the first seat must be the lead\n"
    assert "implementer" not in load_registry(repo)["seats"]

    assert main(["seat", "spawn", "lead", "--name", "other"]) == 1
    assert capsys.readouterr().err == "foil: lead seat must be named lead\n"

    assert main(["seat", "spawn", "lead", "--name", "lead", "--task", "ship it"]) == 0
    assert capsys.readouterr().err == ""
    assert main(["seat", "spawn", "lead"]) == 1
    assert capsys.readouterr().err == "foil: lead already exists\n"
    mail = _mail(repo, "lead")
    assert len(mail) == 1
    text = mail[0].read_text(encoding="utf-8")
    assert "from: user\n" in text
    assert text.endswith("ship it\n")


def test_spawn_writes_plan_identity_and_window_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    calls = _launch(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    registry = load_registry(repo)
    seat = registry["seats"]["lead"]
    assert seat["window_id"] == "@21"
    assert seat["template"] == "lead"
    assert seat["harness"] == "claude"
    assert seat["worktree"] == ""
    assert seat["branch"] == ""
    assert seat["state"] != "killed"
    assert seat["session_id"]
    assert registry["lead"] == "lead"
    assert registry["tmux_session"].startswith("foil-")
    assert calls[0]["session_name"] == registry["tmux_session"]
    assert calls[0]["working_directory"] == repo.resolve()
    assert calls[0]["runner_argv"][:3] == [
        calls[0]["runner_argv"][0],
        "-m",
        "foil.runner",
    ]
    plan = json.loads(Path(calls[0]["runner_argv"][3]).read_text(encoding="utf-8"))
    assert plan["env"] == {"FOIL_SEAT_ID": "lead"}
    assert plan["cwd"] == str(repo.resolve())
    assert "PATH" in plan["env_forward"]
    assert "HOME" in plan["env_forward"]
    assert "HOME" not in plan["env"]
    assert plan["argv"][0] == "claude"
    instruction = foil_root(repo) / "run" / "instructions" / "lead.md"
    text = instruction.read_text(encoding="utf-8")
    assert text in plan["argv"]
    assert "You are seat `lead`." in text
    assert "bootstrap.json" not in text


def test_launch_plan_forwards_home_for_a_user_preset_with_empty_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    calls = _launch(monkeypatch)
    harnesses = foil_root(repo) / "harnesses"
    (harnesses / "local.toml").write_text(
        'id = "local"\ncommand = ["grok", "{prompt}"]\nsession_id = "none"\nenv = []\n',
        encoding="utf-8",
    )
    (foil_root(repo) / "templates" / "lead.toml").write_text(
        'harness = "local"\n'
        'persona = "personas/lead.md"\n'
        "worktree = false\n"
        'permission = "ask"\n',
        encoding="utf-8",
    )
    assert main(["seat", "spawn", "lead"]) == 0
    plan = json.loads(Path(calls[0]["runner_argv"][3]).read_text(encoding="utf-8"))
    assert plan["argv"][0] == "grok"
    assert plan["env"] == {"FOIL_SEAT_ID": "lead"}
    assert "HOME" in plan["env_forward"]
    assert "PATH" in plan["env_forward"]
    assert plan["env_forward"].count("HOME") == 1


def test_worktree_names_stay_inside_the_foil_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    calls = _launch(monkeypatch)
    git_args: list[list[str]] = []
    real = _git

    def record(toplevel: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        git_args.append(args)
        return real(toplevel, args)

    monkeypatch.setattr("foil.lifecycle._git", record)
    assert main(["seat", "spawn", "lead"]) == 0
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["seat", "spawn", "implementer", "--task", "build it"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    assert main(["seat", "spawn", "reviewer", "--name", "helper"]) == 0
    assert capsys.readouterr().err == ""

    registry = load_registry(repo)
    first = repo / ".foil" / "worktrees" / "implementer-1"
    second = repo / ".foil" / "worktrees" / "implementer-2"
    assert Path(registry["seats"]["implementer-1"]["worktree"]) == first.resolve()
    assert registry["seats"]["implementer-1"]["branch"] == "foil/implementer-1"
    assert registry["seats"]["implementer-2"]["branch"] == "foil/implementer-2"
    assert registry["seats"]["helper"]["worktree"] == ""
    assert first.is_dir() and second.is_dir()
    assert foil_root(repo).resolve() in first.resolve().parents
    assert not (repo / "worktrees").exists()
    assert not (repo.parent / "project.foil").exists()
    assert calls[1]["working_directory"] == first.resolve()
    assert calls[2]["session_name"] == calls[0]["session_name"]
    assert all("-B" not in command for command in git_args)
    assert ["worktree", "remove"] not in [command[:2] for command in git_args]

    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""
    mail = _mail(repo, "implementer-1")[0].read_text(encoding="utf-8")
    assert "from: lead\n" in mail
    assert mail.endswith("build it\n")

    registry["seats"]["implementer-1"]["state"] = "killed"
    registry["seats"]["lead"]["state"] = "killed"
    save_registry(repo, registry)
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    assert main(["seat", "spawn", "implementer", "--name", "implementer-1"]) == 1
    assert capsys.readouterr().err == "foil: seat 'implementer-1' already exists\n"
    assert main(["seat", "spawn", "implementer"]) == 0
    assert "implementer-3" in load_registry(repo)["seats"]
    assert main(["seat", "spawn", "lead"]) == 0
    assert load_registry(repo)["seats"]["lead"]["window_id"] == "@26"
    assert load_registry(repo)["seats"]["lead"]["state"] != "killed"


def _rev(repo: Path, name: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", name],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_leftover_branch_uses_the_next_free_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    _launch(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    head = _rev(repo, "HEAD")
    for name in ("foil/implementer-1", "foil/implementer-1-2"):
        subprocess.run(
            ["git", "-C", str(repo), "branch", name],
            check=True,
            capture_output=True,
            text=True,
        )
    assert main(["seat", "spawn", "implementer"]) == 0
    assert capsys.readouterr().err == ""
    seat = load_registry(repo)["seats"]["implementer-1"]
    assert seat["branch"] == "foil/implementer-1-3"
    assert Path(seat["worktree"]).name == "implementer-1-3"
    assert _rev(repo, "foil/implementer-1") == head
    assert _rev(repo, "foil/implementer-1-2") == head


def test_existing_worktree_directory_uses_the_same_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    _launch(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    natural = repo / ".foil" / "worktrees" / "implementer-1"
    natural.mkdir(parents=True)
    assert main(["seat", "spawn", "implementer"]) == 0
    assert capsys.readouterr().err == ""
    seat = load_registry(repo)["seats"]["implementer-1"]
    assert seat["branch"] == "foil/implementer-1-2"
    worktree = repo / ".foil" / "worktrees" / "implementer-1-2"
    assert Path(seat["worktree"]) == worktree.resolve()
    assert natural.is_dir()
    assert not (repo.parent / "project.foil").exists()


def test_launch_failure_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    _launch(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0

    def boom(self: TmuxController, **kwargs: object) -> TmuxTarget:
        del self, kwargs
        raise TmuxError("hidden")

    monkeypatch.setattr("foil.lifecycle.TmuxController.launch", boom)
    assert main(["seat", "spawn", "implementer"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: could not launch seat\n"
    assert "Traceback" not in captured.err
    assert "hidden" not in captured.err
    assert "implementer-1" not in load_registry(repo)["seats"]
    assert (repo / ".foil" / "worktrees" / "implementer-1").is_dir()
    assert not (repo.parent / "project.foil").exists()


def test_worktree_paths_outside_the_foil_folder_are_refused(tmp_path: Path) -> None:
    project = tmp_path / "project"
    allowed = project / ".foil" / "worktrees" / "implementer-1"
    assert not _refused_worktree(project, allowed)
    assert _refused_worktree(project, project / "worktrees" / "implementer-1")
    assert _refused_worktree(project, project / "src" / "implementer-1")


def test_overlong_auto_name_leaves_the_registry_loadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    calls = _launch(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    name = "a" * 128
    template = foil_root(repo) / "templates" / f"{name}.toml"
    template.write_text('harness = "grok"\nworktree = true\n', encoding="utf-8")

    assert main(["seat", "spawn", name]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: could not name seat\n"
    assert "Traceback" not in captured.err

    reloaded = load_registry(repo)
    assert set(reloaded["seats"]) == {"lead"}
    assert name not in reloaded["seats"]
    assert len(calls) == 1
    assert not (repo.parent / "project.foil").exists()
    plans = foil_root(repo) / "run" / "plans"
    instructions = foil_root(repo) / "run" / "instructions"
    assert all(name not in path.name for path in plans.glob("*"))
    assert all(name not in path.name for path in instructions.glob("*"))


def test_instruction_points_at_the_role_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["seat", "spawn", "reviewer", "--name", "reader"]) == 0
    root = foil_root(repo)
    lead = (root / "run" / "instructions" / "lead.md").read_text(encoding="utf-8")
    reader = (root / "run" / "instructions" / "reader.md").read_text(encoding="utf-8")
    assert f"Skill: `{(root / 'skills' / 'lead.md').resolve()}`." in lead
    assert f"Skill: `{(root / 'skills' / 'worker.md').resolve()}`." in reader
    assert "You plan the goal, staff the fleet" in lead
    assert "You do the assigned task, stay in your own worktree" in reader
    assert "whenever you are woken." in lead
    assert "whenever you are woken." in reader


def test_launch_prompt_inlines_the_skill_and_presets_gain_no_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    calls = _launch(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    templates = foil_root(repo) / "templates"
    for harness in BUILTIN_IDS:
        if harness == "claude":
            continue
        templates.joinpath(f"via-{harness}.toml").write_text(
            f'harness = "{harness}"\nworktree = false\n',
            encoding="utf-8",
        )
        task = ["--task", "only the goal"] if harness == "fake" else []
        assert main(["seat", "spawn", f"via-{harness}", "--name", f"seat-{harness}", *task]) == 0
    assert capsys.readouterr().err == ""

    seats = {"lead": "claude"}
    seats.update({f"seat-{name}": name for name in BUILTIN_IDS if name != "claude"})
    assert len(calls) == len(seats)
    for seat, harness in seats.items():
        plan = json.loads(
            (foil_root(repo) / "run" / "plans" / f"{seat}.json").read_text(encoding="utf-8")
        )
        text = (foil_root(repo) / "run" / "instructions" / f"{seat}.md").read_text(encoding="utf-8")
        assert text in plan["argv"]
        skill = "lead.md" if seat == "lead" else "worker.md"
        skill_text = (foil_root(repo) / "skills" / skill).read_text(encoding="utf-8").strip()
        assert skill_text in text
        reread = (foil_root(repo) / "run" / "instructions" / f"{seat}.md").resolve()
        assert f"Re-read `{reread}`" in text
        assert "whenever you are woken." in text
        preset = load_preset(repo, harness)
        allowed = {
            token
            for token in (
                *preset["command"],
                *preset["permission"]["ask"],
                *preset["permission"]["auto"],
            )
            if token.startswith("-")
        }
        assert {token for token in plan["argv"] if token.startswith("-")} <= allowed
        assert not any("system-prompt" in token for token in plan["argv"])
    mail = _mail(repo, "seat-fake")
    assert len(mail) == 1
    assert mail[0].read_text(encoding="utf-8").endswith("only the goal\n")
    fake = (foil_root(repo) / "run" / "instructions" / "seat-fake.md").read_text(encoding="utf-8")
    assert "only the goal" not in fake
    fake_plan = json.loads(
        (foil_root(repo) / "run" / "plans" / "seat-fake.json").read_text(encoding="utf-8")
    )
    assert fake_plan["argv"][fake_plan["argv"].index("--prompt") + 1] == fake
