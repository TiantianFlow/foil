"""Distribution and documentation contracts for management skills (CAP-001, CAP-010, CAP-027)."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SKILLS_ROOT = ROOT / "skills"
PYPROJECT = ROOT / "pyproject.toml"

MANAGEMENT_SKILLS = ("controller", "manager", "poll-status")
SHIPPED_COMMANDS = (
    "init",
    "launch",
    "status",
    "stop",
    "resume",
    "poll-status",
    "send-message",
    "ack-message",
    "message-status",
)
FORBIDDEN_CONTROL_SURFACES = (
    "mcp",
    "cdp",
    "playwright",
    "credential store",
    "credential stores",
    "terminal scrap",
    "screen scrap",
    "regex match",
)
FRONTMATTER = re.compile(
    r"^---\n(?P<meta>.*?)\n---\n(?P<body>.*)\Z",
    re.DOTALL,
)


def _skill_path(name: str) -> Path:
    return SKILLS_ROOT / name / "SKILL.md"


def _parse_skill(name: str) -> tuple[dict[str, str], str]:
    text = _skill_path(name).read_text(encoding="utf-8")
    match = FRONTMATTER.match(text)
    assert match is not None, f"{name} is missing conservative YAML frontmatter"
    metadata: dict[str, str] = {}
    for raw_line in match.group("meta").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        assert separator, f"{name} frontmatter line is not key: value"
        metadata[key.strip()] = value.strip()
    return metadata, match.group("body")


def test_management_skills_exist_with_conservative_metadata() -> None:
    for name in MANAGEMENT_SKILLS:
        metadata, body = _parse_skill(name)
        assert set(metadata) == {"name", "description"}
        assert metadata["name"] == f"foil-{name}"
        assert metadata["description"]
        assert "foil --help" in body or f"foil {name}" in body or "foil poll-status" in body
        assert "foil" in body
        assert ".agents/skills/" in body
        assert "AGENTS.md" in body


def test_pyproject_packages_management_skills() -> None:
    payload = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    includes = payload["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    for name in MANAGEMENT_SKILLS:
        source = f"skills/{name}"
        assert includes[source] == f"foil/resources/skills/{name}"
        assert _skill_path(name).is_file()


@pytest.mark.parametrize("command", SHIPPED_COMMANDS)
def test_each_management_command_is_in_a_skill_and_cli_help(command: str) -> None:
    bodies = "\n".join(_parse_skill(name)[1] for name in MANAGEMENT_SKILLS)
    assert f"foil {command}" in bodies

    help_result = subprocess.run(
        [sys.executable, "-m", "foil", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert command in help_result.stdout

    command_help = subprocess.run(
        [sys.executable, "-m", "foil", command, "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert command_help.returncode == 0, command_help.stderr
    assert "usage:" in command_help.stdout.lower()


def test_skills_do_not_offer_forbidden_control_surfaces() -> None:
    for name in MANAGEMENT_SKILLS:
        body = _parse_skill(name)[1].lower()
        for token in FORBIDDEN_CONTROL_SURFACES:
            assert token not in body, f"{name} mentions {token} as a control surface"


def test_controller_covers_lifecycle_mailbox_and_resume_precedence() -> None:
    body = _parse_skill("controller")[1]
    assert "foil init" in body
    assert "foil launch" in body
    assert "foil send-message" in body
    assert "foil ack-message" in body
    assert "foil message-status" in body
    assert "queued" in body
    assert "advisory" in body.lower() or "wake" in body.lower()
    assert "revive_tmux" in body
    assert "resume_native" in body
    assert "start_fresh" in body
    assert "FOIL_STATE_DIR" in body
    assert "--json" in body
    assert "display name" in body.lower()


def test_manager_documents_current_staffing_and_later_boundaries() -> None:
    body = _parse_skill("manager")[1]
    assert "adapter_paths" in body
    assert "Python adapter" in body
    assert "not shipped" in body.lower()
    assert "catalog" in body.lower()
    assert "workspace" in body.lower()
    assert "cli" in body.lower()
    assert "catalog support is not shipped" in body.lower()
    assert "parse, download, or vendor" in body.lower()
    assert "foil doctor" not in body
    assert "--fresh" not in body
    assert "scheduler" in body.lower()


def test_poll_status_skill_distinguishes_files_from_live_status() -> None:
    body = _parse_skill("poll-status")[1]
    assert "foil poll-status" in body
    assert "foil status" in body
    assert "--state-dir" in body
    assert "--fleet" in body
    assert "pane text" in body.lower()
    assert "working" in body
    assert "exited" in body
    assert "blocked" in body
