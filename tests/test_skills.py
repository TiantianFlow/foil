"""S4: skill files use only the section 6 command surface."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from foil import __version__

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"

FLAGS = {
    ("init",): set(),
    ("--version",): set(),
    ("seat", "spawn"): {"--name", "--task"},
    ("seat", "kill"): {"--all"},
    ("seat", "resume"): set(),
    ("seat", "renew"): set(),
    ("seat", "list"): {"--json", "--archived", "--all-fleets"},
    ("seat", "archive"): set(),
    ("seat", "peek"): {"--lines"},
    ("seat", "join"): {"--harness", "--session", "--take"},
    ("seat", "leave"): set(),
    ("send",): set(),
    ("post",): {"--re", "--decline", "--members", "--close"},
    ("mail", "read"): {"--json"},
    ("mail", "list"): {"--json"},
    ("mail", "wait"): {"--timeout", "--wake", "--all-fleets"},
    ("board", "read"): {"--json"},
    ("board", "list"): {"--json"},
    ("memory", "add"): {"--replaces"},
    ("memory", "accept"): set(),
    ("memory", "reject"): set(),
    ("memory", "list"): {"--all", "--json"},
    ("roster", "list"): {"--json"},
    ("roster", "show"): {"--json"},
    # Section 6 lists `--project` on roster add, update, and remove.
    ("roster", "add"): {"--from", "--project"},
    ("roster", "update"): {"--project"},
    ("roster", "remove"): {"--project"},
    ("view",): {"--port"},
}

ROLE_COMMANDS = {
    "joined.md": {
        ("--version",),
        ("init",),
        ("seat", "join"),
        ("seat", "leave"),
        ("seat", "spawn"),
        ("seat", "list"),
        ("seat", "kill"),
        ("seat", "archive"),
        ("seat", "renew"),
        ("send",),
        ("mail", "wait"),
        ("roster", "list"),
        ("roster", "add"),
        ("roster", "update"),
        ("roster", "remove"),
        ("view",),
    },
    "lead.md": {
        # The roster-before-init sentence names `foil init`.
        ("init",),
        ("seat", "spawn"),
        ("seat", "kill"),
        ("seat", "archive"),
        ("seat", "resume"),
        ("seat", "renew"),
        ("seat", "list"),
        ("seat", "peek"),
        ("send",),
        ("post",),
        ("mail", "read"),
        ("mail", "list"),
        ("board", "read"),
        ("board", "list"),
        ("memory", "add"),
        ("memory", "list"),
        ("memory", "accept"),
        ("memory", "reject"),
        ("roster", "list"),
        ("roster", "show"),
        ("roster", "update"),
    },
    "worker.md": {
        ("send",),
        ("post",),
        ("mail", "read"),
        ("mail", "list"),
        ("board", "read"),
        ("board", "list"),
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

# A backtick name is a command when it is `foil <words>`. `foil:` is an error string.
_INVOCATION = re.compile(r"`(foil [^`]*)`")
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
    if tokens[1:2] in (["memory"], ["mail"], ["board"], ["roster"]):
        return (tokens[1], tokens[2])
    return (tokens[1],)


def _flags(tokens: list[str], command: tuple[str, ...]) -> list[str]:
    start = 1 + len(command)
    return [token for token in tokens[start:] if token.startswith("-") and token != "-"]


_LOOKUP = ("packaged", "global", "project", "fleet")

_LAYER_THEN = re.compile(
    r"\b(packaged|global|project|fleet)\b"
    r"(?:\s*,?\s+then\s+\b(packaged|global|project|fleet)\b)+",
    re.IGNORECASE,
)

# Phrases that state a layering other than packaged, then global, then project, then fleet.
_OTHER_LAYERING = (
    "three lists in the project folder",
    "The roster is `roles.toml` and `mapping.toml` in the project folder.",
    "Personas live in `templates/personas/`",
    "Personas live in the project folder's `templates/personas/`",
    "`init` copies every packaged persona",
    "nowhere to add a preset",
    "overridden by the project folder's `harnesses/`",
    "replaces the operator skill",
    "`Updated skills:",
    "`Skills: current`",
    "`<project folder>/harnesses/",
    "`templates/personas/",
    "It writes `harnesses.toml`, `roles.toml`, and `mapping.toml` when they are missing.",
)


def _layer_orders(text: str) -> list[tuple[str, ...]]:
    found: list[tuple[str, ...]] = []
    for match in _LAYER_THEN.finditer(text):
        names = tuple(
            part.lower()
            for part in re.findall(
                r"packaged|global|project|fleet", match.group(0), re.IGNORECASE
            )
        )
        found.append(names)
    return found


def _follows_lookup(order: tuple[str, ...]) -> bool:
    index = 0
    for name in order:
        while index < len(_LOOKUP) and _LOOKUP[index] != name:
            index += 1
        if index == len(_LOOKUP):
            return False
        index += 1
    return True


def _layering_problems(label: str, text: str) -> list[str]:
    problems: list[str] = []
    orders = _layer_orders(text)
    if _LOOKUP not in orders:
        problems.append(
            f"{label}: does not describe the lookup "
            "packaged, then global, then project, then fleet"
        )
    for order in orders:
        if not _follows_lookup(order):
            shown = ", then ".join(order)
            problems.append(f"{label}: describes a different layering ({shown})")
    for phrase in _OTHER_LAYERING:
        if phrase in text:
            problems.append(f"{label}: describes a different layering ({phrase})")
    return problems


def _foil_invocations(text: str) -> list[str]:
    found = _invocations(text)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("foil "):
            found.append(stripped)
    return list(dict.fromkeys(found))


def _command_problems(label: str, text: str) -> list[str]:
    problems: list[str] = []
    for invocation in _foil_invocations(text):
        cleaned = invocation.replace("[", " ").replace("]", " ")
        try:
            tokens = shlex.split(cleaned)
        except ValueError as exc:
            problems.append(f"{label}: cannot parse {invocation!r} ({exc})")
            continue
        if len(tokens) < 2 or tokens[0] != "foil":
            problems.append(f"{label}: unknown command {invocation}")
            continue
        try:
            command = _command(tokens)
        except IndexError:
            problems.append(f"{label}: unknown command {invocation}")
            continue
        if command not in FLAGS:
            problems.append(f"{label}: unknown command {invocation}")
            continue
        unknown = set(_flags(tokens, command)) - FLAGS[command]
        if unknown:
            problems.append(f"{label}: flags {sorted(unknown)} in {invocation}")
    return problems


def _settings_texts(root: Path) -> list[tuple[str, str]]:
    from foil.lifecycle import _instruction

    packaged = ROOT / "src" / "foil" / "defaults" / "skills"
    texts: list[tuple[str, str]] = []
    for name in ("joined.md", "lead.md", "worker.md"):
        texts.append(
            (
                f"src/foil/defaults/skills/{name}",
                (packaged / name).read_text(encoding="utf-8"),
            )
        )
        texts.append((f"skills/{name}", (SKILLS / name).read_text(encoding="utf-8")))
    instruction = _instruction(
        root,
        "implementer-1",
        {"name": "implementer", "worktree": True, "persona": "none"},
        restarted=False,
    )
    texts.append(("worker instruction", instruction))
    return texts


def _joined_instruction(repo: Path) -> str:
    from foil.lifecycle import _instruction

    return _instruction(
        repo,
        "lead",
        {"name": "lead", "worktree": False, "persona": "none"},
        restarted=False,
        joined=True,
    )


def settings_surface_problems(root: Path) -> list[str]:
    problems: list[str] = []
    for label, text in _settings_texts(root):
        problems.extend(_command_problems(label, text))
        problems.extend(_layering_problems(label, text))
    return problems


def test_only_the_three_skills_remain() -> None:
    assert SKILLS.is_dir()
    assert sorted(path.name for path in SKILLS.glob("*.md")) == [
        "joined.md",
        "lead.md",
        "worker.md",
    ]


def test_skill_links_point_at_the_packaged_files() -> None:
    packaged = ROOT / "src" / "foil" / "defaults" / "skills"
    for name in ("joined.md", "lead.md", "worker.md"):
        link = SKILLS / name
        assert link.is_symlink()
        assert link.resolve() == (packaged / name).resolve()


def test_joined_skill_spawns_a_lead_only_from_the_handoff() -> None:
    text = (SKILLS / "joined.md").read_text(encoding="utf-8")
    spawns = []
    for invocation in _invocations(text):
        tokens = shlex.split(invocation)
        if tokens[:3] == ["foil", "seat", "spawn"] and "lead" in tokens:
            spawns.append(invocation)
    assert spawns == [
        "foil seat spawn lead",
        'foil seat spawn lead --task "Read board/handoffs/lead.md, then continue the goal."',
    ]
    assert 'FOIL_FLEET=docs foil seat spawn lead --task "board/tasks/goal.md"' in text
    assert "foil seat join lead" in text
    assert "`foil send user`" in text.split("## What differs", 1)[1].split("\n## ", 1)[0]
    assert "three quiet timeouts in a row" in text


def test_onboarding_sentence_names_the_install_source() -> None:
    sentence = (
        "Install Foil from https://github.com/TiantianFlow/foil, "
        "onboard this repository with it, and start a fleet. My goal: <goal>."
    )
    assert "github.com/TiantianFlow/foil" in sentence
    for name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert sentence in text


def test_permission_is_per_template_in_the_onboarding_docs() -> None:
    for relative in ("README.md", "README.zh-CN.md", "skills/joined.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "roles.toml" in text, relative
    for relative in ("README.md", "skills/joined.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        for name in ("lead", "implementer", "reviewer"):
            assert name in text, relative
    for relative in ("README.md", "skills/joined.md"):
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


def test_skills_match_section_6_and_the_settings_lookup(tmp_path: Path) -> None:
    """Fail when skill text leaves section 6 or states another settings lookup."""

    problems = settings_surface_problems(tmp_path)
    assert problems == [], (
        "skill text names a command or flag section 6 does not list, "
        "or describes a different layering:\n" + "\n".join(problems)
    )


def test_lead_skill_keeps_a_checklist() -> None:
    text = (SKILLS / "lead.md").read_text(encoding="utf-8")
    assert "## Checklist" in text
    assert "- [ ]" in text
    assert "- [x]" in text
    assert "every item is ticked" in text


def test_onboarding_says_who_answers_a_dialog() -> None:
    """Issue 24: the pages a new user follows agree on the dialog rule."""

    for relative in ("README.md", "skills/joined.md", "docs/demo.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "folder-trust" not in text, relative
        assert "approval dialog" not in text, relative
        assert "opt-in" in text, relative
        assert "reports any prompt it sees rather than answering it" not in text
    skill = (SKILLS / "joined.md").read_text(encoding="utf-8")
    assert "Write board/status.md with state: done" not in skill
    assert "no installed eligible harness" in skill
    assert "foil: no harness installed" in skill
    readme_order = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme_order.index("onboard this repository") < readme_order.index("foil roster list")
    by_hand = readme_order.split("### By hand", 1)[1].split("### If nothing happens", 1)[0]
    assert by_hand.index("note its `updated`") < by_hand.index("foil seat spawn")
    zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert zh.index("onboard this repository") < zh.index("foil roster list")
    zh_hand = zh.split("### 手动操作", 1)[1].split("### 如果什么都没发生", 1)[0]
    assert zh_hand.index("先记下它的 `updated`") < zh_hand.index("foil seat spawn")
    assert "Read the whole report before you staff" in skill
    assert "1. Which harnesses are installed" not in skill
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    started = readme.split("## Get started", 1)[1].split("### By hand", 1)[0]
    assert "Limits" in started
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "而不是替你回答" not in chinese
    zh_started = chinese.split("## 开始使用", 1)[1].split("### 手动操作", 1)[0]
    assert "限制" in zh_started
    assert "文件夹信任" not in zh_started
    assert "leaves those for you" in started
    assert "留给你" in zh_started
    assert "审批对话框" not in zh_started
    requirements = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    onboarding = requirements.split("### Onboarding", 1)[1].split("## 4.", 1)[0]
    assert "Onboarding adds no command or flag" in onboarding
    assert "an approval prompt, or an error" not in onboarding


def test_skills_state_the_channel_choice() -> None:
    joined = (SKILLS / "joined.md").read_text(encoding="utf-8")
    lead = (SKILLS / "lead.md").read_text(encoding="utf-8")
    worker = (SKILLS / "worker.md").read_text(encoding="utf-8")
    requirements = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    section_8 = requirements.split("## 8. Skills", 1)[1].split("## 9.", 1)[0]
    rows = {
        line.split("|", 2)[1].strip(): line
        for line in section_8.splitlines()
        if line.startswith("| `")
    }
    step_5 = requirements.split("5. Seats send short messages", 1)[1].split("\n6. ", 1)[0]
    shared = (
        "Send is one seat. Post is a room's members.",
        "A worker replies in a room and does not open one.",
        "A decline writes a file and wakes nobody.",
        "Status still comes from `status.md`.",
        "`foil send` one seat when the point is for that seat alone: a question or a correction.",
        "`foil post` when more than one other seat needs the same point.",
        "`foil post` when other seats need the point, not the lead alone.",
        "The task file is the brief, and the mail is the pointer to it.",
        "The result file is the report. The mail is the pointer.",
        "A note wakes nobody.",
        "A note under `board/notes/` is scratch that wakes nobody.",
        "Do not put the result or a decision there.",
        "Do not continue a thread by mailing another seat.",
    )
    for sentence in shared:
        assert sentence in step_5
    joined_rules = (
        "asks the human in its own chat, not with `foil send user`",
        "commits or sets aside its own work in progress before it spawns",
        "does not implement the fleet's goal or grade its own coordination",
        "three quiet timeouts in a row",
    )
    lead_rules = (
        "Write the plan into `board/status.md`, including the checklist.",
        "Write each piece of work as `board/tasks/<id>.md`. That file is the brief.",
        "`foil seat spawn --task` writes a mail file whose text is the task string.",
        "It does not write the task file.",
        "The task file is the brief, and the mail is the pointer to it.",
        "The seat follows the task file, not a second copy of the brief in the mail body.",
        "`--task` is the text of the first mail.",
        "Pass the task file's path.",
        "Foil does not write the task file.",
        "The role instructions arrive in the first prompt.",
        "`foil send` one seat when the point is for that seat alone: a question or a correction.",
        "`foil post` when more than one other seat needs the same point.",
        "Do not continue a thread by mailing another seat.",
        "A note under `board/notes/` is scratch that wakes nobody.",
        "Decisions, briefs, and results go in the task, result, and status files.",
        "A path under `board/room/` is `foil board read`.",
        "Implementation belongs in a seat whose template sets `worktree` to true.",
    )
    worker_rules = (
        "Read the task file your mail points at, then the rest of your mail, before you act.",
        "Write the result at `board/results/<id>.md`.",
        "`foil send lead` that the result is ready.",
        "The result file is the report.",
        "The mail is the pointer.",
        "A question or a result for the lead alone is `foil send lead`.",
        "`foil post` when other seats need the point, not the lead alone.",
        "A worker asks the lead to open a room or to bring in a seat.",
        "Do not continue a thread by mailing another seat.",
        "A note under `board/notes/` is scratch that wakes nobody.",
        "Do not put the result or a decision there.",
        "A path under `board/room/` is `foil board read`.",
    )
    for phrase in joined_rules:
        assert phrase in rows["`joined`"], phrase
    for phrase in ("in your own chat, not with `foil send user`", "Commit or set aside",
                   "You do not implement the fleet's goal", "three quiet timeouts in a row"):
        assert phrase in joined, phrase
    for sentence in lead_rules:
        assert sentence in lead, sentence
        if sentence != "Implementation belongs in a seat whose template sets `worktree` to true.":
            assert sentence in rows["`lead`"], sentence
    assert "`--task` carries only the goal" not in lead
    assert "`--task` carries only the goal" not in rows["`lead`"]
    for sentence in worker_rules:
        assert sentence in worker, sentence
        assert sentence in rows["`worker`"], sentence
    assert "is a room file" not in lead


def test_no_skill_types_into_a_pane() -> None:
    """Turns are headless: no skill clears a dialog, and Foil has no key-sending code."""

    for name in ("joined.md", "lead.md", "worker.md"):
        text = (SKILLS / name).read_text(encoding="utf-8")
        assert "folder-trust" not in text, name
        assert "send-keys" not in text, name
        assert "nudge" not in text.lower(), name
    source = ROOT / "src" / "foil"
    for path in source.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "send-keys" not in text, path
    requirements = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    f12 = next(line for line in requirements.splitlines() if line.startswith("| F12 |"))
    assert "Nothing types into a seat's pane: Foil sends no keys" in f12


def test_joined_lead_stops_waiting_after_quiet_timeouts() -> None:
    """Issue 92: a wait that backs off ends, and says so."""

    text = (SKILLS / "joined.md").read_text(encoding="utf-8")
    section = text.split("## Hearing from the fleet", 1)[1].split("\n## ", 1)[0]
    assert "three quiet timeouts in a row" in section
    assert "foil mail wait --timeout 0" in section
    requirements = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    f41 = next(line for line in requirements.splitlines() if line.startswith("| F41 |"))
    assert "quiet waits in a row" in f41


def test_the_lead_checks_a_method_that_comes_with_the_goal() -> None:
    """A method in the goal is a proposal; the lead verifies results and chooses."""

    lead = (SKILLS / "lead.md").read_text(encoding="utf-8")
    persona = (ROOT / "src" / "foil" / "defaults" / "personas" / "lead.md").read_text(
        encoding="utf-8"
    )
    decisions = lead.split("## Decisions you ask, not make", 1)[1].split("\n## ", 1)[0]
    assert "follow it." not in decisions
    assert "accept it or send it back" in lead
    for text in (lead, persona):
        assert "A method, a cause, or a fix" in text
        assert "Do not follow it unchecked, and do not drop it quietly." in text
    assert "a seat proposes it" in persona
    assert "The human's answer to a question you asked is the decision" in persona
    assert "The human's answer to a question in `status.md` `questions` is the decision" in lead


def test_lead_section_8_uses_roster_commands() -> None:
    text = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    section_8 = text.split("## 8. Skills", 1)[1].split("## 9.", 1)[0]
    lead = next(line for line in section_8.splitlines() if line.startswith("| `lead`"))
    assert "foil roster list" in lead
    assert "foil roster add" in lead
    assert "editing template files" not in lead
    assert "write `.foil/templates/<role>.toml`" not in lead


def test_requirements_define_the_checklist() -> None:
    text = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    section_74 = text.split("### 7.4 Board files", 1)[1].split("### 7.5", 1)[0]
    assert "## Checklist" in section_74
    section_8 = text.split("## 8. Skills", 1)[1].split("## 9.", 1)[0]
    rows = {
        line.split("|", 2)[1].strip(): line
        for line in section_8.splitlines()
        if line.startswith("| `")
    }
    assert "checklist" in rows["`lead`"]


def test_demos_show_the_current_init_pointer_and_fleet_board() -> None:
    """The illustrated run matches this version's init pointer and fleet board."""

    for relative in ("docs/demo.md", "docs/demo.zh-CN.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert f"foil {__version__}" in text
        assert "foil 0.4.0" not in text
        assert "foil 0.3.1" not in text
        assert "fleets/main/board/mail/" in text
        assert "your-repo-1a2b3c/skills/operator.md" not in text


def test_scenario_1_status_checklist_matches_the_demos() -> None:
    fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "scenario-1" / "scripts" / "lead.json").read_text(
            encoding="utf-8"
        )
    )
    written = next(
        action["text"]
        for step in fixture["steps"]
        for action in step["do"]
        if action.get("write") == "board/status.md"
    )
    body = written[written.index("## Checklist") :]
    for relative in ("docs/demo.md", "docs/demo.zh-CN.md"):
        assert body in (ROOT / relative).read_text(encoding="utf-8")


