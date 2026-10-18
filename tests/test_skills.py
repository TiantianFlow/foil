"""S4: skill files use only the section 6 command surface."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"

FLAGS = {
    ("init",): set(),
    ("--version",): set(),
    ("seat", "spawn"): {"--name", "--task"},
    ("seat", "kill"): {"--all"},
    ("seat", "resume"): set(),
    ("seat", "list"): {"--json", "--archived"},
    ("seat", "archive"): set(),
    ("seat", "peek"): {"--lines"},
    ("send",): set(),
    ("post",): {"--re", "--decline"},
    ("mail", "read"): {"--json"},
    ("mail", "list"): {"--json"},
    ("board", "read"): {"--json"},
    ("board", "list"): {"--json"},
    ("memory", "add"): {"--replaces"},
    ("memory", "accept"): set(),
    ("memory", "reject"): set(),
    ("memory", "list"): {"--all", "--json"},
    ("roster", "list"): {"--json"},
    ("roster", "show"): {"--json"},
    ("roster", "add"): {"--from"},
    ("roster", "update"): set(),
    ("roster", "remove"): set(),
}

ROLE_COMMANDS = {
    "operator.md": {
        ("--version",),
        ("init",),
        ("seat", "spawn"),
        ("seat", "list"),
        ("seat", "peek"),
        ("seat", "kill"),
        ("seat", "archive"),
        ("send",),
        ("post",),
        ("memory", "list"),
        ("memory", "accept"),
        ("memory", "reject"),
        ("roster", "list"),
        ("roster", "show"),
        ("roster", "add"),
        ("roster", "update"),
    },
    "lead.md": {
        ("seat", "spawn"),
        ("seat", "kill"),
        ("seat", "archive"),
        ("seat", "resume"),
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
        ("roster", "add"),
        ("roster", "update"),
        ("roster", "remove"),
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
    if tokens[1:2] in (["memory"], ["mail"], ["board"], ["roster"]):
        return (tokens[1], tokens[2])
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
    for relative in ("README.md", "README.zh-CN.md", "skills/operator.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "roles.toml" in text, relative
    for relative in ("README.md", "skills/operator.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        for name in ("lead", "implementer", "reviewer"):
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


def test_lead_skill_keeps_a_checklist() -> None:
    text = (SKILLS / "lead.md").read_text(encoding="utf-8")
    assert "## Checklist" in text
    assert "- [ ]" in text
    assert "- [x]" in text
    assert "every item is ticked" in text


def test_onboarding_says_who_answers_a_dialog() -> None:
    """Issue 24: the pages a new user follows agree on the dialog rule."""

    for relative in ("README.md", "skills/operator.md", "docs/demo.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "approval dialog" in text, relative
        assert "opt-in" in text, relative
        assert (
            "not answered" in text
            or "leaves those for you" in text
            or "leaves it for you" in text
        )
        assert "reports any prompt it sees rather than answering it" not in text
    skill = (SKILLS / "operator.md").read_text(encoding="utf-8")
    assert 'foil seat spawn lead --task "Write board/status.md with state: done"' in skill
    assert "The check passes only when `updated` changes" in skill
    assert "When the check passes, send the human's goal" in skill
    assert "When the file shows `state: done`, send" not in skill
    assert "no installed eligible harness" in skill
    assert "foil: no harness installed" in skill
    assert "foil seat kill lead" in skill
    readme_order = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme_order.index("onboard this repository") < readme_order.index("foil roster list")
    by_hand = readme_order.split("### By hand", 1)[1].split("### If nothing happens", 1)[0]
    assert by_hand.index("note its `updated`") < by_hand.index("foil seat spawn")
    zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert zh.index("onboard this repository") < zh.index("foil roster list")
    zh_hand = zh.split("### 手动操作", 1)[1].split("### 如果什么都没发生", 1)[0]
    assert zh_hand.index("先记下它的 `updated`") < zh_hand.index("foil seat spawn")
    assert "Read the whole report before you staff" in skill
    assert "| no |" in skill
    assert "1. Which harnesses are installed" not in skill
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    started = readme.split("## Get started", 1)[1].split("### By hand", 1)[0]
    assert "Limits" in started
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "而不是替你回答" not in chinese
    zh_started = chinese.split("## 开始使用", 1)[1].split("### 手动操作", 1)[0]
    assert "限制" in zh_started
    assert "审批对话框" in zh_started
    for relative in ("docs/demo.md", "docs/demo.zh-CN.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "foil 0.3.1" not in text
        assert "foil 0.4.0" in text
        assert "illustration" in text or "示意" in text
    requirements = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    onboarding = requirements.split("### Onboarding", 1)[1].split("## 4.", 1)[0]
    assert "Onboarding adds no command or flag" in onboarding
    assert "an approval prompt, or an error" not in onboarding


def test_skills_state_the_channel_choice() -> None:
    operator = (SKILLS / "operator.md").read_text(encoding="utf-8")
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
        "Send is one seat. Post is every other alive seat.",
        "`foil send lead` is the goal, the human's answer, and a nudge when the lead looks stuck.",
        "Ordinary human answers stay `foil send lead`.",
        "`foil post` is for words the human wants every alive seat to see, "
        "such as a question for the whole fleet.",
        "You are outside the fleet, so that post reaches every alive seat.",
        "A decline writes a file and types nothing.",
        "List `board/room`. If threads exist, tell the human that a room exists.",
        "Read a thread when the human asks about it, or before you post.",
        "Status still comes from `status.md`.",
        "Do not guess progress from a room file you have not read.",
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
    operator_rules = (
        "`foil send lead` is the goal, the human's answer, and a nudge when the lead looks stuck.",
        "Ordinary human answers stay `foil send lead`.",
        "`foil post` is for words the human wants every alive seat to see, "
        "such as a question for the whole fleet.",
        "You are outside the fleet, so that post reaches every alive seat.",
        "A decline writes a file and types nothing.",
        "List `board/room`. If threads exist, tell the human that a room exists.",
        "Read a thread when the human asks about it, or before you post.",
        "Status still comes from `status.md`.",
        "Do not guess progress from a room file you have not read.",
        "A note wakes nobody.",
        "Do not write it up as a board note for the fleet to treat as findings.",
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
        "Do not continue a thread by mailing another seat.",
        "A note under `board/notes/` is scratch that wakes nobody.",
        "Do not put the result or a decision there.",
        "A path under `board/room/` is `foil board read`.",
    )
    for sentence in operator_rules:
        assert sentence in operator
        assert sentence in rows["`operator`"]
    for sentence in lead_rules:
        assert sentence in lead, sentence
        if sentence != "Implementation belongs in a seat whose template sets `worktree` to true.":
            assert sentence in rows["`lead`"], sentence
    assert "`--task` carries only the goal" not in lead
    assert "`--task` carries only the goal" not in rows["`lead`"]
    assert "`--task` carries only the goal" in operator
    assert "`--task` carries only the goal" in rows["`operator`"]
    for sentence in worker_rules:
        assert sentence in worker, sentence
        assert sentence in rows["`worker`"], sentence
    assert "is a room file" not in lead


def test_operator_reports_the_checklist() -> None:
    text = (SKILLS / "operator.md").read_text(encoding="utf-8")
    section = text.split("## Check in", 1)[1].split("\n## ", 1)[0]
    assert "checklist" in section
    assert "whenever the human asks" in section
    assert "guess progress from the pane" in section


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
    assert "checklist" in rows["`operator`"]
    assert "checklist" in rows["`lead`"]


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
