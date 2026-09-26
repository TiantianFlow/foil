"""Board mail files. Slice A only rejects unknown recipients."""

from __future__ import annotations

from pathlib import Path

from foil.errors import FoilError, assert_no_secret


def send_mail(root: Path, to: str, text: str) -> None:
    del root
    assert_no_secret(text)
    raise FoilError(f"foil: unknown seat '{to}'")
