"""User-facing README contract for potential operators, not the T1–T6 lab script."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
LICENSE_BADGE = re.compile(
    r"\[!\[[^\]]*\]\(https://img\.shields\.io/badge/[^)]*Apache[_ ]?2\.0[^)]*\)\]\(LICENSE\)",
    re.IGNORECASE,
)
VERSION_BADGE = re.compile(
    r"!?\[[^\]]*\]\(https://img\.shields\.io/badge/[^)]*0\.1\.0[^)]*\)",
    re.IGNORECASE,
)
CI_BADGE = (
    "https://github.com/TiantianFlow/foil/actions/workflows/ci.yml/badge.svg"
)
CI_WORKFLOW_URL = "https://github.com/TiantianFlow/foil/actions/workflows/ci.yml"
UNSUPPORTED_CLAIMS = (
    "browser bridge",
    "browser-bridge",
    "full scheduler",
    "quota intelligence",
)
LAB_MARKERS = (
    "T1–T6",
    "T1-T6",
    "DEMO_ROOT",
    "BEFORE_STATUS_JSON",
    "foil-walking-skeleton-demo",
    "tmux kill-session",
)
QUICK_START_COMMANDS = (
    'uv tool install "git+https://github.com/TiantianFlow/foil.git@v0.1.0"',
    "foil init",
    "foil seat spawn",
    "--state-dir",
    "foil send-message",
    "--fleet",
    "--seat",
    "--sender",
    "--body",
    "--wake",
)
EXISTING_REPO_MARKERS = {
    "README.md": "existing Git repository",
    "README.zh-CN.md": "已有的 Git 仓库",
}
EMPTY_DIR_LAB_MARKERS = (
    "mkdir my-project",
    "cd my-project",
)


@pytest.mark.parametrize(
    ("readme_name", "title", "locale_link"),
    [
        ("README.md", "# Foil · 运筹", "[中文](README.zh-CN.md)"),
        ("README.zh-CN.md", "# 运筹 · Foil", "[English](README.md)"),
    ],
)
def test_readme_has_locked_heading_and_prominent_locale_link(
    readme_name: str,
    title: str,
    locale_link: str,
) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    assert text.startswith(f"{title}\n")
    prefix = text.split("## ", 1)[0]
    assert locale_link in prefix


@pytest.mark.parametrize("readme_name", ["README.md", "README.zh-CN.md"])
def test_readme_has_required_license_and_version_badges(readme_name: str) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    prefix = text.split("## ", 1)[0]
    assert LICENSE_BADGE.search(prefix)
    assert VERSION_BADGE.search(prefix)


@pytest.mark.parametrize("readme_name", ["README.md", "README.zh-CN.md"])
def test_readme_ci_badge_matches_real_workflow(readme_name: str) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    prefix = text.split("## ", 1)[0]
    assert CI_BADGE in prefix
    assert CI_WORKFLOW_URL in prefix
    assert WORKFLOW.is_file()
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "uv run --no-config --frozen --isolated --extra dev pytest" in workflow
    assert "uv run --no-config --frozen --isolated --extra dev ruff check" in workflow
    assert "uv pip install" in workflow
    assert "dist/foil_orchestrator-*.whl" in workflow
    assert '"$wheel_env/bin/foil" --version' in workflow
    assert '"foil 0.1.0"' in workflow
    assert "actions/checkout@" in workflow
    assert "astral-sh/setup-uv@" in workflow


@pytest.mark.parametrize(
    ("readme_name", "local_token", "login_token"),
    [
        ("README.md", "local", "login"),
        ("README.zh-CN.md", "本地", "登录"),
    ],
)
def test_readme_explains_shipped_user_value(
    readme_name: str,
    local_token: str,
    login_token: str,
) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    lowered = text.lower()
    assert "cli" in lowered
    assert "tmux" in lowered
    assert "mcp" in lowered
    assert local_token in text or local_token in lowered
    assert login_token in text
    for claim in UNSUPPORTED_CLAIMS:
        assert claim not in text
        assert claim.lower() not in lowered


@pytest.mark.parametrize(
    ("readme_name", "independent_process", "subagent", "blind_spot"),
    [
        ("README.md", "independent CLI processes", "subagents", "correlated blind spots"),
        ("README.zh-CN.md", "独立的 CLI 进程", "subagent", "相关性盲区"),
    ],
)
def test_readme_explains_why_loyal_opposition_is_useful(
    readme_name: str,
    independent_process: str,
    subagent: str,
    blind_spot: str,
) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    assert independent_process in text
    assert subagent in text.lower()
    assert blind_spot in text
    assert "guarantee correctness" in text or "不保证正确" in text


@pytest.mark.parametrize("readme_name", ["README.md", "README.zh-CN.md"])
def test_readme_profile_example_is_shipped_and_valid(readme_name: str) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    profile_path = ROOT / "profiles" / "pi-interactive.toml"
    assert "./profiles/pi-interactive.toml" in text
    assert profile_path.is_file()

    from foil.profiles import load_profile

    profile = load_profile(profile_path)
    assert profile.profile_id == "pi-interactive"
    assert profile.cli == "pi"


@pytest.mark.parametrize("readme_name", ["README.md", "README.zh-CN.md"])
def test_readme_has_user_facing_mermaid_diagram(readme_name: str) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    match = re.search(r"```mermaid\n(.*?)\n```", text, flags=re.DOTALL)
    assert match is not None
    diagram = match.group(1).lower()
    assert "foil" in diagram
    assert "tmux" in diagram
    assert "-->" in diagram
    assert any(token in diagram for token in ("operator", "controller", "操作", "控制"))
    assert any(token in diagram for token in ("file", "文件"))


@pytest.mark.parametrize(
    ("readme_name", "heading"),
    [
        ("README.md", "## Quick Start"),
        ("README.zh-CN.md", "## 快速开始"),
    ],
)
def test_readme_has_short_human_quick_start(readme_name: str, heading: str) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    assert heading in text
    section = text.split(heading, 1)[1]
    next_heading = re.search(r"\n## ", section)
    if next_heading:
        section = section[: next_heading.start()]
    assert all(command in section for command in QUICK_START_COMMANDS)
    assert "locally authenticated" in section or "完成本地认证" in section
    assert section.count("```") <= 8
    assert "python3 -c" not in section


@pytest.mark.parametrize("readme_name", ["README.md", "README.zh-CN.md"])
def test_readme_quick_start_targets_an_existing_git_repo(readme_name: str) -> None:
    """The primary path onboards an existing Git repo, not a lab directory."""
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    heading = "## Quick Start" if readme_name == "README.md" else "## 快速开始"
    section = text.split(heading, 1)[1]
    next_heading = re.search(r"\n## ", section)
    if next_heading:
        section = section[: next_heading.start()]
    assert EXISTING_REPO_MARKERS[readme_name] in section
    for marker in EMPTY_DIR_LAB_MARKERS:
        assert marker not in section


@pytest.mark.parametrize("readme_name", ["README.md", "README.zh-CN.md"])
def test_readme_is_not_the_t1_t6_lab_script(readme_name: str) -> None:
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    assert not re.search(r"\bT[1-6]\b", text)
    for marker in LAB_MARKERS:
        assert marker not in text
    assert "docs/walking-skeleton.md" in text
