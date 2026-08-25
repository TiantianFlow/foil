"""Executable T1–T6 contract for the canonical walking-skeleton document."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
WALKING_SKELETON = ROOT / "docs" / "walking-skeleton.md"

T1_T6_HEADINGS = {
    "T1 — Install from a clean checkout": (
        "uv tool install .",
        "uv tool install --reinstall .",
    ),
    "T2 — Initialize and scaffold an isolated project": (
        "foil init .",
        "git init -b foil-demo",
    ),
    "T3 — Spawn a lead and complementary seats": (
        "foil seat spawn",
        "foil status",
        "implementer",
        "reviewer-challenger",
    ),
    "T4 — Deliver a file-backed message and wake the reviewer": (
        "foil send-message",
        "foil message-status",
        "wake",
        "queued",
    ),
    "T5 — Kill tmux and verify resume precedence and registry continuity": (
        "tmux kill-session",
        "foil resume",
        "resume_native",
        "registry",
    ),
    "T6 — Stop safely and remove the verified demo root": (
        "foil seat stop",
        "rm -rf",
    ),
}


def _level_three_sections(text: str) -> dict[str, str]:
    headings = list(re.finditer(r"^### (.+)$", text, flags=re.MULTILINE))
    return {
        match.group(1): text[
            match.end() : headings[index + 1].start() if index + 1 < len(headings) else None
        ]
        for index, match in enumerate(headings)
    }


def _shell_block(heading: str) -> str:
    text = WALKING_SKELETON.read_text(encoding="utf-8")
    section = _level_three_sections(text)[heading]
    match = re.search(r"```sh\n(.*?)\n```", section, flags=re.DOTALL)
    assert match is not None
    return match.group(1)


def test_walking_skeleton_document_exists() -> None:
    assert WALKING_SKELETON.is_file()


def test_walking_skeleton_documents_complete_contract() -> None:
    text = WALKING_SKELETON.read_text(encoding="utf-8")

    assert text.startswith("# T1–T6 walking skeleton\n")
    assert all(f"T{step}" in text for step in range(1, 7))

    prerequisites = (
        "Python >=3.11",
        "uv",
        "tmux >=3.2",
        "tmux 3.7b",
        "Git",
        "grok 1.0.5",
        "opencode 1.18.21",
        "locally authenticated",
    )
    assert all(prerequisite in text for prerequisite in prerequisites)

    adapter_pairings = (
        "grok",
        "grok-4.6",
        "opencode",
        "xai/grok-4.6",
    )
    assert all(pairing in text for pairing in adapter_pairings)

    commands = (
        "uv tool install .",
        "uv tool install --reinstall .",
        "foil init .",
        "git init -b foil-demo",
        "foil seat spawn",
        "foil status",
        "foil poll-status",
        "foil send-message",
        "foil message-status",
        "tmux kill-session",
        "foil resume",
        "foil seat stop",
    )
    assert all(command in text for command in commands)
    assert "foil launch" not in text
    assert ".foil/runtime.toml" not in text

    gates = (
        "reviewer-challenger",
        "queued",
        "wake",
        "native_session_id",
        "resume_native",
        "revive_tmux",
        "start_fresh",
        "BEFORE_STATUS_JSON",
        "RESUME_JSON",
        "STOP_JSON",
    )
    assert all(gate in text for gate in gates)


def test_walking_skeleton_states_security_and_cleanup_boundaries() -> None:
    text = WALKING_SKELETON.read_text(encoding="utf-8")

    assert "MCP" in text
    assert "credential" in text
    assert "terminal buffer" in text
    assert "FOIL_STATE_DIR" in text
    assert "DEMO_ROOT" in text


def test_walking_skeleton_maps_t1_t6_to_exact_acceptance_actions() -> None:
    text = WALKING_SKELETON.read_text(encoding="utf-8")
    sections = _level_three_sections(text)

    for heading, actions in T1_T6_HEADINGS.items():
        assert heading in sections
        assert all(action in sections[heading] for action in actions)

    assert "foil seat stop" not in sections[
        "T5 — Kill tmux and verify resume precedence and registry continuity"
    ]


def test_documented_t2_setup_executes_with_a_symlinked_tmpdir(tmp_path: Path) -> None:
    setup = _shell_block("T2 — Initialize and scaffold an isolated project")

    real_tmp = tmp_path / "canonical-tmp"
    real_tmp.mkdir()
    linked_tmp = tmp_path / "linked-tmp"
    linked_tmp.symlink_to(real_tmp, target_is_directory=True)
    capture_path = tmp_path / "captured-paths.json"

    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    fake_foil = executable_dir / "foil"
    fake_foil.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

assert sys.argv[1:] == ["init", "."]
project = Path.cwd().resolve()
state_text = os.environ["FOIL_STATE_DIR"]
state = Path(state_text).resolve()
roles = project / ".foil" / "roles"
roles.mkdir(parents=True)
Path(os.environ["FOIL_DOC_CAPTURE"]).write_text(
    json.dumps({"project": str(project), "state_text": state_text}),
    encoding="utf-8",
)
print(json.dumps({
    "fleet_id": "starter-documentation-test",
    "roles_path": str(roles),
    "lead_seat_id": None,
    "git_branch": "foil-demo",
    "state_root": str(state),
}))
""",
        encoding="utf-8",
    )
    fake_foil.chmod(0o755)

    environment = os.environ.copy()
    environment["TMPDIR"] = str(linked_tmp)
    environment["FOIL_DOC_CAPTURE"] = str(capture_path)
    environment["PATH"] = f"{executable_dir}{os.pathsep}{environment['PATH']}"
    result = subprocess.run(
        ["/bin/sh", "-c", setup],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    expected_root = real_tmp.resolve() / "foil-generation-2-demo"
    assert captured == {
        "project": str(expected_root / "project"),
        "state_text": str(expected_root / "state"),
    }


@pytest.mark.parametrize("readme_name", ["README.md", "README.zh-CN.md"])
def test_marketing_readmes_do_not_own_the_walking_skeleton_contract(readme_name: str) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    assert not re.search(r"\bT[1-6]\b", text)
    assert "T1–T6" not in text
    assert "T1-T6" not in text
    for heading in T1_T6_HEADINGS:
        assert heading not in text