_ROSTER_BEFORE_INIT = (
    "An unflagged roster command, outside a fleet, reads or writes the global layer "
    "and works in a git checkout before `foil init`. "
    "It does not print `foil: not initialized`."
)


def test_joined_and_lead_skills_state_roster_before_init() -> None:
    """Section 8: joined and lead say an unflagged roster command works before init."""
    packaged = ROOT / "src" / "foil" / "defaults" / "skills"
    for name in ("joined.md", "lead.md"):
        assert _ROSTER_BEFORE_INIT in (packaged / name).read_text(encoding="utf-8")
    assert _ROSTER_BEFORE_INIT not in (packaged / "worker.md").read_text(encoding="utf-8")


def test_lead_instruction_names_global_lists_and_existing_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F24: the lead instruction names the global lists and only project paths that exist."""
    from foil.lifecycle import _instruction, init_project
    from foil.project import foil_root, store_root

    repo = tmp_path.resolve()
    subprocess.run(
        ["git", "-C", str(repo), "init", "--quiet"],
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(repo)
    init_project(str(repo))
    project = foil_root(repo)
    (project / "mapping.toml").write_text(
        '[[map]]\nrole = "lead"\nharness = "codex"\n',
        encoding="utf-8",
    )
    (project / "harnesses").mkdir()
    text = _instruction(
        repo,
        "lead",
        {"name": "lead", "worktree": False, "persona": "none"},
        restarted=False,
    )
    root = store_root()
    for name in ("harnesses.toml", "roles.toml", "mapping.toml"):
        assert f"`{root / name}`" in text
    assert f"`{project / 'mapping.toml'}`" in text
    assert f"`{project / 'harnesses'}`" in text
    for name in ("roles.toml", "harnesses.toml", "personas"):
        assert f"`{project / name}`" not in text
    assert "defaults/harnesses.toml" not in text


def _lead_row() -> str:
    text = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    section_8 = text.split("## 8. Skills", 1)[1].split("## 9.", 1)[0]
    return next(line for line in section_8.splitlines() if line.startswith("| `lead`"))


_WITHDRAWN = (
    "0.5.3",
    "0.5.2",
    "0.5.1",
    "0.5.0",
    "0.4.4",
    "0.4.3",
    "0.4.2",
    "0.3.2",
    "0.3.1",
    "0.2.1",
)


def test_normative_docs_do_not_name_a_withdrawn_release() -> None:
    """A normative doc names no withdrawn release.

    History stays out: the changelog records the withdrawn headings, and a plan
    or an issue note may name the version it was written against.
    """

    normative = [
        ROOT / "docs" / "requirements.md",
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "CONTRIBUTING.md",
    ]
    normative += sorted((ROOT / "src" / "foil" / "defaults" / "skills").glob("*.md"))
    normative += sorted((ROOT / "skills").glob("*.md"))
    # docs/README.md is the index of the plan files, and its rows name the
    # version each plan was written for. That is the same history as the plans.
    normative += sorted(
        path
        for path in (ROOT / "docs").glob("*.md")
        if path.name != "README.md" and not path.name.startswith(("plan-", "issue-"))
    )
    for path in normative:
        text = path.read_text(encoding="utf-8")
        for version in _WITHDRAWN:
            assert version not in text, f"{path.relative_to(ROOT)} names {version}"


def test_breaking_leads_with_the_init_deletion() -> None:
    """The first Breaking bullet of 0.6.0 is the unread init deletion."""

    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    current = text.split("## [0.6.0]", 1)[1].split("\n## ", 1)[0]
    breaking = current.split("### Breaking", 1)[1].split("### ", 1)[0]
    bullets = [line for line in breaking.splitlines() if line.startswith("- ")]
    assert bullets, "the 0.6.0 Breaking list is empty"
    assert bullets[0].startswith(
        "- **`foil init` deletes your old project settings files without reading them.**"
    ), bullets[0]
    assert (
        "`roles.toml`, `mapping.toml`, `harnesses.toml`, `harnesses/`, `templates/`, "
        "`skills/operator.md`, `skills/lead.md`, and `skills/worker.md`"
    ) in bullets[0]
    assert (
        "Re-create a customized roster with an unflagged `foil roster` before `init`"
        in bullets[0]
    )


def test_a_withdrawn_heading_drops_the_version_print_line() -> None:
    """A withdrawn heading keeps its ship-in note and drops the version print line."""

    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for version in _WITHDRAWN:
        heading = f"## {version} - "
        section = text.split(heading, 1)[1].split("\n## ", 1)[0]
        assert "This release was withdrawn." in section
        assert f"`foil --version` prints {version}." not in section


def test_a_withdrawn_release_does_not_name_a_withdrawn_destination() -> None:
    """A withdrawn changelog line ships into a kept release, never another withdrawn one."""

    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.startswith("This release was withdrawn.")]
    assert len(lines) == len(_WITHDRAWN)
    for line in lines:
        destination = line.removeprefix(
            "This release was withdrawn. Its changes ship in "
        ).rstrip(".")
        assert destination not in _WITHDRAWN, line


def test_lead_skill_does_not_run_a_refused_roster_command() -> None:
    """F21: inside a fleet the lead sets harness and model, and runs nothing else."""

    from foil.lifecycle import _for_lead

    path = ROOT / "src" / "foil" / "defaults" / "skills" / "lead.md"
    text = _for_lead(path.read_text(encoding="utf-8"), joined=False)
    absent = (
        "foil roster add",
        "foil roster remove",
        "persona=",
        "worktree=",
        "permission=",
        "A role you add arrives as `auto`",
        "A role that commits needs `worktree = true`",
    )
    # Named once, as a command the lead does not run. Not offered as a step.
    assert text.count("--project") == 1
    assert "and `--project` are for a caller outside the fleet" in text
    for phrase in absent:
        assert phrase not in text, phrase
    assert "foil roster update ROLE harness=ID/MODEL,ID/MODEL" in text
    assert "foil roster update ROLE model=VALUE" in text
    assert "harness` and `model` only" in text or "set `harness` and `model` only" in text


def test_lead_row_agrees_with_in_fleet_roster_limits() -> None:
    """Section 8's lead row matches F21: add, remove, and role fields stay outside."""

    lead = _lead_row()
    for phrase in (
        "foil roster remove",
        "To add a role whose persona file exists, use `foil roster add`",
        "A role that commits needs `worktree = true`",
        "A role the lead adds arrives as `auto`",
        "`foil roster add ROLE` creates a template",
    ):
        assert phrase not in lead, phrase
    assert "set `harness` and `model` only" in lead
    assert "a caller outside the fleet" in lead
    assert "The lead does not run `foil roster add`" in lead


