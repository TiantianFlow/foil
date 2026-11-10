"""The stand-in CLIs read each packaged preset's command line as that CLI would."""

from __future__ import annotations

from pathlib import Path

import pytest

from foil.presets import expand_argv, load_preset
from tests.harness.standin import NAMES, Flag, Refused, options, parse

SESSION = "6f1f2a0e-3f0b-4c57-9d53-0c7d7c1f5a11"
PROMPT = "Written by foil.\nYou are seat `lead`.\nRe-read `/x/run/instructions/lead.md`.\n"
# What each CLI is told about approvals, for a template that asks and one that does not.
PERMISSION = {
    "claude": {"ask": "default", "auto": "auto"},
    "codex": {"ask": "on-request", "auto": "never"},
    "gemini": {"ask": "default", "auto": "yolo"},
    "opencode": {"ask": "", "auto": "auto"},
    "grok": {"ask": "", "auto": "always-approve"},
}
RESUME = {"claude": "id", "grok": "id", "codex": "last", "opencode": "last", "gemini": "last"}


def _launch(tmp_path: Path, name: str, **given):
    preset = load_preset(tmp_path, name)
    given.setdefault("model", preset["models"][0])
    argv = expand_argv(preset, prompt=PROMPT, session_id=SESSION, **given)
    assert argv[0] == name
    return preset, parse(name, argv[1:], preset["set_env"])


@pytest.mark.parametrize("permission", ["ask", "auto"])
@pytest.mark.parametrize("name", NAMES)
def test_a_packaged_preset_starts_its_cli_with_the_prompt_and_the_model(
    name: str, permission: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    preset, launch = _launch(tmp_path, name, permission=permission)
    assert launch.prompt == PROMPT
    assert launch.resume == ""
    assert launch.facts["model"] == preset["models"][0]
    assert launch.facts["permission"] == PERMISSION[name][permission]
    assert launch.session == (SESSION if preset["session_id"] == "generated" else "")


@pytest.mark.parametrize("name", NAMES)
def test_a_packaged_preset_starts_its_cli_without_a_model(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _preset, launch = _launch(tmp_path, name, model=None, permission="auto")
    assert (launch.prompt, launch.facts["model"]) == (PROMPT, "")


@pytest.mark.parametrize("name", NAMES)
def test_a_packaged_preset_resumes_its_cli(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    preset, launch = _launch(tmp_path, name, permission="auto", resume=True)
    assert launch.resume == RESUME[name]
    assert launch.facts["permission"] == PERMISSION[name]["auto"]
    assert launch.session == (SESSION if preset["session_id"] == "generated" else "")


def test_the_packaged_presets_turn_off_the_question_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert _launch(tmp_path, "claude")[1].facts["disallowed"] == "AskUserQuestion"
    assert _launch(tmp_path, "opencode")[1].facts["question"] == "deny"


def test_a_tool_list_takes_a_prompt_that_follows_it() -> None:
    line = ["--model", "opus", "--session-id", SESSION, "--disallowedTools", "AskUserQuestion"]
    with pytest.raises(Refused, match="no prompt"):
        parse("claude", [*line, PROMPT], {})
    assert parse("claude", [*line, "--permission-mode", "auto", PROMPT], {}).prompt == PROMPT


@pytest.mark.parametrize(
    ("name", "argv", "message"),
    [
        ("claude", ["--modle", "opus", PROMPT], "unknown option '--modle'"),
        ("claude", ["--permission-mode", "yolo", PROMPT], "does not take 'yolo'"),
        ("claude", ["--session-id", "abc", PROMPT], "'abc' is not a session id"),
        ("claude", ["--resume"], "--resume needs a session id"),
        ("claude", ["--session-id", SESSION, "--resume", SESSION], "cannot be used with"),
        ("claude", ["--model", "opus"], "no prompt"),
        ("claude", [PROMPT, "again"], "unexpected argument 'again'"),
        ("codex", ["--ask-for-approval", "sometimes", PROMPT], "does not take 'sometimes'"),
        ("codex", ["--model", "--ask-for-approval", "never", PROMPT], "'--model' needs a value"),
        ("codex", ["--last", PROMPT], "unknown option '--last'"),
        ("codex", ["resume"], "resume needs --last or a session id"),
        ("codex", ["--model", "gpt"], "no prompt"),
        ("gemini", ["--approval-mode=never", PROMPT], "does not take 'never'"),
        ("gemini", ["--yolo", "--approval-mode", "yolo", PROMPT], "cannot be used with"),
        ("gemini", ["--model", "pro"], "no prompt"),
        ("opencode", [PROMPT], "is not a project directory"),
        ("opencode", [".", "--model", "sonnet", "--prompt", PROMPT], "is not provider/model"),
        ("opencode", [".", "--model", "a/b"], "no prompt"),
        ("opencode", [".", "--prompt"], "'--prompt' needs a value"),
        ("grok", ["--always-approve=yes", PROMPT], "takes no value"),
        ("grok", ["--session-id", SESSION, "--resume", SESSION], "cannot be used with"),
        ("grok", ["--resume"], "'--resume' needs a value"),
    ],
)
def test_a_stand_in_refuses_what_its_cli_would(
    name: str, argv: list[str], message: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(Refused, match=message):
        parse(name, argv, {})


def test_opencode_reads_its_setting_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    argv = [".", "--prompt", PROMPT]
    assert parse("opencode", argv, {}).facts["question"] == ""
    with pytest.raises(Refused, match="is not JSON"):
        parse("opencode", argv, {"OPENCODE_CONFIG_CONTENT": "{permission"})


def test_options_reads_attached_optional_and_repeated_values() -> None:
    flags = {"--one": Flag("one"), "--maybe": Flag("optional"), "--many": Flag("many")}
    found, words = options(
        ["--one=a", "--maybe", "--many", "b", "c", "--maybe", "d", "word", "-"], flags, {}
    )
    assert found == {"--one": ["a"], "--maybe": ["d"], "--many": ["b", "c"]}
    assert words == ["word", "-"]
