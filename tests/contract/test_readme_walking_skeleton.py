"""Documentation contract for the generation-2 walking skeleton (CAP-001, CAP-017, CAP-027)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def _level_three_sections(text: str) -> dict[str, str]:
    headings = list(re.finditer(r"^### (.+)$", text, flags=re.MULTILINE))
    return {
        match.group(1): text[
            match.end() : headings[index + 1].start() if index + 1 < len(headings) else None
        ]
        for index, match in enumerate(headings)
    }


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


@pytest.mark.parametrize(
    ("readme_name", "headings"),
    [
        (
            "README.md",
            {
                "T1 — Install from a clean checkout": (
                    "uv tool install .",
                    "uv tool install --reinstall .",
                ),
                "T2 — Initialize and scaffold an isolated project": (
                    "capstan init .",
                    "git init -b capstan-demo",
                ),
                "T3 — Launch two real seats and validate machine-readable JSON": (
                    "capstan launch",
                    "capstan status",
                    "implementer",
                    "reviewer-challenger",
                ),
                "T4 — Deliver a file-backed message and wake the reviewer": (
                    "capstan send-message",
                    "capstan message-status",
                    "wake",
                    "queued",
                ),
                "T5 — Kill tmux and verify resume precedence and registry continuity": (
                    "tmux kill-session",
                    "capstan resume",
                    "resume_native",
                    "registry",
                ),
                "Cleanup — Stop safely and remove the verified demo root": (
                    "capstan stop",
                    "rm -rf",
                ),
            },
        ),
        (
            "README.zh-CN.md",
            {
                "T1 — 从干净 checkout 安装": (
                    "uv tool install .",
                    "uv tool install --reinstall .",
                ),
                "T2 — 初始化并生成隔离项目 scaffold": (
                    "capstan init .",
                    "git init -b capstan-demo",
                ),
                "T3 — 启动两个真实 seat 并验证机器可读 JSON": (
                    "capstan launch",
                    "capstan status",
                    "implementer",
                    "reviewer-challenger",
                ),
                "T4 — 通过文件投递消息并唤醒 reviewer": (
                    "capstan send-message",
                    "capstan message-status",
                    "wake",
                    "queued",
                ),
                "T5 — 终止 tmux 并验证 resume 优先级与 registry 连续性": (
                    "tmux kill-session",
                    "capstan resume",
                    "resume_native",
                    "registry",
                ),
                "清理 — 安全停止并删除已验证的 demo 根目录": (
                    "capstan stop",
                    "rm -rf",
                ),
            },
        ),
    ],
)
def test_readme_maps_t1_t5_and_cleanup_to_exact_acceptance_actions(
    readme_name: str,
    headings: dict[str, tuple[str, ...]],
) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    sections = _level_three_sections(text)

    for heading, actions in headings.items():
        assert heading in sections
        assert all(action in sections[heading] for action in actions)

    t5_heading = next(heading for heading in headings if heading.startswith("T5 "))
    assert "capstan stop" not in sections[t5_heading]
