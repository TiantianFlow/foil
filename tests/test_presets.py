"""Slice C: builtin presets, expansion, and init templates."""

from __future__ import annotations

import os
import shutil
import tomllib
from importlib.resources import files
from pathlib import Path

import pytest

from foil.cli import main
from foil.errors import FoilError
from foil.presets import (
    BUILTIN_IDS,
    expand_argv,
    installed_harness,
    installed_presets,
    launch_command,
    load_preset,
    load_template,
    persona_text,
)
from foil.project import foil_root
from tests.test_init import _init_git_repository

_AGENT_CLIS = {"grok", "claude", "codex", "opencode", "gemini", "fake"}


def _hide_agent_clis(monkeypatch: pytest.MonkeyPatch) -> None:
    real = shutil.which

    def which(name: str) -> str | None:
        if name in _AGENT_CLIS:
            return None
        return real(name)

    monkeypatch.setattr(shutil, "which", which)


def test_builtins_cover_the_six_harnesses(tmp_path: Path) -> None:
    assert BUILTIN_IDS == ("claude", "codex", "gemini", "opencode", "grok", "fake")
    for harness_id in BUILTIN_IDS:
        preset = load_preset(tmp_path, harness_id)
        assert preset["id"] == harness_id
        assert preset["session_id"] in {"generated", "none"}
        assert preset["command"]
    package = Path(__file__).resolve().parents[1] / "src" / "foil"
    assert not list(package.rglob("foil-fake*"))
    assert not list(package.rglob("foil_fake.py"))


def test_expansion_drops_missing_values_and_their_flags(tmp_path: Path) -> None:
    prompt = "Read /tmp/instructions.md first."
    grok = load_preset(tmp_path, "grok")
    assert expand_argv(
        grok, model="grok-4", prompt=prompt, session_id="abc", permission="ask"
    ) == ["grok", "--model", "grok-4", "--session-id", "abc", prompt]
    dropped = expand_argv(grok, prompt=prompt, session_id="abc", permission="auto")
    assert dropped == ["grok", "--session-id", "abc", "--always-approve", prompt]

    claude = load_preset(tmp_path, "claude")
    assert expand_argv(
        claude, model="sonnet", prompt=prompt, session_id="abc", permission="auto"
    ) == ["claude", "--model", "sonnet", "--session-id", "abc", "--permission-mode", "auto", prompt]

    codex = load_preset(tmp_path, "codex")
    assert codex["unverified"] == ["resume-session-id"]
    assert codex["session_id"] == "none"
    assert expand_argv(codex, model="gpt", prompt=prompt, session_id="ignored") == [
        "codex",
        "--model",
        "gpt",
        "--ask-for-approval",
        "on-request",
        prompt,
    ]
    assert expand_argv(codex, prompt=prompt, permission="auto", resume=True) == [
        "codex",
        "resume",
        "--ask-for-approval",
        "never",
        "--last",
        prompt,
    ]

    opencode = load_preset(tmp_path, "opencode")
    assert expand_argv(opencode, model="m", prompt=prompt, permission="auto") == [
        "opencode",
        ".",
        "--model",
        "m",
        "--auto",
        "--prompt",
        prompt,
    ]
    assert expand_argv(opencode, prompt=prompt, resume=True) == [
        "opencode",
        ".",
        "--continue",
        "--prompt",
        prompt,
    ]

    gemini = load_preset(tmp_path, "gemini")
    assert gemini["unverified"] == ["all-flags-docs-only"]
    assert gemini["session_id"] == "none"
    assert expand_argv(gemini, model="g", prompt=prompt, permission="auto") == [
        "gemini",
        "--model",
        "g",
        "--approval-mode=yolo",
        prompt,
    ]
    assert expand_argv(gemini, prompt=prompt, resume=True) == ["gemini", "--resume"]

    fake = load_preset(tmp_path, "fake")
    assert fake["session_id"] == "generated"
    launched = expand_argv(fake, prompt=prompt, session_id="abc")
    assert launched == ["foil-fake", "--session", "abc", "--prompt", prompt]


