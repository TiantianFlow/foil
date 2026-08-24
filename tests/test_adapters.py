"""Declarative adapter contract tests (CAP-008–CAP-009, CAP-033–CAP-034)."""

from __future__ import annotations

from pathlib import Path

import pytest

from capstan.adapters import (
    AdapterError,
    CaptureKind,
    expand_argv,
    load_adapter,
    load_builtin_adapter,
)


def test_builtin_pool_model_pairs_and_cli_contracts_are_exact() -> None:
    grok = load_builtin_adapter("grok_cli")
    opencode = load_builtin_adapter("opencode")

    assert grok.observed_version == "1.0.5"
    assert grok.models == ("grok-4.6",)
    assert grok.executable.candidates == ("grok",)
    assert grok.launch.argv == (
        "grok",
        "--model",
        "{model}",
        "--session-id",
        "{native_session_id}",
    )
    assert grok.resume.argv == (
        "grok",
        "--model",
        "{model}",
        "--resume",
        "{native_session_id}",
    )
    assert grok.session_capture.kind is CaptureKind.GENERATED_UUID

    assert opencode.observed_version == "1.18.21"
    assert opencode.models == ("xai/grok-4.6",)
    assert opencode.executable.candidates == ("opencode",)
    assert opencode.launch.argv == ("opencode", ".", "--model", "{model}")
    assert opencode.resume.argv == (
        "opencode",
        ".",
        "--model",
        "{model}",
        "--session",
        "{native_session_id}",
    )
    assert opencode.session_capture.kind is CaptureKind.COMMAND_JSON_LIST_DELTA
    assert opencode.session_capture.argv == (
        "opencode",
        "session",
        "list",
        "--format",
        "json",
    )


def test_argv_expansion_is_token_preserving_and_rejects_unknowns() -> None:
    template = ("fixture", "--model", "{model}", "--id={native_session_id}")
    expanded = expand_argv(
        template,
        {
            "model": "model name; $(touch /tmp/nope)",
            "native_session_id": "session-1",
        },
    )

    assert expanded == [
        "fixture",
        "--model",
        "model name; $(touch /tmp/nope)",
        "--id=session-1",
    ]
    with pytest.raises(AdapterError, match="unknown placeholder"):
        expand_argv(("fixture", "{provider_name}"), {})
    with pytest.raises(AdapterError, match="missing placeholder"):
        expand_argv(("fixture", "{model}"), {})


def test_adapter_loader_rejects_shell_strings_and_unsupported_schema(tmp_path: Path) -> None:
    unsupported = tmp_path / "unsupported.toml"
    unsupported.write_text('schema_version = 99\nid = "fixture"\n', encoding="utf-8")

    with pytest.raises(AdapterError, match="schema version"):
        load_adapter(unsupported)

    shell_string = tmp_path / "shell-string.toml"
    shell_string.write_text(
        """
schema_version = 1
id = "fixture"
observed_version = "1"
models = ["fixture/model"]
skill = "skills/adapters/fixture/SKILL.md"

[executable]
candidates = ["fixture"]
version_argv = "fixture --version"

[launch]
argv = "fixture --model {model}"

[resume]
supported = false

[session_capture]
kind = "none"
""",
        encoding="utf-8",
    )

    with pytest.raises(AdapterError, match="argv"):
        load_adapter(shell_string)


def test_core_runtime_has_no_known_provider_name_conditionals() -> None:
    core = Path(__file__).parents[1] / "src" / "capstan"
    runtime_sources = [
        core / "adapters.py",
        core / "runtime.py",
        core / "tmux.py",
    ]
    prohibited = ("grok_cli", "opencode", "grok-4.6", "xai/grok-4.6")

    for source in runtime_sources:
        text = source.read_text(encoding="utf-8")
        assert all(token not in text for token in prohibited)
