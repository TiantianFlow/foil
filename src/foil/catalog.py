"""Local Markdown persona catalog listing (no download, no vendor)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

FRONTMATTER = re.compile(r"^---\n(?P<meta>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)


class CatalogError(ValueError):
    """A local persona catalog could not be read."""


def list_personas(path: Path) -> dict[str, Any]:
    root = Path(path)
    if not root.is_dir():
        raise CatalogError("catalog path must be a directory")
    personas: list[dict[str, str]] = []
    for file in sorted(root.rglob("*.md")):
        text = file.read_text(encoding="utf-8")
        match = FRONTMATTER.match(text)
        if match is None:
            continue
        metadata: dict[str, str] = {}
        for raw in match.group("meta").splitlines():
            if ":" not in raw:
                continue
            key, value = raw.split(":", 1)
            metadata[key.strip()] = value.strip()
        name = metadata.get("name")
        if not name:
            continue
        personas.append(
            {
                "name": name,
                "description": metadata.get("description", ""),
                "path": str(file),
            }
        )
    return {"personas": personas}


def map_persona(path: Path, persona_name: str) -> dict[str, Any]:
    """Return display fields for a local persona.

    ``cli`` and ``preset`` stay null: a Markdown persona does not assign a
    CLI. Persist staffing with ``foil seats set``.
    """
    listed = list_personas(path)
    for persona in listed["personas"]:
        if persona["name"] == persona_name:
            specialization = "independent-review"
            lowered = f"{persona['name']} {persona['description']}".lower()
            if "review" in lowered or "challeng" in lowered:
                specialization = "independent-review"
            elif "implement" in lowered:
                specialization = "implementation"
            return {
                "display_name": persona["name"],
                "primary_specialization": specialization,
                "description": persona["description"],
                "path": persona["path"],
                "preset": None,
                "cli": None,
                "usage_pool_id": (
                    "independent-review"
                    if specialization == "independent-review"
                    else "primary"
                ),
            }
    raise CatalogError(f"persona not found: {persona_name}")
