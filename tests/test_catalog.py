"""Local Markdown persona catalog contracts (catalog-list / catalog-map)."""

from __future__ import annotations

from pathlib import Path

import pytest

from foil.catalog import CatalogError, list_personas, map_persona

PERSONA = """---
name: Engineering reviewer
description: Challenges plans and diffs with evidence.
---

# Engineering reviewer
"""


def _write_persona(root: Path, name: str = "reviewer.md", text: str = PERSONA) -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


def test_catalog_list_returns_persona_paths(tmp_path: Path) -> None:
    persona = _write_persona(tmp_path)

    listed = list_personas(tmp_path)

    assert listed["personas"] == [
        {
            "name": "Engineering reviewer",
            "description": "Challenges plans and diffs with evidence.",
            "path": str(persona),
        }
    ]


def test_catalog_map_returns_the_persona_path_for_role_file_spawns(
    tmp_path: Path,
) -> None:
    persona = _write_persona(tmp_path)

    mapped = map_persona(tmp_path, "Engineering reviewer")

    assert mapped["path"] == str(persona)
    assert Path(mapped["path"]).is_file()
    assert mapped["display_name"] == "Engineering reviewer"
    assert mapped["primary_specialization"] == "independent-review"
    assert mapped["usage_pool_id"] == "independent-review"
    assert mapped["cli"] is None
    assert mapped["preset"] is None


def test_catalog_map_specializes_implementation_personas(tmp_path: Path) -> None:
    _write_persona(
        tmp_path,
        text=(
            "---\nname: Builder\ndescription: Implements scoped changes.\n---\n"
            "# Builder\n"
        ),
    )

    mapped = map_persona(tmp_path, "Builder")

    assert mapped["primary_specialization"] == "implementation"
    assert mapped["usage_pool_id"] == "primary"


def test_catalog_map_fails_closed_for_an_unknown_persona(tmp_path: Path) -> None:
    _write_persona(tmp_path)

    with pytest.raises(CatalogError, match="persona not found"):
        map_persona(tmp_path, "Missing persona")


def test_catalog_requires_a_directory(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="directory"):
        list_personas(tmp_path / "missing")
