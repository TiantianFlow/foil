"""``foil bridge`` and ``foil doctor``. Core does not import the Bridge package.

The boot file and the desk token live here because ``foil bridge install``
has to work when the package is present on ``PATH`` and when the command
only needs to report that it is missing.
"""

from __future__ import annotations

import http.client
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from foil.errors import FoilError
from foil.project import store_root

PORT = 8791
INSTALL = (
    'uv tool install "git+https://github.com/TiantianFlow/foil.git#subdirectory=addons/bridge"'
)
MISSING = f"foil: bridge is not installed; {INSTALL}"
_ENV_KEYS = ("FOIL_HOME", "XDG_DATA_HOME")


def bridge_port() -> int:
    raw = os.environ.get("FOIL_BRIDGE_PORT", "").strip()
    if not raw:
        return PORT
    if raw.isascii() and raw.isdecimal() and 1024 <= int(raw) <= 65535:
        return int(raw)
    raise FoilError("foil: invalid port")


def bridge_root() -> Path:
    return store_root() / "bridge"


def desk_path() -> Path:
    return bridge_root() / "desk"


def bridge_binary() -> str | None:
    found = shutil.which("foil-bridge")
    if found is None:
        return None
    return str(Path(found).resolve())


def _platform() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def _config_home() -> Path | None:
    raw = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def unit_path(platform: str, *, home: Path, config: Path | None) -> Path:
    if platform == "darwin":
        return home / "Library" / "LaunchAgents" / "com.foil.bridge.plist"
    if platform == "linux":
        base = config if config is not None else home / ".config"
        return base / "systemd" / "user" / "foil-bridge.service"
    raise FoilError("foil: bridge install supports Linux and macOS")


def service_environment() -> list[tuple[str, str]]:
    """What the boot entry passes through. Nothing else from the process."""

    rows = [
        ("PATH", os.environ.get("PATH", "")),
        ("FOIL_BRIDGE_PORT", str(bridge_port())),
    ]
    for key in _ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            rows.append((key, value))
    for _key, value in rows:
        if any(ch in value for ch in '\n\r\x00"'):
            raise FoilError("foil: bridge install refused the service environment")
    return rows


def _exec(binary: str) -> str:
    if not binary or any(ch.isspace() or ch in '"\\' for ch in binary):
        raise FoilError("foil: bridge install refused the service environment")
    return binary


