"""S4: skill files use only the section 6 command surface."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"

FLAGS = {
    ("init",): set(),
    ("seat", "spawn"): {"--name", "--task"},
    ("seat", "kill"): {"--all"},
    ("seat", "resume"): set(),
    ("seat", "list"): {"--json"},
    ("seat", "peek"): {"--lines"},
    ("send",): set(),
    ("memory", "add"): {"--replaces"},
    ("memory", "accept"): set(),
    ("memory", "reject"): set(),
    ("memory", "list"): {"--all", "--json"},
}

ROLE_COMMANDS = {
    "operator.md": {
        ("init",),
        ("seat", "spawn"),
        ("seat", "list"),
        ("seat", "peek"),
        ("seat", "kill"),
        ("send",),
        ("memory", "list"),
        ("memory", "accept"),
        ("memory", "reject"),
    },
    "lead.md": {
        ("seat", "spawn"),
        ("seat", "kill"),
        ("seat", "resume"),
        ("seat", "list"),
        ("seat", "peek"),
        ("send",),
        ("memory", "add"),
        ("memory", "list"),
        ("memory", "accept"),
        ("memory", "reject"),
    },
    "worker.md": {
        ("send",),
        ("seat", "list"),
        ("seat", "peek"),
        ("memory", "add"),
        ("memory", "list"),
    },
}

FORBIDDEN = (
    "--state-dir",
    "--fleet",
    "--actor",
    "message-status",
    "ack-message",
    "notepad",
    "foil seats",
)

_INVOCATION = re.compile(r"`(foil\b[^`]*)`")
_FENCE = re.compile(r"```text\n(.*?)```", re.DOTALL)


def _invocations(text: str) -> list[str]:
    found = _INVOCATION.findall(text)
    for block in _FENCE.findall(text):
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("foil "):
                found.append(stripped)
    return found


def _command(tokens: list[str]) -> tuple[str, ...]:
    if tokens[:2] == ["foil", "seat"]:
        return ("seat", tokens[2])
    if tokens[:2] == ["foil", "memory"]:
        return ("memory", tokens[2])
    return (tokens[1],)


def _flags(tokens: list[str], command: tuple[str, ...]) -> list[str]:
    start = 1 + len(command)
    return [token for token in tokens[start:] if token.startswith("-") and token != "-"]


def test_only_the_three_skills_remain() -> None:
    assert SKILLS.is_dir()
    assert sorted(path.name for path in SKILLS.glob("*.md")) == [
        "lead.md",
        "operator.md",
        "worker.md",
    ]


def test_skill_links_point_at_the_packaged_files() -> None:
    packaged = ROOT / "src" / "foil" / "defaults" / "skills"
    for name in ("operator.md", "lead.md", "worker.md"):
        link = SKILLS / name
        assert link.is_symlink()
        assert link.resolve() == (packaged / name).resolve()


def test_operator_spawns_the_lead_once_then_sends_the_goal() -> None:
    text = (SKILLS / "operator.md").read_text(encoding="utf-8")
    spawns = []
    sends = []
    for invocation in _invocations(text):
        tokens = shlex.split(invocation)
        if tokens[:3] == ["foil", "seat", "spawn"] and "lead" in tokens:
            spawns.append(invocation)
        if tokens[:3] == ["foil", "send", "lead"]:
            sends.append(invocation)
    assert spawns == [
        'foil seat spawn lead --task "Write board/status.md with state: done"'
    ]
    assert 'foil send lead "the human\'s goal"' in sends
    assert "Do not spawn the lead again." in text


def test_permission_is_per_template_in_the_onboarding_docs() -> None:
    for relative in ("README.md", "README.zh-CN.md", "skills/operator.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        for name in ("lead.toml", "implementer.toml", "reviewer.toml"):
            assert name in text, relative
    for relative in ("README.md", "skills/operator.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "on the lead alone does not" in text
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "不会让工人席位无人值守" in chinese


def test_skill_commands_exist_in_section_6() -> None:
    for name, allowed in ROLE_COMMANDS.items():
        text = (SKILLS / name).read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert token not in text, f"{name} mentions {token}"
        seen: set[tuple[str, ...]] = set()
        for invocation in _invocations(text):
            tokens = shlex.split(invocation)
            assert tokens and tokens[0] == "foil", invocation
            command = _command(tokens)
            assert command in FLAGS, f"{name}: unknown command {invocation}"
            assert command in allowed, f"{name}: command not for this role {invocation}"
            unknown = set(_flags(tokens, command)) - FLAGS[command]
            assert not unknown, f"{name}: flags {sorted(unknown)} in {invocation}"
            seen.add(command)
        assert seen == allowed, f"{name}: commands {seen} != {allowed}"
