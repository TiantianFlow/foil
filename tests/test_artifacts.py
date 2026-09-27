"""Release-artifact content tests: wheels and sdists never ship runtime residue."""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import uuid
import zipfile
from email.parser import BytesParser
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv is unavailable")

RESIDUE_BASENAMES = (
    "FOIL.md",
    "adapter-state",
    "worktrees",
)
RESIDUE_PATH_PARTS = (
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
)
INTENDED_SDIST_MEMBERS = (
    "pyproject.toml",
    "README.md",
    "README.zh-CN.md",
    "LICENSE",
    "uv.lock",
    "docs/requirements.md",
    "skills/operator.md",
    "src/foil/__init__.py",
    "src/foil/cli.py",
    "tests/test_init.py",
    "tests/harness/foil_fake.py",
)
INTENDED_WHEEL_MEMBERS = (
    "foil/__init__.py",
    "foil/cli.py",
    "foil/lifecycle.py",
    "foil/runner.py",
    "foil/tmux.py",
    "foil/defaults/harnesses/grok.toml",
    "foil/defaults/harnesses/fake.toml",
    "foil/defaults/personas/lead.md",
    "foil/defaults/skills/operator.md",
    "foil/defaults/skills/lead.md",
    "foil/defaults/skills/worker.md",
)
WHEEL_ONLY_LAYOUT = ("tests/", "docs/", "skills/", "adapters/")


def _stage_checkout(tmp_path: Path) -> tuple[Path, str]:
    """Copy the project and seed it with realistic Foil runtime residue."""

    stage = tmp_path / "checkout"
    shutil.copytree(
        ROOT,
        stage,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "dist",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".hypothesis",
            "*.pyc",
        ),
    )
    marker = f"residue-{uuid.uuid4().hex}"
    state_root = stage / "state-root-with-absolute-path"

    (stage / "FOIL.md").write_text(
        f"# Foil seat\n\nState dir: `{state_root}` marker {marker}\n",
        encoding="utf-8",
    )
    worker = stage / "worktrees" / "developer"
    worker.mkdir(parents=True)
    (worker / "NOTES.md").write_text(f"worker checkout {marker}", encoding="utf-8")
    adapter_state = stage / "adapter-state" / "developer"
    adapter_state.mkdir(parents=True)
    (adapter_state / "bootstrap.json").write_text(
        f'{{"marker": "{marker}"}}', encoding="utf-8"
    )
    runtime_state = stage / "state" / "v1" / "fleets" / "fleet-x"
    runtime_state.mkdir(parents=True)
    (runtime_state / "fleet.json").write_text(
        f'{{"marker": "{marker}"}}', encoding="utf-8"
    )
    for cache in (".venv", ".pytest_cache", ".ruff_cache"):
        directory = stage / cache
        directory.mkdir()
        (directory / "cached.txt").write_text(marker, encoding="utf-8")
    pycache = stage / "src" / "foil" / "__pycache__"
    pycache.mkdir()
    (pycache / "runtime.pyc").write_text(marker, encoding="utf-8")
    dist = stage / "dist"
    dist.mkdir()
    (dist / "stale.whl").write_text(marker, encoding="utf-8")
    return stage, marker


def _build(stage: Path, out_dir: Path) -> tuple[Path, Path]:
    result = subprocess.run(
        ["uv", "build", "--out-dir", str(out_dir), str(stage)],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    sdists = list(out_dir.glob("*.tar.gz"))
    wheels = list(out_dir.glob("*.whl"))
    assert len(sdists) == 1 and len(wheels) == 1
    return sdists[0], wheels[0]


def _sdist_members(path: Path) -> dict[str, bytes]:
    with tarfile.open(path) as archive:
        return {
            member.name.split("/", 1)[1]: archive.extractfile(member).read()
            for member in archive.getmembers()
            if member.isfile() and "/" in member.name
        }


def _wheel_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {
            name: archive.read(name)
            for name in archive.namelist()
            if not name.endswith("/")
        }


Built = tuple[dict[str, bytes], dict[str, bytes], str, Path]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Built:
    stage, marker = _stage_checkout(tmp_path_factory.mktemp("stage"))
    out_dir = tmp_path_factory.mktemp("artifacts")
    sdist, wheel = _build(stage, out_dir)
    return _sdist_members(sdist), _wheel_members(wheel), marker, stage


def _assert_no_residue(members: dict[str, bytes], marker: str, stage: Path) -> None:
    for name, content in members.items():
        for basename in RESIDUE_BASENAMES:
            assert basename not in name, f"{basename} leaked into artifact: {name}"
        for part in RESIDUE_PATH_PARTS:
            assert f"{part}/" not in name, f"{part} leaked into artifact: {name}"
        assert ".foil/" not in name, f"fleet scaffold leaked into artifact: {name}"
        assert "/dist/" not in name and not name.startswith("dist/"), name
        assert marker.encode() not in content, f"runtime residue content in {name}"
        assert str(stage).encode() not in content, f"absolute machine path in {name}"


def test_sdist_never_contains_runtime_residue(built: Built) -> None:
    sdist, _wheel, marker, stage = built
    _assert_no_residue(sdist, marker, stage)


def test_wheel_never_contains_runtime_residue(built: Built) -> None:
    _sdist, wheel, marker, stage = built
    _assert_no_residue(wheel, marker, stage)


def test_sdist_contains_only_intended_project_files(built: Built) -> None:
    sdist, _wheel, _marker, _stage = built
    for member in INTENDED_SDIST_MEMBERS:
        assert member in sdist, f"sdist is missing {member}"


def test_wheel_contains_only_the_package_and_packaged_resources(built: Built) -> None:
    _sdist, wheel, _marker, _stage = built
    for member in INTENDED_WHEEL_MEMBERS:
        assert member in wheel, f"wheel is missing {member}"
    assert any(name.endswith(".dist-info/METADATA") for name in wheel)
    for name in wheel:
        for layout in WHEEL_ONLY_LAYOUT:
            assert not name.startswith(layout), f"wheel must not contain {layout}: {name}"


def test_wheel_skills_have_name_and_description(built: Built) -> None:
    _sdist, wheel, _marker, _stage = built
    for name in ("operator.md", "lead.md", "worker.md"):
        text = wheel[f"foil/defaults/skills/{name}"].decode()
        assert text.startswith("---\n")
        header = text.split("---", 2)[1]
        assert "name:" in header
        assert "description:" in header


def test_wheel_metadata_has_public_release_identity(built: Built) -> None:
    _sdist, wheel, _marker, _stage = built
    metadata_name = next(name for name in wheel if name.endswith(".dist-info/METADATA"))
    metadata = BytesParser().parsebytes(wheel[metadata_name])

    assert metadata["Name"] == "foil-orchestrator"
    assert metadata["Version"] == "0.2.0"
    assert metadata["Requires-Python"] == ">=3.11"
    assert metadata["Author-email"] == (
        "TiantianFlow <177855728+TiantianFlow@users.noreply.github.com>"
    )
    assert metadata.get_all("Project-URL") == [
        "Homepage, https://github.com/TiantianFlow/foil",
        "Repository, https://github.com/TiantianFlow/foil",
        "Issues, https://github.com/TiantianFlow/foil/issues",
        "Documentation, https://github.com/TiantianFlow/foil#readme",
    ]
