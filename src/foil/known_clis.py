"""Shipped CLI names that resolve to builtin adapter records."""

from __future__ import annotations

CLI_ADAPTERS = {
    "grok": "grok_cli",
    "grok_cli": "grok_cli",
    "opencode": "opencode",
}


def adapter_id_for_cli(cli: str | None) -> str | None:
    if not cli:
        return None
    return CLI_ADAPTERS.get(cli)
