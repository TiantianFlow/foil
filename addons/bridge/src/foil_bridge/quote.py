"""Send a line range from the page. The mail is ``foil quote`` as ``user``."""

from __future__ import annotations

from foil.api import quote
from foil.errors import FoilError
from foil.project import checkout_for_key

_CAP = 16 * 1024


def deliver(
    project: object,
    fleet: object,
    path: object,
    start: object,
    end: object,
    text: object,
) -> dict:
    """Quote ``start-end`` of ``path`` and mail the owner. Returns ``to``."""

    if not isinstance(text, str):
        raise FoilError("foil: quote text is empty")
    if len(text.encode()) > _CAP:
        raise FoilError("foil: quote text is too long")
    if not all(isinstance(item, str) and item for item in (project, fleet, path)):
        raise FoilError("foil: path must stay on the board")
    if isinstance(start, bool) or isinstance(end, bool):
        raise FoilError("foil: --lines is N or N-M")
    if not isinstance(start, int) or not isinstance(end, int):
        raise FoilError("foil: --lines is N or N-M")
    checkout = checkout_for_key(str(project))
    return quote(checkout, str(fleet), "user", str(path), f"{start}-{end}", text)