def systemd_unit(binary: str, env: list[tuple[str, str]]) -> str:
    lines = [
        "[Unit]",
        "Description=Foil Bridge",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        f"ExecStart={_exec(binary)}",
    ]
    lines.extend(f'Environment="{key}={value}"' for key, value in env)
    lines.extend(
        [
            "Restart=on-failure",
            "RestartSec=2",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )
    return "\n".join(lines)


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def launchd_plist(binary: str, env: list[tuple[str, str]]) -> str:
    program = _exec(binary)
    variables = "\n".join(
        f"    <key>{_xml(key)}</key>\n    <string>{_xml(value)}</string>" for key, value in env
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "  <key>Label</key>\n"
        "  <string>com.foil.bridge</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        f"    <string>{_xml(program)}</string>\n"
        "  </array>\n"
        "  <key>EnvironmentVariables</key>\n"
        "  <dict>\n"
        f"{variables}\n"
        "  </dict>\n"
        "  <key>RunAtLoad</key>\n"
        "  <true/>\n"
        "  <key>KeepAlive</key>\n"
        "  <true/>\n"
        "</dict>\n"
        "</plist>\n"
    )


def unit_text(binary: str, platform: str | None = None) -> str:
    system = platform or _platform()
    env = service_environment()
    if system == "darwin":
        return launchd_plist(binary, env)
    if system == "linux":
        return systemd_unit(binary, env)
    raise FoilError("foil: bridge install supports Linux and macOS")


def current_unit_path() -> Path:
    return unit_path(_platform(), home=Path.home(), config=_config_home())


def _private_dir(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise FoilError("foil: refusing symlink")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise FoilError("foil: refusing symlink")
    os.chmod(path, 0o700)


def write_private(path: Path, text: str) -> None:
    """One owner-only file. A symlink is refused rather than followed."""

    _private_dir(path.parent)
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    temporary = path.with_name(path.name + ".tmp")
    if temporary.is_symlink():
        raise FoilError("foil: refusing symlink")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _token_text(path: Path) -> tuple[str, int] | None:
    try:
        info = path.lstat()
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if path.is_symlink():
        return None
    return text, info.st_mode


def read_desk_token() -> str | None:
    found = _token_text(desk_path())
    if found is None:
        return None
    text, mode = found
    if mode & 0o077 or len(text.splitlines()) != 1:
        return None
    token = text.strip()
    return token or None


def ensure_desk_token() -> str:
    """The current desk token, or a new one. A re-run does not rotate it."""

    current = read_desk_token()
    if current is not None:
        return current
    path = desk_path()
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    found = _token_text(path)
    if found is not None:
        text, mode = found
        token = text.strip()
        if token and len(text.splitlines()) == 1 and mode & 0o077:
            os.chmod(path, 0o600)
            if path.stat().st_mode & 0o077:
                raise FoilError("foil: could not read the bridge token")
            return token
    token = secrets.token_urlsafe(32)
    write_private(path, token + "\n")
    return token


def _version_line(binary: str) -> str | None:
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = result.stdout.strip()
    if result.returncode != 0 or not line.startswith("foil-bridge "):
        return None
    return line


def show_bridge() -> dict[str, object]:
    binary = bridge_binary()
    if binary is None:
        raise FoilError(MISSING)
    line = _version_line(binary)
    if line is None:
        raise FoilError("foil: bridge failed")
    print(line)
    return {"installed": True, "version": line.split(" ", 1)[1]}


def _write_unit(path: Path, text: str) -> None:
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def install_bridge() -> dict[str, str]:
    binary = bridge_binary()
    if binary is None:
        raise FoilError(MISSING)
    path = current_unit_path()
    _write_unit(path, unit_text(binary))
    token = ensure_desk_token()
    port = bridge_port()
    url = f"http://127.0.0.1:{port}/?token={token}"
    if _platform() == "darwin":
        load = f"launchctl bootstrap gui/{os.getuid()} {path}"
    else:
        load = "systemctl --user enable --now foil-bridge.service"
    print(path)
    print(url)
    print(load)
    print(f"tailscale serve --bg {port}")
    return {"unit": str(path), "url": url}


def _worse(left: int, right: int) -> int:
    rank = {0: 0, 1: 1, 3: 2, 2: 3}
    return left if rank[left] >= rank[right] else right


def _bridge_line(binary: str | None) -> tuple[str, int]:
    if binary is None:
        return "bridge: not installed", 3
    if _version_line(binary) is None:
        return "bridge: failed", 2
    return "bridge: installed", 0


def _unit_line(binary: str | None) -> tuple[str, int]:
    try:
        path = current_unit_path()
    except FoilError:
        return "unit: unsupported", 2
    if path.is_symlink() or not path.is_file():
        return "unit: missing", 3
    if binary is None:
        return "unit: wrong", 2
    try:
        actual = path.read_text(encoding="utf-8")
        expected = unit_text(binary)
    except (OSError, FoilError):
        return "unit: wrong", 2
    if actual != expected:
        return "unit: wrong", 2
    return "unit: ok", 0


def _token_line() -> tuple[str, int]:
    path = desk_path()
    if path.is_symlink():
        return "token: too open", 2
    if not path.exists():
        return "token: missing", 3
    found = _token_text(path)
    if found is None:
        return "token: too open", 2
    text, mode = found
    if mode & 0o077 or len(text.splitlines()) != 1 or not text.strip():
        return "token: too open", 2
    return "token: ok", 0


def _listen_line(port: int) -> tuple[str, int]:
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        connection.request("GET", "/")
        status = connection.getresponse().status
        connection.close()
    except OSError:
        return "listen: down", 1
    if status == 401:
        return "listen: up", 0
    return "listen: open", 2


def _tailscale_line(port: int) -> tuple[str, int]:
    binary = shutil.which("tailscale")
    if binary is None:
        return "tailscale: not installed", 0
    try:
        result = subprocess.run(
            [binary, "serve", "status"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "tailscale: serve not set", 3
    text = f"{result.stdout}\n{result.stderr}"
    if result.returncode == 0 and re.search(rf"(?<!\d){port}(?!\d)", text):
        return "tailscale: ok", 0
    return "tailscale: serve not set", 3


def doctor_report() -> tuple[int, list[str]]:
    binary = bridge_binary()
    rows = [
        _bridge_line(binary),
        _unit_line(binary),
        _token_line(),
        _listen_line(bridge_port()),
        _tailscale_line(bridge_port()),
    ]
    code = 0
    for _line, severity in rows:
        code = _worse(code, severity)
    return code, [line for line, _severity in rows]


def run_doctor() -> dict[str, object]:
    code, lines = doctor_report()
    for line in lines:
        print(line)
    return {"code": code, "checks": lines}
