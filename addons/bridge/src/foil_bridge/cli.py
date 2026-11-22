"""The ``foil-bridge`` program. ``--version`` does not listen."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from foil_bridge import __version__


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--version"]:
        print(f"foil-bridge {__version__}")
        return 0
    if args:
        print("foil-bridge: unexpected argument", file=sys.stderr)
        return 2
    from foil.api import VERSION
    from foil.errors import FoilError

    major = int(str(VERSION).split(".", 1)[0])
    if major != 1:
        print("foil-bridge: needs foil.api 1", file=sys.stderr)
        return 1
    from foil_bridge.server import serve

    try:
        return serve()
    except FoilError as exc:
        text = str(exc)
        if text.startswith("foil:"):
            text = "foil-bridge:" + text[len("foil:") :]
        print(text, file=sys.stderr)
        return 1
    except Exception:
        print("foil-bridge: unexpected error", file=sys.stderr)
        return 1
