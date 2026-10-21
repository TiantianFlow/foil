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
    directory = foil_root(repo) / "fleets" / "main" / "board" / "mail" / seat
    return sorted(directory.glob("*.md")) if directory.is_dir() else []


def _set_entries(repo: Path, role: str, entries: list[tuple[str, str]]) -> None:
    from foil.presets import read_maps, save_maps

    maps = [item for item in read_maps(repo) if item["role"] != role]
    for harness, model in entries:
        maps.append({"role": role, "id": harness, "model": model})
    save_maps(repo, maps)


def _set_persona(repo: Path, role: str, persona: str) -> None:
    from foil.presets import read_roles, save_roles

    roles = read_roles(repo) or []
    for item in roles:
        if item["id"] == role:
            item["persona"] = persona
    save_roles(repo, roles)


def _set_worktree(repo: Path, role: str, value: bool) -> None:
    from foil.presets import read_roles, save_roles

    roles = read_roles(repo) or []
    for item in roles:
        if item["id"] == role:
            item["worktree"] = value
    save_roles(repo, roles)


def _add_role(
    repo: Path,
    role: str,
    harness: str,
    *,
    model: str = "",
    worktree: bool = False,
    persona: str = "personas/implementer.md",
) -> None:
    from foil.presets import read_maps, read_roles, save_maps, save_roles

    roles = list(read_roles(repo) or [])
    roles.append(
        {
            "id": role,
            "description": "",
            "persona": persona,
            "worktree": worktree,
            "permission": "auto",
        }
    )
    save_roles(repo, roles)
    maps = list(read_maps(repo))
    maps.append({"role": role, "id": harness, "model": model})
    save_maps(repo, maps)


