"""This machine's tailnet name, read once when the Bridge starts."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}
# tailscale serve sets these on every request it forwards; a local browser sends none.
PROXIED = (
    "X-Forwarded-For",
    "X-Forwarded-Host",
    "Tailscale-User-Login",
    "Tailscale-Funnel-Request",
)


def _host_name(host: str) -> str:
    match = re.fullmatch(r"(\[::1\]|[A-Za-z0-9.-]+)(?::\d{1,5})?", host)
    return match.group(1).lower() if match else ""


def tailnet() -> tuple[str, str] | None:
    """This machine's tailnet name and owner login, when Tailscale runs here untagged."""

    binary = shutil.which("tailscale")
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [binary, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        status = json.loads(result.stdout)
        node = status["Self"]
        name = str(node["DNSName"]).rstrip(".").lower()
        login = str(status["User"][str(node["UserID"])]["LoginName"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None
    running = result.returncode == 0 and status.get("BackendState") == "Running"
    if not running or node.get("Tags") or not _host_name(name) or not login.isascii():
        return None
    return name, login
