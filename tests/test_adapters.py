"""Declarative adapter contract tests (CAP-008–CAP-009, CAP-033–CAP-034)."""

from __future__ import annotations

from pathlib import Path

import pytest

from foil.adapters import (
    AdapterError,
    CaptureKind,
    expand_argv,
    load_adapter,
    load_builtin_adapter,
    permission_argv,
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
    assert grok.startup.argv == ("Read {bootstrap_path} before beginning.",)
    assert grok.permissions.supervised == ()
    assert grok.permissions.auto == ("--always-approve",)

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
    assert opencode.startup.argv == ("Read {bootstrap_path} before beginning.",)
    assert opencode.permissions.auto == ("--auto",)


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


def test_adapter_can_declare_startup_argv(tmp_path: Path) -> None:
    path = tmp_path / "startup.toml"
    path.write_text(
        """
schema_version = 1
id = "fixture"
observed_version = "1"
models = ["fixture/model"]
skill = "skills/adapters/fixture/SKILL.md"

[executable]
candidates = ["fixture"]
version_argv = ["fixture", "--version"]

[launch]
argv = ["fixture", "--model", "{model}"]

[resume]
supported = false

[session_capture]
kind = "none"

[startup]
argv = ["Read {bootstrap_path} before beginning."]
""",
        encoding="utf-8",
    )
    record = load_adapter(path)
    assert record.startup.argv == ("Read {bootstrap_path} before beginning.",)
    expanded = expand_argv(
        record.launch.argv + record.startup.argv,
        {
            "model": "fixture/model",
            "bootstrap_path": "/tmp/state/bootstrap.json",
        },
    )
    assert expanded[-1] == "Read /tmp/state/bootstrap.json before beginning."


def test_adapter_declares_permission_profiles(tmp_path: Path) -> None:
    path = tmp_path / "permissions.toml"
    path.write_text(
        """
schema_version = 1
id = "fixture"
observed_version = "1"
models = ["fixture/model"]
skill = "skills/adapters/fixture/SKILL.md"

[executable]
candidates = ["fixture"]
version_argv = ["fixture", "--version"]

[launch]
argv = ["fixture", "--model", "{model}"]

[resume]
supported = false

[session_capture]
kind = "none"

[permissions]
auto = ["--always-approve"]
""",
        encoding="utf-8",
    )
    record = load_adapter(path)
    assert permission_argv(record, "supervised") == ()
    assert permission_argv(record, "auto") == ("--always-approve",)
    missing = tmp_path / "no-auto.toml"
    missing.write_text(
        """
schema_version = 1
id = "fixture"
observed_version = "1"
models = ["fixture/model"]
skill = "skills/adapters/fixture/SKILL.md"

[executable]
candidates = ["fixture"]
version_argv = ["fixture", "--version"]

[launch]
argv = ["fixture", "--model", "{model}"]

[resume]
supported = false

[session_capture]
kind = "none"
""",
        encoding="utf-8",
    )
    bare = load_adapter(missing)
    with pytest.raises(AdapterError, match="unsupported"):
        permission_argv(bare, "auto")
    with pytest.raises(AdapterError, match="unknown permission"):
        permission_argv(record, "bypass")


def test_repo_adapters_match_packaged_resources() -> None:
    root = Path(__file__).parents[1]
    for name in ("grok_cli.toml", "opencode.toml"):
        public = (root / "adapters" / name).read_text(encoding="utf-8")
        packaged = (root / "src" / "foil" / "resources" / "adapters" / name).read_text(
            encoding="utf-8"
        )
        assert public == packaged


def test_core_runtime_has_no_known_provider_name_conditionals() -> None:
    core = Path(__file__).parents[1] / "src" / "foil"
    runtime_sources = [
        core / "adapters.py",
        core / "runtime.py",
        core / "tmux.py",
    ]
    prohibited = ("grok_cli", "opencode", "grok-4.6", "xai/grok-4.6")

    for source in runtime_sources:
        text = source.read_text(encoding="utf-8")
        assert all(token not in text for token in prohibited)