def test_spawn_rejects_a_missing_persona_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _set_persona(repo, "lead", "personas/verfier.md")
    capsys.readouterr()
    assert main(["seat", "spawn", "lead"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: persona file not found: personas/verfier.md\n"
    assert captured.out == ""


def _branch_exists(repo: Path, name: str) -> bool:
    shown = subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return shown.returncode == 0


def test_misspelled_persona_leaves_no_implementer_branch_or_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    _launch(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    _set_persona(repo, "implementer", "personas/implementr.md")
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    capsys.readouterr()
    assert main(["seat", "spawn", "implementer"]) == 1
    assert not _branch_exists(repo, "foil/implementer-1")
    assert not (foil_root(repo) / "worktrees" / "implementer-1").exists()
    assert "implementer-1" not in load_registry(repo)["seats"]
    captured = capsys.readouterr()
    assert captured.err == "foil: persona file not found: personas/implementr.md\n"
    assert captured.out == ""


def test_persona_path_outside_templates_leaves_no_implementer_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    _launch(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    outside = foil_root(repo) / "verifier.md"
    outside.write_text("should not be read\n", encoding="utf-8")
    _set_persona(repo, "implementer", "../verifier.md")
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    capsys.readouterr()
    assert main(["seat", "spawn", "implementer"]) == 1
    assert not _branch_exists(repo, "foil/implementer-1")
    assert not (foil_root(repo) / "worktrees" / "implementer-1").exists()
    assert "implementer-1" not in load_registry(repo)["seats"]
    captured = capsys.readouterr()
    assert (
        captured.err
        == "foil: persona path must stay inside the project templates: ../verifier.md\n"
    )
    assert captured.out == ""
    assert "should not be read" not in captured.err


def test_spawn_still_fails_when_the_template_names_a_broken_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    (foil_root(repo) / "harnesses" / "zz.toml").write_text('id = "zz"\n', encoding="utf-8")
    _set_entries(repo, "lead", [("zz", "")])
    assert main(["seat", "spawn", "lead"]) == 1
    assert capsys.readouterr().err == "foil: invalid preset\n"


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
    assert plan["env"]["FOIL_SEAT_ID"] == "lead"
    assert plan["env"]["FOIL_PROJECT"]
    assert "FOIL_HOME" in plan["env"]
    assert "HOME" not in plan["env"]
    assert plan["cwd"] == str(repo.resolve())
    assert "PATH" in plan["env_forward"]
    assert "HOME" in plan["env_forward"]
    assert "HOME" not in plan["env"]
    assert plan["argv"][0] == "claude"
    instruction = foil_root(repo) / "fleets" / "main" / "run" / "instructions" / "lead.md"
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
    _set_entries(repo, "lead", [("local", "")])
    assert main(["seat", "spawn", "lead"]) == 0
    plan = json.loads(Path(calls[0]["runner_argv"][3]).read_text(encoding="utf-8"))
    assert plan["argv"][0] == "grok"
    assert plan["env"]["FOIL_SEAT_ID"] == "lead"
    assert plan["env"]["FOIL_PROJECT"]
    assert "FOIL_HOME" in plan["env"]
    assert "HOME" not in plan["env"]
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
    first = foil_root(repo) / "worktrees" / "implementer-1"
    second = foil_root(repo) / "worktrees" / "implementer-2"
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
    natural = foil_root(repo) / "worktrees" / "implementer-1"
    natural.mkdir(parents=True)
    assert main(["seat", "spawn", "implementer"]) == 0
    assert capsys.readouterr().err == ""
    seat = load_registry(repo)["seats"]["implementer-1"]
    assert seat["branch"] == "foil/implementer-1-2"
    worktree = foil_root(repo) / "worktrees" / "implementer-1-2"
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
    assert (foil_root(repo) / "worktrees" / "implementer-1").is_dir()
    assert not (repo.parent / "project.foil").exists()


def test_failed_launch_removes_the_task_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)

    def boom(self: TmuxController, **kwargs: object) -> TmuxTarget:
        del self, kwargs
        raise TmuxError("hidden")

    monkeypatch.setattr("foil.lifecycle.TmuxController.launch", boom)
    assert main(["seat", "spawn", "lead", "--task", "ship it"]) == 1
    assert capsys.readouterr().err == "foil: could not launch seat\n"
    assert _mail(repo, "lead") == []
    assert "lead" not in load_registry(repo)["seats"]


def test_spawn_records_the_model_and_an_older_registry_still_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    _set_entries(repo, "lead", [("claude", "demo-model")])
    assert main(["seat", "spawn", "lead"]) == 0
    assert load_registry(repo)["seats"]["lead"]["model"] == "demo-model"
    path = foil_root(repo) / "fleets" / "main" / "run" / "registry.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["seats"]["lead"]["model"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_registry(repo)["seats"]["lead"]["model"] == ""
    assert main(["seat", "list"]) == 0
    assert "demo-model" not in capsys.readouterr().out


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
    _add_role(repo, name, "grok", worktree=True)

    assert main(["seat", "spawn", name]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: could not name seat\n"
    assert "Traceback" not in captured.err

    reloaded = load_registry(repo)
    assert set(reloaded["seats"]) == {"lead"}
    assert name not in reloaded["seats"]
    assert len(calls) == 1
    assert not (repo.parent / "project.foil").exists()
    plans = foil_root(repo) / "fleets" / "main" / "run" / "plans"
    instructions = foil_root(repo) / "fleets" / "main" / "run" / "instructions"
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
    lead = (root / "fleets" / "main" / "run" / "instructions" / "lead.md").read_text(
        encoding="utf-8"
    )
    reader = (root / "fleets" / "main" / "run" / "instructions" / "reader.md").read_text(
        encoding="utf-8"
    )
    assert ".foil/skills/" not in lead
    assert ".foil/skills/" not in reader
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
    for harness in BUILTIN_IDS:
        if harness == "claude":
            continue
        _add_role(repo, f"via-{harness}", harness)
        task = ["--task", "only the goal"] if harness == "fake" else []
        assert main(["seat", "spawn", f"via-{harness}", "--name", f"seat-{harness}", *task]) == 0
    assert capsys.readouterr().err == ""

    seats = {"lead": "claude"}
    seats.update({f"seat-{name}": name for name in BUILTIN_IDS if name != "claude"})
    assert len(calls) == len(seats)
    for seat, harness in seats.items():
        plan = json.loads(
            (foil_root(repo) / "fleets" / "main" / "run" / "plans" / f"{seat}.json").read_text(
                encoding="utf-8"
            )
        )
        text = (
            foil_root(repo) / "fleets" / "main" / "run" / "instructions" / f"{seat}.md"
        ).read_text(encoding="utf-8")
        assert text in plan["argv"]
        skill = "lead.md" if seat == "lead" else "worker.md"
        packaged = Path(__file__).resolve().parents[1] / "src" / "foil" / "defaults" / "skills"
        skill_text = (packaged / skill).read_text(encoding="utf-8").strip()
        assert skill_text in text
        reread = (
            foil_root(repo) / "fleets" / "main" / "run" / "instructions" / f"{seat}.md"
        ).resolve()
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
    fake = (
        foil_root(repo) / "fleets" / "main" / "run" / "instructions" / "seat-fake.md"
    ).read_text(encoding="utf-8")
    assert "only the goal" not in fake
    fake_plan = json.loads(
        (foil_root(repo) / "fleets" / "main" / "run" / "plans" / "seat-fake.json").read_text(
            encoding="utf-8"
        )
    )
    assert fake_plan["argv"][fake_plan["argv"].index("--prompt") + 1] == fake


def _registry_file(repo: Path) -> dict:
    return json.loads(
        (foil_root(repo) / "fleets" / "main" / "run" / "registry.json").read_text(encoding="utf-8")
    )


_REGISTRY_KEYS = {"schema_version", "fleet_id", "tmux_session", "lead", "seats"}


def _two_entries(role: str) -> str:
    return (
        f'persona = "personas/{role}.md"\n'
        "worktree = false\n"
        'permission = "auto"\n'
        "\n"
        "[[harness]]\n"
        'id = "claude"\n'
        'model = "opus"\n'
        "\n"
        "[[harness]]\n"
        'id = "codex"\n'
        'model = "gpt-5.5"\n'
    )


def _killable(monkeypatch: pytest.MonkeyPatch) -> None:
    def matches_window(
        self: TmuxController,
        fleet_id: str,
        seat_id: str,
        session_name: str,
        window_id: str,
    ) -> bool:
        del self, fleet_id, seat_id, session_name, window_id
        return True

    def stop(self: TmuxController, fleet_id: str, seat_id: str, target: TmuxTarget) -> bool:
        del self, fleet_id, seat_id, target
        return True

    monkeypatch.setattr("foil.lifecycle.TmuxController.matches_window", matches_window)
    monkeypatch.setattr("foil.lifecycle.TmuxController.remove_verified", stop)


def _plan(calls: list[dict], index: int = -1) -> dict:
    return json.loads(Path(calls[index]["runner_argv"][3]).read_text(encoding="utf-8"))


def test_legacy_string_harness_spawns_that_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    calls = _launch(monkeypatch)
    _set_entries(repo, "lead", [("claude", "opus")])
    assert main(["seat", "spawn", "lead"]) == 0
    seat = load_registry(repo)["seats"]["lead"]
    assert seat["harness"] == "claude"
    assert seat["model"] == "opus"
    plan = _plan(calls)
    assert plan["argv"][0] == "claude"
    assert "opus" in plan["argv"]
    assert set(_registry_file(repo)) == _REGISTRY_KEYS


def test_one_entry_list_spawns_that_entry_every_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    _set_worktree(repo, "implementer", False)
    _set_entries(repo, "implementer", [("codex", "gpt-5.5")])
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    seats = load_registry(repo)["seats"]
    assert seats["implementer-1"]["harness"] == "codex"
    assert seats["implementer-1"]["model"] == "gpt-5.5"
    assert seats["implementer-2"]["harness"] == "codex"
    assert seats["implementer-2"]["model"] == "gpt-5.5"
    assert set(_registry_file(repo)) == _REGISTRY_KEYS


def test_spawns_alternate_and_a_failed_launch_does_not_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    calls = _launch(monkeypatch)
    _set_worktree(repo, "implementer", False)
    _set_entries(repo, "implementer", [("claude", "opus"), ("codex", "gpt-5.5")])
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    first = load_registry(repo)["seats"]["implementer-1"]
    assert first["harness"] == "claude"
    assert first["model"] == "opus"
    assert "opus" in _plan(calls)["argv"]
    assert set(_registry_file(repo)) == _REGISTRY_KEYS

    def boom(self: TmuxController, **kwargs: object) -> TmuxTarget:
        del self, kwargs
        raise TmuxError("hidden")

    monkeypatch.setattr("foil.lifecycle.TmuxController.launch", boom)
    assert main(["seat", "spawn", "implementer"]) == 1
    assert capsys.readouterr().err == "foil: could not launch seat\n"
    saved = _registry_file(repo)
    assert set(saved) == _REGISTRY_KEYS
    assert "implementer-2" not in saved["seats"]

    calls = _launch(monkeypatch)
    assert main(["seat", "spawn", "implementer"]) == 0
    second = load_registry(repo)["seats"]["implementer-2"]
    assert second["harness"] == "codex"
    assert second["model"] == "gpt-5.5"
    plan = _plan(calls)
    assert plan["argv"][0] == "codex"
    assert "gpt-5.5" in plan["argv"]

    assert main(["seat", "spawn", "implementer"]) == 0
    assert load_registry(repo)["seats"]["implementer-3"]["harness"] == "claude"
    assert set(_registry_file(repo)) == _REGISTRY_KEYS


def test_a_killed_seat_still_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    _killable(monkeypatch)
    _set_worktree(repo, "implementer", False)
    _set_entries(repo, "implementer", [("claude", "opus"), ("codex", "gpt-5.5")])
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    assert load_registry(repo)["seats"]["implementer-1"]["harness"] == "claude"
    assert main(["seat", "kill", "implementer-1"]) == 0
    assert load_registry(repo)["seats"]["implementer-1"]["state"] == "killed"
    assert main(["seat", "spawn", "implementer"]) == 0
    second = load_registry(repo)["seats"]["implementer-2"]
    assert second["harness"] == "codex"
    assert second["model"] == "gpt-5.5"
    assert load_registry(repo)["seats"]["implementer-1"]["state"] == "killed"


def test_replacing_a_killed_lead_follows_the_one_stored_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    _killable(monkeypatch)
    _set_entries(repo, "lead", [("claude", "opus"), ("codex", "gpt-5.5")])
    assert main(["seat", "spawn", "lead"]) == 0
    assert load_registry(repo)["seats"]["lead"]["harness"] == "claude"
    assert main(["seat", "kill", "lead"]) == 0
    assert main(["seat", "spawn", "lead"]) == 0
    assert list(load_registry(repo)["seats"]) == ["lead"]
    assert load_registry(repo)["seats"]["lead"]["harness"] == "codex"
    assert main(["seat", "kill", "lead"]) == 0
    assert main(["seat", "spawn", "lead"]) == 0
    assert load_registry(repo)["seats"]["lead"]["harness"] == "codex"
    assert load_registry(repo)["seats"]["lead"]["model"] == "gpt-5.5"


def test_fleet_memory_overrides_the_model_on_the_next_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    calls = _launch(monkeypatch)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["roster", "update", "implementer", "model=opus"]) == 0
    assert "fleet memory: implementer model = opus" in capsys.readouterr().out
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    root = foil_root(repo)
    assert "opus" not in (root / "mapping.toml").read_text(encoding="utf-8")
    assert 'model = "opus"' in (root / "fleets" / "main" / "overrides.toml").read_text(
        encoding="utf-8"
    )
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    lead = _plan(calls, 0)
    worker = _plan(calls, 1)
    assert "Workspace:" in lead["argv"][-1]
    assert "Posts:" in lead["argv"][-1]
    assert "implementer: claude/opus" in lead["argv"][-1]
    assert worker["argv"][worker["argv"].index("--model") + 1] == "opus"
    assert load_registry(repo)["seats"]["implementer-1"]["model"] == "opus"
    assert load_registry(repo)["seats"]["implementer-1"]["harness"] == "claude"


def test_a_fleet_list_replaces_the_mapping_list_in_that_fleet_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    _launch(monkeypatch)
    _set_entries(repo, "implementer", [("claude", "opus"), ("codex", "gpt-5.5")])
    mapping = (foil_root(repo) / "mapping.toml").read_bytes()
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["roster", "update", "implementer", "model=x"]) == 1
    assert "harness=ID/MODEL" in capsys.readouterr().err
    assert main(["roster", "update", "implementer", "harness=codex/gpt-5.5,claude/sonnet"]) == 0
    assert "implementer harness = codex/gpt-5.5,claude/sonnet" in capsys.readouterr().out
    assert main(["roster", "update", "implementer", "harness=nope"]) == 1
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    assert (foil_root(repo) / "mapping.toml").read_bytes() == mapping
    for role in ("lead", "implementer", "implementer", "implementer"):
        assert main(["seat", "spawn", role]) == 0
    monkeypatch.setenv("FOIL_FLEET", "other")
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    seats = load_registry(repo, "main")["seats"].values()
    pairs = [(seat["harness"], seat["model"]) for seat in seats if seat["template"] != "lead"]
    assert pairs == [("codex", "gpt-5.5"), ("claude", "sonnet"), ("codex", "gpt-5.5")]
    other = load_registry(repo, "other")["seats"]["implementer-1"]
    assert (other["harness"], other["model"]) == ("claude", "opus")


def test_an_in_fleet_model_edits_the_fleet_list_and_names_its_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foil.presets import fleet_overrides

    repo = _repo(tmp_path, monkeypatch)
    _set_entries(repo, "implementer", [("claude", "opus")])
    overrides = foil_root(repo) / "fleets" / "main" / "overrides.toml"
    overrides.parent.mkdir(parents=True, exist_ok=True)
    overrides.write_text('[[override]]\nrole = "implementer"\nmodel = "old"\n', encoding="utf-8")
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    # An older model-only row becomes a row that names the mapping's harness.
    assert main(["roster", "update", "implementer", "model=big"]) == 0
    assert fleet_overrides(repo) == [
        {"role": "implementer", "id": "claude", "model": "big", "note": ""}
    ]
    assert main(["roster", "update", "implementer", "harness=codex/gpt-5.5"]) == 0
    assert main(["roster", "update", "implementer", "model=gpt-6"]) == 0
    assert [(row["id"], row["model"]) for row in fleet_overrides(repo)] == [("codex", "gpt-6")]
    assert main(["roster", "update", "implementer", "harness=codex,claude"]) == 0
    before = overrides.read_bytes()
    assert main(["roster", "update", "implementer", "model=x"]) == 1
    assert overrides.read_bytes() == before
    overrides.write_text(
        before.decode() + '\n[[override]]\nrole = "ghost"\nharness = "claude"\n',
        encoding="utf-8",
    )
    before = overrides.read_bytes()
    assert main(["roster", "update", "ghost", "harness=codex"]) == 1
    assert overrides.read_bytes() == before


def test_spawn_refuses_a_model_only_row_on_a_longer_pair_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A model-only row on a pair list of more than one entry is refused and writes no seat."""

    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    _launch(monkeypatch)
    _set_entries(repo, "implementer", [("claude", "opus"), ("codex", "gpt-5.5")])
    (foil_root(repo) / "fleets" / "main").mkdir(parents=True, exist_ok=True)
    overrides = foil_root(repo) / "fleets" / "main" / "overrides.toml"
    overrides.write_text(
        '[[override]]\nrole = "implementer"\nmodel = "big"\n', encoding="utf-8"
    )
    before = overrides.read_bytes()
    assert main(["seat", "spawn", "lead"]) == 0
    capsys.readouterr()
    assert main(["seat", "spawn", "implementer"]) == 1
    captured = capsys.readouterr()
    assert captured.err.count("\n") == 1
    assert "foil roster update implementer harness=ID/MODEL,ID/MODEL" in captured.err
    assert "implementer-1" not in load_registry(repo)["seats"]
    assert overrides.read_bytes() == before

    # A model-only row on a one-entry pair list stays valid.
    _set_entries(repo, "implementer", [("claude", "opus")])
    assert main(["seat", "spawn", "implementer"]) == 0
    seat = load_registry(repo)["seats"]["implementer-1"]
    assert (seat["harness"], seat["model"]) == ("claude", "big")


def test_an_older_model_only_row_keeps_the_mapping_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    _launch(monkeypatch)
    _set_entries(repo, "implementer", [("claude", "opus"), ("codex", "gpt-5.5")])
    (foil_root(repo) / "fleets" / "main").mkdir(parents=True, exist_ok=True)
    (foil_root(repo) / "fleets" / "main" / "overrides.toml").write_text(
        '[[override]]\nrole = "implementer"\nmodel = "big"\n', encoding="utf-8"
    )
    for role in ("lead", "implementer", "implementer"):
        assert main(["seat", "spawn", role]) == 0
    seats = load_registry(repo)["seats"].values()
    pairs = [(seat["harness"], seat["model"]) for seat in seats if seat["template"] != "lead"]
    assert pairs == [("claude", "big"), ("codex", "big")]


def test_the_first_spawn_in_a_new_fleet_stores_the_fleet_id_it_launched_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    calls = _launch(monkeypatch)
    monkeypatch.setenv("FOIL_FLEET", "other")
    assert not (foil_root(repo) / "fleets" / "other" / "run" / "registry.json").exists()
    assert main(["seat", "spawn", "lead"]) == 0
    assert calls[0]["fleet_id"] == load_registry(repo, "other")["fleet_id"]
