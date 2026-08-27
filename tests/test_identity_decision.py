"""Contracts for Foil dual-name and technical identity."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
TAGLINE = "your agents' loyal opposition"
LEGACY_LOWER = "cap" + "stan"
LEGACY_UPPER = LEGACY_LOWER.upper()


def test_adr_0010_accepts_names_and_amends_the_frozen_requirements() -> None:
    decision_path = ROOT / "docs" / "adr" / "0010-foil-identity.md"
    assert decision_path.is_file()
    decision = decision_path.read_text(encoding="utf-8")

    assert decision.startswith("# ADR-0010:")
    assert "Status: Accepted" in decision
    assert "Chinese primary name: **运筹**" in decision
    assert "English/international name: **Foil**" in decision
    assert "**Foil · 运筹**" in decision
    assert "**运筹 · Foil**" in decision
    assert f"`{TAGLINE}`" in decision
    assert "Supersedes" in decision and "ADR-0007" in decision
    assert "frozen v0.5" in decision
    assert "Acceptance impact" in decision

    requirements = (ROOT / "docs" / "spec" / "requirements.md").read_text(encoding="utf-8")
    assert "Status: **FROZEN v0.5**" in requirements
    assert "| v0.5 |" in requirements
    assert "ADR-0010" in requirements
    assert "`foil-`" in requirements
    assert TAGLINE in requirements


def test_adr_0007_is_superseded_and_omits_former_technical_identity() -> None:
    adr_0007_path = ROOT / "docs" / "adr" / "0007-naming.md"
    adr_0007 = adr_0007_path.read_text(encoding="utf-8")
    adr_0008 = (ROOT / "docs" / "adr" / "0008-pool-accounting.md").read_text(
        encoding="utf-8"
    )

    assert adr_0007_path.is_file()
    assert adr_0007.startswith("# ADR-0007:")
    assert "Superseded" in adr_0007 and "ADR-0010" in adr_0007
    assert "dual-name" in adr_0007.lower()
    assert LEGACY_LOWER not in adr_0007.lower()
    assert LEGACY_UPPER not in adr_0007
    assert adr_0008.startswith("# ADR-0008: Evidence-based usage-pool accounting")
    assert not (ROOT / "docs" / "adr" / "0008-foil-identity.md").exists()


def test_locale_readme_headings_are_locked() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    assert english.startswith("# Foil · 运筹\n")
    assert chinese.startswith("# 运筹 · Foil\n")


def test_package_binary_config_environment_tmux_and_wake_identity(tmp_path: Path) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["name"] == "foil-orchestrator"
    assert project["project"]["authors"] == [
        {
            "name": "TiantianFlow",
            "email": "177855728+TiantianFlow@users.noreply.github.com",
        }
    ]
    assert project["project"]["urls"] == {
        "Homepage": "https://github.com/TiantianFlow/foil",
        "Repository": "https://github.com/TiantianFlow/foil",
        "Issues": "https://github.com/TiantianFlow/foil/issues",
        "Documentation": "https://github.com/TiantianFlow/foil#readme",
    }
    assert project["project"]["scripts"] == {"foil": "foil.cli:main"}
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/foil"]
    assert not (ROOT / "src" / LEGACY_LOWER).exists()
    assert (ROOT / "src" / "foil").is_dir()

    from foil.delivery import TmuxWakeService
    from foil.naming import tmux_session_name
    from foil.onboarding import initialize_project, resolve_state_root

    state_root = tmp_path / "state"
    assert resolve_state_root(
        tmp_path,
        environ={"FOIL_STATE_DIR": str(state_root)},
        platform="linux",
        home=tmp_path / "home",
    ) == state_root.resolve()
    project_root = tmp_path / "project"
    project_root.mkdir()
    initialized = initialize_project(
        project_root,
        environ={"FOIL_STATE_DIR": str(state_root)},
        platform="linux",
        home=tmp_path / "home",
    )
    assert initialized.roles_path == project_root / ".foil" / "roles"
    assert initialized.lead_seat_id is None
    assert initialized.git_branch == "foil-demo"
    assert not (project_root / ".foil" / "fleet.toml").exists()
    assert not (project_root / ".foil" / "runtime.toml").exists()
    assert tmux_session_name("Example", "fleet-1").startswith("foil-")
    assert TmuxWakeService.WAKE_TEXT == (
        "Foil mail is queued. Poll your mailbox and acknowledge messages."
    )

    help_result = subprocess.run(
        [sys.executable, "-m", "foil", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0
    assert help_result.stderr == ""
    assert "Foil · 运筹" in help_result.stdout


def test_active_surfaces_reject_stale_technical_identity() -> None:
    active_roots = (
        ROOT / "src",
        ROOT / "tests",
        ROOT / "skills",
        ROOT / "schemas",
        ROOT / "docs" / "design",
        ROOT / "adapters",
    )
    active_files = [
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
    ]
    for root in active_roots:
        active_files.extend(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix in {".json", ".lock", ".md", ".py", ".toml"}
        )

    stale: list[str] = []
    for path in active_files:
        if path == Path(__file__):
            continue
        relative = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        if LEGACY_LOWER in relative.as_posix().lower():
            stale.append(f"{relative}: path")
        if LEGACY_LOWER in text.lower() or LEGACY_UPPER in text:
            stale.append(f"{relative}: content")

    assert stale == []
