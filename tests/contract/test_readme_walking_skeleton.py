"""Documentation contract for the generation-2 walking skeleton (CAP-001, CAP-017, CAP-027)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("readme_name", "title", "name_explanations"),
    [
        (
            "README.md",
            "# Capstan · 运筹",
            ("English/international name", "Chinese primary name"),
        ),
        (
            "README.zh-CN.md",
            "# 运筹 · Capstan",
            ("英文/国际名称", "中文主名称"),
        ),
    ],
)
def test_readme_documents_complete_walking_skeleton_contract(
    readme_name: str,
    title: str,
    name_explanations: tuple[str, str],
) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")

    assert text.startswith(f"{title}\n")
    assert all(explanation in text for explanation in name_explanations)
    assert all(f"T{step}" in text for step in range(1, 6))

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
        "grok_cli",
        "grok-4.6",
        "opencode",
        "xai/grok-4.6",
    )
    assert all(pairing in text for pairing in adapter_pairings)

    commands = (
        "uv tool install .",
        "uv tool install --reinstall .",
        "capstan init .",
        "git init -b capstan-demo",
        ".capstan/runtime.toml",
        "capstan launch",
        "capstan status",
        "capstan poll-status",
        "capstan send-message",
        "capstan message-status",
        "tmux kill-session",
        "capstan resume",
        "capstan stop",
    )
    assert all(command in text for command in commands)

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


@pytest.mark.parametrize("readme_name", ["README.md", "README.zh-CN.md"])
def test_readme_states_security_and_cleanup_boundaries(readme_name: str) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")

    assert "MCP" in text
    assert "credential" in text
    assert "terminal buffer" in text
    assert "CAPSTAN_STATE_DIR" in text
    assert "DEMO_ROOT" in text