def test_resume_keeps_permission_flags(tmp_path: Path) -> None:
    prompt = "Read /tmp/instructions.md first."
    assert expand_argv(
        load_preset(tmp_path, "codex"), prompt=prompt, permission="ask", resume=True
    ) == ["codex", "resume", "--ask-for-approval", "on-request", "--last", prompt]
    assert expand_argv(
        load_preset(tmp_path, "opencode"), model="m", prompt=prompt, permission="auto", resume=True
    ) == ["opencode", ".", "--continue", "--auto", "--prompt", prompt]
    assert expand_argv(
        load_preset(tmp_path, "claude"),
        model="sonnet",
        prompt=prompt,
        session_id="abc",
        permission="auto",
        resume=True,
    ) == ["claude", "--resume", "abc", "--permission-mode", "auto"]
    assert expand_argv(
        load_preset(tmp_path, "grok"),
        model="grok-4",
        prompt=prompt,
        session_id="abc",
        permission="auto",
        resume=True,
    ) == ["grok", "--model", "grok-4", "--resume", "abc", "--always-approve"]
    assert expand_argv(
        load_preset(tmp_path, "gemini"), prompt=prompt, permission="auto", resume=True
    ) == ["gemini", "--resume", "--approval-mode=yolo"]


def test_generated_session_id_is_filled_when_omitted(tmp_path: Path) -> None:
    prompt = "Read /tmp/instructions.md first."
    argv = expand_argv(load_preset(tmp_path, "fake"), prompt=prompt)
    assert argv[0] == "foil-fake"
    assert argv[1] == "--session"
    assert argv[2] not in {"", "none", "{session_id}"}
    assert argv[3:] == ["--prompt", prompt]


def test_user_harness_overrides_builtin(tmp_path: Path) -> None:
    path = tmp_path / ".foil" / "harnesses" / "grok.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'id = "grok"\ncommand = ["custom-grok", "{prompt}"]\nsession_id = "none"\n',
        encoding="utf-8",
    )
    preset = load_preset(tmp_path, "grok")
    assert preset["command"] == ["custom-grok", "{prompt}"]
    assert expand_argv(preset, prompt="Read /tmp/i first.") == [
        "custom-grok",
        "Read /tmp/i first.",
    ]


def test_unknown_placeholder_and_template_key_are_rejected(tmp_path: Path) -> None:
    builtin = (tmp_path / ".foil" / "harnesses")
    builtin.mkdir(parents=True)
    (builtin / "grok.toml").write_text(
        'id = "grok"\ncommand = ["grok", "{cwd}"]\nsession_id = "none"\n',
        encoding="utf-8",
    )
    with pytest.raises(FoilError, match="invalid preset"):
        load_preset(tmp_path, "grok")

    templates = tmp_path / ".foil" / "templates"
    templates.mkdir(parents=True)
    (templates / "lead.toml").write_text(
        'harness = "grok"\ncolor = "red"\n',
        encoding="utf-8",
    )
    with pytest.raises(FoilError, match="invalid template"):
        load_template(tmp_path, "lead")


def test_init_writes_real_personas_and_resolves_a_launch_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0

    prompt = "Read /tmp/instructions.md first."
    root = foil_root(repo)
    for role, worktree in (("lead", False), ("implementer", True), ("reviewer", False)):
        template = load_template(repo, role)
        assert template["harness"] == "grok"
        assert template["worktree"] is worktree
        assert template["permission"] == "ask"
        packaged = files("foil").joinpath("defaults", "personas", f"{role}.md").read_text(
            encoding="utf-8"
        )
        assert persona_text(template) == packaged
        assert len(packaged) > 50
        argv = launch_command(repo, role, prompt=prompt, session_id="abc")
        assert argv[0] == "grok"
        assert argv[-1] == prompt
        stored = tomllib.loads((root / "templates" / f"{role}.toml").read_text(encoding="utf-8"))
        assert stored["persona"] == f"personas/{role}.md"

    lead = root / "templates" / "lead.toml"
    persona = root / "templates" / "personas" / "lead.md"
    lead.write_text('harness = "claude"\npersona = "keep me"\n', encoding="utf-8")
    persona.write_text("custom persona\n", encoding="utf-8")
    reviewer = root / "templates" / "reviewer.toml"
    reviewer.unlink()
    assert main(["init"]) == 0
    assert lead.read_text(encoding="utf-8") == 'harness = "claude"\npersona = "keep me"\n'
    assert persona.read_text(encoding="utf-8") == "custom persona\n"
    assert load_template(repo, "lead")["harness"] == "claude"
    assert persona_text(load_template(repo, "lead")) == "keep me"
    assert reviewer.is_file()
    assert load_template(repo, "reviewer")["harness"] == "grok"
    reviewed = reviewer.read_bytes()
    assert main(["init"]) == 0
    assert reviewer.read_bytes() == reviewed


