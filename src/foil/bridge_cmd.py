"""``foil bridge`` and ``foil doctor``. Core does not import the Bridge package.

The boot file and the desk token live here because ``foil bridge install``
has to work when the package is present on ``PATH`` and when the command
only needs to report that it is missing.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
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


def _owner_only(path: Path) -> bool:
    try:
        return os.stat(path).st_mode & 0o777 == 0o600
    except OSError:
        return False


def _json_object(path: Path) -> dict | None | bool:
    """None when missing, False when unusable, else the object."""

    if path.is_symlink() or not path.is_file():
        return None if not path.exists() else False
    try:
        if path.stat().st_size > 8192:
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return data if isinstance(data, dict) else False


def _vapid_ok(data: dict) -> bool:
    raw = data.get("d")
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        return False
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except ValueError:
        return False
    return len(decoded) == 32


def _stamp(path: Path) -> str:
    """fresh, stale, missing, or broken."""

    if path.is_symlink():
        return "broken"
    data = _json_object(path)
    if data is None:
        return "missing"
    if data is False:
        return "broken"
    moment = data.get("t")
    if isinstance(moment, bool) or not isinstance(moment, int | float):
        return "broken"
    if time.time() - float(moment) > 120:
        return "stale"
    return "fresh"


def _push_line() -> tuple[str, int]:
    root = bridge_root()
    push = root / "push"
    hook = root / "webhook"
    paths = (
        push / "settings",
        push / "vapid",
        push / "subscriptions",
        push / "heartbeat",
        hook / "settings",
        hook / "heartbeat",
    )
    if any(path.is_symlink() for path in paths):
        return "push: broken", 2
    subscriptions = paths[2]
    if subscriptions.exists() and not subscriptions.is_dir():
        return "push: broken", 2
    settings = _json_object(paths[0])
    webhook = _json_object(paths[4])
    if settings is False or webhook is False:
        return "push: broken", 2
    for path, data in ((paths[0], settings), (paths[4], webhook)):
        if data is not None and not _owner_only(path):
            return "push: broken", 2
    kinds_on = False
    down = False
    if isinstance(settings, dict):
        for name in ("question", "seat", "delivery", "done", "down"):
            if settings.get(name) is True:
                kinds_on = True
        down = settings.get("down") is True
    hook_on = isinstance(webhook, dict) and webhook.get("enabled") is True
    if not kinds_on and not hook_on:
        return "push: off", 0
    vapid = _json_object(paths[1])
    if vapid is None:
        return "push: waiting", 3
    if vapid is False or not _vapid_ok(vapid) or not _owner_only(paths[1]):
        return "push: broken", 2
    for required, path in ((down, paths[3]), (hook_on, paths[5])):
        if not required:
            continue
        state = _stamp(path)
        if state == "broken":
            return "push: broken", 2
        if state in {"missing", "stale"}:
            return "push: stale", 1
    return "push: ok", 0


def _master_line() -> tuple[str, int]:
    from foil.master import _alive, _load_window, directory, load_record

    root = directory()
    run = root / "run"
    if (run / "window.json").is_symlink() or (run / "record.json").is_symlink():
        return "master: broken", 2
    window = _load_window(root)
    if not str(window.get("window_id") or "").startswith("@"):
        return "master: not started", 0
    if not _alive(window):
        return "master: down", 1
    if load_record(root).get("paused"):
        return "master: paused", 1
    return "master: ok", 0


def _fleet_runner(key: str, checkout: Path, fleet: str) -> tuple[str, int]:
    from foil.errors import FoilError
    from foil.runner import load_record
    from foil.store import fleet_dir, load_registry
    from foil.tmux import TmuxController, TmuxError

    try:
        registry = load_registry(checkout, fleet)
    except (FoilError, OSError):
        return f"runner: {key} {fleet} broken", 2
    launched = [
        (name, seat)
        for name, seat in sorted(registry["seats"].items())
        if seat.get("state") not in {"killed", "joined"}
    ]
    if not launched:
        return f"runner: {key} {fleet} none", 0
    down = False
    paused = False
    fleet_id = str(registry.get("fleet_id") or fleet)
    session = str(registry.get("tmux_session") or "")
    for name, seat in launched:
        try:
            owned = TmuxController().matches_window(
                fleet_id, name, session, seat.get("window_id") or ""
            )
        except TmuxError:
            owned = False
        if not owned:
            down = True
            continue
        if load_record(fleet_dir(checkout, fleet) / "board", name).get("paused"):
            paused = True
    if down:
        return f"runner: {key} {fleet} down", 1
    if paused:
        return f"runner: {key} {fleet} paused", 1
    return f"runner: {key} {fleet} ok", 0


def _runner_lines() -> list[tuple[str, int]]:
    from foil.inbox import projects
    from foil.store import fleet_ids

    lines = [
        _fleet_runner(key, checkout, fleet)
        for key, checkout in projects()
        for fleet in fleet_ids(checkout)
    ]
    return lines or [("runners: none", 0)]


def doctor_report() -> tuple[int, list[str]]:
    binary = bridge_binary()
    rows = [
        _bridge_line(binary),
        _unit_line(binary),
        _token_line(),
        _listen_line(bridge_port()),
        _tailscale_line(bridge_port()),
        _master_line(),
        *_runner_lines(),
        _push_line(),
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