def test_lead_instruction_names_no_refused_roster_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F24: the generated lead instruction lists no roster command the CLI refuses."""

    from foil.lifecycle import _instruction, init_project

    repo = tmp_path.resolve()
    subprocess.run(
        ["git", "-C", str(repo), "init", "--quiet"],
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(repo)
    init_project(str(repo))
    text = _instruction(
        repo,
        "lead",
        {"name": "lead", "worktree": False, "persona": "none"},
        restarted=False,
    )
    for phrase in (
        "foil roster add",
        "foil roster remove",
        "persona=",
        "worktree=",
        "permission=",
    ):
        assert phrase not in text, phrase
    commands = text.split("Commands you may run:", 1)[1].split("Mail:", 1)[0]
    assert "--project" not in commands
    assert "foil roster list [--json]" in text
    assert "foil roster show ROLE [--json]" in text
    assert "foil roster update ROLE FIELD=VALUE" in text


_EXAMPLE_MODEL = re.compile(
    r"""(?:\bmodel\s*=\s*["']([^"'\s,]+)["']|\bmodel=([A-Za-z0-9][^,\s`]*))"""
)
# The whole harness= list, including every ID/MODEL pair after a comma.
_EXAMPLE_HARNESS = re.compile(
    r"\bharness=([A-Za-z0-9][^,\s`]*(?:,[A-Za-z0-9][^,\s`]*)*)"
)
_TOML_HARNESS = re.compile(r"""\bharness\s*=\s*["']([A-Za-z0-9][^"'\s]*)["']""")
_PLACEHOLDER_MODEL = {"", "MODEL", "VALUE", "model", "<model>"}


def _packaged_models() -> dict[str, set[str]]:
    import tomllib

    catalog = tomllib.loads(
        (ROOT / "src" / "foil" / "defaults" / "harnesses.toml").read_text(encoding="utf-8")
    )
    return {row["id"]: set(row["models"]) for row in catalog["harness"]}


def _example_model_problems(label: str, text: str, models: dict[str, set[str]]) -> list[str]:
    problems: list[str] = []
    for match in _EXAMPLE_MODEL.finditer(text):
        quoted, bare = match.group(1), match.group(2)
        model = quoted or bare
        if model in _PLACEHOLDER_MODEL or model.startswith("<"):
            continue
        # A quoted model belongs to the harness the example just named.
        # A bare model= on a roster line names no harness, so any list may hold it.
        if quoted:
            window = text[max(0, match.start() - 500) : match.start()]
            named = _TOML_HARNESS.findall(window)
            if named:
                harness = named[-1]
                listed = models.get(harness)
                if listed is None or model not in listed:
                    problems.append(
                        f"{label}: {harness}/{model} is not in the packaged lists"
                    )
                continue
        owners = [harness for harness, listed in models.items() if model in listed]
        if not owners:
            problems.append(f"{label}: model {model} is not in the packaged lists")
    for match in _EXAMPLE_HARNESS.finditer(text):
        for pair in match.group(1).split(","):
            if "/" not in pair:
                continue
            harness, model = pair.split("/", 1)
            if model in _PLACEHOLDER_MODEL or harness == "ID":
                continue
            listed = models.get(harness)
            if listed is None or model not in listed:
                problems.append(f"{label}: {harness}/{model} is not in the packaged lists")
    return problems


def test_example_model_ids_are_in_the_packaged_lists() -> None:
    """An example model id is one the packaged harnesses.toml lists for that harness."""

    models = _packaged_models()
    problems: list[str] = []
    skills = ROOT / "src" / "foil" / "defaults" / "skills"
    for path in sorted(skills.glob("*.md")):
        problems.extend(
            _example_model_problems(path.name, path.read_text(encoding="utf-8"), models)
        )
    requirements = ROOT / "docs" / "requirements.md"
    problems.extend(
        _example_model_problems(
            "docs/requirements.md",
            requirements.read_text(encoding="utf-8"),
            models,
        )
    )
    assert problems == [], "\n".join(problems)


def test_a_harness_list_example_checks_every_pair() -> None:
    """A later pair in harness=ID/MODEL,ID/MODEL is checked, not only the first."""

    problems = _example_model_problems(
        "sample",
        "harness=codex/gpt-6.1-sol,claude/claude-opus-4-1",
        _packaged_models(),
    )
    assert any("claude/claude-opus-4-1" in item for item in problems), problems
    assert not any("gpt-6.1-sol" in item for item in problems), problems


def test_a_toml_model_is_checked_against_the_harness_it_names() -> None:
    """model = "..." is checked against the harness = "id" that precedes it."""

    problems = _example_model_problems(
        "sample",
        'harness = "claude"\nmodel = "gpt-6.1-sol"\n',
        _packaged_models(),
    )
    assert any("claude/gpt-6.1-sol" in item for item in problems), problems


def test_skills_keep_seat_context_small() -> None:
    """Issue 88: milestone handoffs, delta re-checks, waits that back off, and long output."""

    lead = (SKILLS / "lead.md").read_text(encoding="utf-8")
    worker = (SKILLS / "worker.md").read_text(encoding="utf-8")
    requirements = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    section_8 = requirements.split("## 8. Skills", 1)[1].split("## 9.", 1)[0]
    output = (
        "Write long command output to a file and read its tail. Write a long document once "
        "with your file tool, not inline in a shell command. A post or a mail points at the "
        "board file instead of repeating its body."
    )
    for text in (lead, worker, section_8):
        assert output in text
    for sentence in (
        "Hand off at milestones too, not only near a full context",
        "Do not mail a checkpoint into a seat that keeps running.",
        "A re-check after a fix checks the fix and every acceptance item that failed before",
        "A full test suite runs once per round, by the seat that owns acceptance",
        "New mail or a post starts your next turn, so end your turn instead of polling.",
        "double the wait",
        "`--members` on a reply sets the whole list",
        "A closed room refuses every later post",
    ):
        assert sentence in lead, sentence
    assert "When the lead asks for a handoff, or your context is nearly full" in worker
    assert "read it instead of running the suite again" in worker
    assert "You do not open a room." in worker


def test_joined_instruction_adds_the_joined_skill_and_outside_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F24: a joined lead's file adds the joined skill and the outside commands, no task line."""
    from foil.lifecycle import init_project

    repo = tmp_path.resolve()
    subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True, capture_output=True)
    monkeypatch.chdir(repo)
    init_project(str(repo))
    text = _joined_instruction(repo)
    assert text.index("# Lead") < text.index("# Joined lead")
    commands = text.split("Commands you may run:", 1)[1].split("Mail:", 1)[0]
    for command in ("foil seat kill --all", "foil seat leave", "foil roster remove ROLE"):
        assert command in commands, command
    assert "foil mail wait [--timeout SECONDS] [--wake]" in commands
    assert "listed in the brief" in text
    assert "Your task is" not in text
    assert "Older mail in your mailbox" not in text
    kill = "You may kill another seat by name. `foil seat kill --all` stops every other seat."
    assert kill in text
    assert _command_problems("joined instruction", text) == []


def test_each_lead_reads_only_its_own_paragraphs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F24: the joined file drops the launched-only lines and four sections; and the reverse."""
    from foil.lifecycle import _instruction, init_project

    repo = tmp_path.resolve()
    subprocess.run(["git", "-C", str(repo), "init", "--quiet"], check=True, capture_output=True)
    monkeypatch.chdir(repo)
    init_project(str(repo))
    joined = _joined_instruction(repo)
    launched = _instruction(
        repo, "lead", {"name": "lead", "worktree": False, "persona": "none"}, restarted=False
    )
    for text in (joined, launched):
        assert "<!--" not in text
    for line in (
        "New mail or a post starts your next turn",
        "you may not run `foil seat kill --all`",
        "Record them with `foil roster update",
        "## Setup",
        "## The viewer",
        "## Teardown and upgrading",
    ):
        assert line not in joined, line
    for line in (
        "Nothing starts a turn for you",
        "The human reads your chat",
        "## Several goals",
        "## Leaving",
        "You do not type into a pane",
    ):
        assert line in joined, line
    assert "## Dialogs" not in joined
    assert joined.count("foil send user") == 1
    assert "not with `foil send user`" in joined
    assert joined.count("## Roster") == 1
    for line in (
        "New mail or a post starts your next turn",
        "foil send user",
        "You do not type into a pane",
    ):
        assert line in launched, line
    for line in ("Nothing starts a turn for you", "The human reads your chat", "# Joined lead"):
        assert line not in launched, line
    assert len(joined.encode()) < 30_000