def _stub_program(directory: Path, name: str) -> None:
    binary = directory / name
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)


def test_scan_sees_user_presets_once_and_excludes_fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("my-agent", "custom-grok", "foil-fake"):
        _stub_program(bindir, name)
    monkeypatch.setenv(
        "PATH",
        f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    harnesses = tmp_path / ".foil" / "harnesses"
    harnesses.mkdir(parents=True)
    (harnesses / "my-agent.toml").write_text(
        'id = "my-agent"\ncommand = ["my-agent", "{prompt}"]\nsession_id = "none"\n',
        encoding="utf-8",
    )
    (harnesses / "grok.toml").write_text(
        'id = "grok"\ncommand = ["custom-grok", "{prompt}"]\nsession_id = "none"\n',
        encoding="utf-8",
    )
    (harnesses / "fake.toml").write_text(
        'id = "fake"\ncommand = ["foil-fake", "{prompt}"]\nsession_id = "none"\n',
        encoding="utf-8",
    )

    found = installed_presets(tmp_path)
    ids = [preset["id"] for preset in found]
    assert "my-agent" in ids
    assert ids.count("grok") == 1
    assert "fake" not in ids
    grok = next(preset for preset in found if preset["id"] == "grok")
    assert grok["command"][0] == "custom-grok"


def test_scan_errors_when_nothing_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hide_agent_clis(monkeypatch)
    assert installed_presets(tmp_path) == []
    with pytest.raises(FoilError, match="no harness installed"):
        installed_harness(tmp_path)


def test_init_without_a_harness_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    _hide_agent_clis(monkeypatch)
    assert main(["init"]) == 1
    assert capsys.readouterr().err == "foil: no harness installed\n"
    assert not foil_root(repo).exists()


def test_init_rerun_with_no_harness_keeps_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0
    before = {
        path: path.read_bytes()
        for path in (foil_root(repo) / "templates").rglob("*")
        if path.is_file()
    }
    _hide_agent_clis(monkeypatch)
    assert main(["init"]) == 0
    after = {
        path: path.read_bytes()
        for path in (foil_root(repo) / "templates").rglob("*")
        if path.is_file()
    }
    assert after == before


def test_init_writes_skills_and_leaves_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0
    root = foil_root(repo) / "skills"
    for name in ("operator.md", "lead.md", "worker.md"):
        packaged = files("foil").joinpath("defaults", "skills", name).read_bytes()
        written = (root / name).read_bytes()
        assert written == packaged
        for copy in (packaged.decode(), written.decode()):
            header = copy.split("---", 2)[1]
            assert "name:" in header
            assert "description:" in header
    edited = root / "operator.md"
    edited.write_text("edited operator\n", encoding="utf-8")
    assert main(["init"]) == 0
    assert edited.read_text(encoding="utf-8") == "edited operator\n"


def test_lead_persona_does_not_take_the_operator_role() -> None:
    root = Path(__file__).resolve().parents[1]
    personas = root / "src" / "foil" / "defaults" / "personas"
    texts = [path.read_text(encoding="utf-8") for path in sorted(personas.glob("*.md"))]
    texts.append((root / "skills" / "lead.md").read_text(encoding="utf-8"))
    texts.append((root / "skills" / "worker.md").read_text(encoding="utf-8"))
    for text in texts:
        assert "operator" not in text.lower()
    lead = (personas / "lead.md").read_text(encoding="utf-8").lower()
    assert "foil init" not in lead
    assert "kill --all" not in lead
    for word in ("spawn", "status", "roster", "memory", "board", "foil"):
        assert word not in lead
    for name in ("implementer.md", "reviewer.md"):
        text = (personas / name).read_text(encoding="utf-8").lower()
        for word in ("foil send", "foil seat", "foil memory", "board/mail", "board/notes"):
            assert word not in text
