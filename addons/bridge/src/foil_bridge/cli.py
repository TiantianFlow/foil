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
    if args[:1] == ["transcript"]:
        return _run(_transcript, args[1:])
    if args == ["mcp"]:
        return _run(_mcp, None)
    if args:
        print("foil-bridge: unexpected argument", file=sys.stderr)
        return 2
    return _run(_serve, None)


def _api_ok() -> bool:
    from foil.api import VERSION

    major = int(str(VERSION).split(".", 1)[0])
    if major != 1:
        print("foil-bridge: needs foil.api 1", file=sys.stderr)
        return False
    return True


def _run(start, argument: list[str] | None) -> int:
    from foil.errors import FoilError
    from foil_bridge.transcript import UsageError

    if not _api_ok():
        return 1
    try:
        return start() if argument is None else start(argument)
    except UsageError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FoilError as exc:
        text = str(exc)
        if text.startswith("foil:"):
            text = "foil-bridge:" + text[len("foil:") :]
        print(text, file=sys.stderr)
        return 1
    except Exception:
        print("foil-bridge: unexpected error", file=sys.stderr)
        return 1


def _serve() -> int:
    from foil_bridge.server import serve

    return serve()


def _mcp() -> int:
    from foil_bridge.mcp import serve_stdio

    return serve_stdio()


def _transcript(argv: list[str]) -> int:
    from foil_bridge.transcript import command

    return command(argv)
