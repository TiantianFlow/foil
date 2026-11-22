"""``foil bridge``, ``foil bridge install``, and ``foil doctor`` without the page."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from foil_bridge.cli import main as bridge_main

from foil.bridge_cmd import (
    MISSING,
    desk_path,
    ensure_desk_token,
    launchd_plist,
    read_desk_token,
    service_environment,
    systemd_unit,
    unit_path,
    unit_text,
)
from foil.cli import main
from foil.errors import FoilError


def _stub(folder: Path, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    binary = folder / "foil-bridge"
    binary.write_text(text, encoding="utf-8")
    binary.chmod(0o755)
    return binary


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, binary: str) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PATH", str(Path(binary).parent) if binary else str(tmp_path / "empty"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)


def test_missing_bridge_prints_the_install_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path, "")
    (tmp_path / "empty").mkdir()
    assert main(["bridge"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == MISSING + "\n"
    assert "git+https://github.com/TiantianFlow/foil.git#subdirectory=addons/bridge" in captured.err


def test_present_bridge_prints_its_version_and_does_not_listen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = _stub(tmp_path / "bin", "#!/bin/sh\nprintf 'foil-bridge 0.1.0\\n'\n")
    _isolate(monkeypatch, tmp_path, str(binary))
    assert main(["bridge"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "foil-bridge 0.1.0\n"
    assert captured.err == ""
    assert not (tmp_path / "config").exists() or not any((tmp_path / "config").rglob("*"))


def test_a_failing_program_is_bridge_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = _stub(tmp_path / "bin", "#!/bin/sh\nexit 9\n")
    _isolate(monkeypatch, tmp_path, str(binary))
    assert main(["bridge"]) == 1
    assert capsys.readouterr().err == "foil: bridge failed\n"


def test_install_writes_the_unit_and_does_not_run_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = tmp_path / "ran"
    binary = _stub(
        tmp_path / "bin",
        f"#!/bin/sh\nprintf ran >> {marker}\nexit 9\n",
    )
    _isolate(monkeypatch, tmp_path, str(binary))

    def refuse_process(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("install started a process")

    monkeypatch.setattr("foil.bridge_cmd.subprocess.run", refuse_process)
    assert main(["bridge", "install"]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 4
    unit = Path(lines[0])
    assert unit.is_file()
    assert stat.S_IMODE(unit.stat().st_mode) == 0o644
    assert unit.read_text(encoding="utf-8") == unit_text(str(binary.resolve()))
    assert "Description=Foil Bridge" in unit.read_text(encoding="utf-8")
    assert f"ExecStart={binary.resolve()}" in unit.read_text(encoding="utf-8")
    assert "WantedBy=default.target" in unit.read_text(encoding="utf-8")
    assert lines[1].startswith("http://127.0.0.1:")
    assert "?token=" in lines[1]
    assert lines[2] == "systemctl --user enable --now foil-bridge.service"
    assert lines[3].startswith("tailscale serve --bg ")
    token = read_desk_token()
    assert token is not None and lines[1].endswith(token)
    assert stat.S_IMODE(desk_path().stat().st_mode) == 0o600
    assert stat.S_IMODE(desk_path().parent.stat().st_mode) == 0o700
    assert not marker.exists()

    assert main(["bridge", "install"]) == 0
    capsys.readouterr()
    assert read_desk_token() == token


def test_install_and_doctor_are_outside_the_fleet_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path, "")
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["doctor"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "foil: doctor is outside the fleet only\n"
    assert main(["bridge"]) == 1
    assert capsys.readouterr().err == "foil: bridge is outside the fleet only\n"
    assert main(["bridge", "install"]) == 1
    assert capsys.readouterr().err == "foil: bridge is outside the fleet only\n"
    assert not (tmp_path / "config").exists()


def test_doctor_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path, "")
    (tmp_path / "empty").mkdir()
    assert main(["doctor"]) == 3
    text = capsys.readouterr().out
    assert text.splitlines()[0] == "bridge: not installed"
    assert "unit: missing" in text
    assert "token: missing" in text
    assert "listen: down" in text
    assert "tailscale: not installed" in text

    binary = _stub(tmp_path / "bin", "#!/bin/sh\nprintf 'foil-bridge 0.1.0\\n'\n")
    monkeypatch.setenv("PATH", str(binary.parent))
    unit = unit_path("linux", home=tmp_path / "home", config=tmp_path / "config")
    unit.parent.mkdir(parents=True)
    unit.write_text("not the unit\n", encoding="utf-8")
    assert main(["doctor"]) == 2
    assert "unit: wrong" in capsys.readouterr().out

    unit.write_text(unit_text(str(binary.resolve())), encoding="utf-8")
    ensure_desk_token()
    os.chmod(desk_path(), 0o644)
    assert main(["doctor"]) == 2
    assert "token: too open" in capsys.readouterr().out

    monkeypatch.setattr("foil.bridge_cmd._platform", lambda: "freebsd")
    assert main(["doctor"]) == 2
    assert "unit: unsupported" in capsys.readouterr().out


def test_doctor_listen_open_is_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Open(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Open)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _isolate(monkeypatch, tmp_path, "")
        (tmp_path / "empty").mkdir()
        binary = _stub(tmp_path / "bin", "#!/bin/sh\nprintf 'foil-bridge 0.1.0\\n'\n")
        monkeypatch.setenv("PATH", str(binary.parent))
        monkeypatch.setenv("FOIL_BRIDGE_PORT", str(server.server_address[1]))
        assert main(["doctor"]) == 2
        assert "listen: open" in capsys.readouterr().out
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_invalid_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path, "")
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("FOIL_BRIDGE_PORT", "80")
    assert main(["doctor"]) == 1
    assert capsys.readouterr().err == "foil: invalid port\n"


def test_unit_paths_and_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    config = tmp_path / "config"
    assert unit_path("linux", home=home, config=config) == (
        config / "systemd" / "user" / "foil-bridge.service"
    )
    assert unit_path("linux", home=home, config=None) == (
        home / ".config" / "systemd" / "user" / "foil-bridge.service"
    )
    assert unit_path("darwin", home=home, config=None) == (
        home / "Library" / "LaunchAgents" / "com.foil.bridge.plist"
    )
    with pytest.raises(FoilError, match="Linux and macOS"):
        unit_path("freebsd", home=home, config=None)

    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("FOIL_BRIDGE_PORT", "8791")
    monkeypatch.delenv("FOIL_HOME", raising=False)
    env = service_environment()
    text = systemd_unit("/usr/bin/foil-bridge", env)
    assert "Type=simple" in text
    assert "Restart=on-failure" in text
    assert 'Environment="FOIL_BRIDGE_PORT=8791"' in text
    plist = launchd_plist("/usr/bin/foil-bridge", env)
    assert "<string>com.foil.bridge</string>" in plist
    assert "<key>RunAtLoad</key>" in plist
    assert "<key>KeepAlive</key>" in plist
    assert "<true/>" in plist

    monkeypatch.setenv("FOIL_HOME", "one\ntwo")
    with pytest.raises(FoilError, match="service environment"):
        service_environment()
    with pytest.raises(FoilError, match="service environment"):
        systemd_unit("/opt/foil bridge", env)


def test_core_does_not_import_the_bridge() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "foil"
    offenders = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "foil_bridge" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_bridge_program_version_and_pin(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert bridge_main(["--version"]) == 0
    assert capsys.readouterr().out == "foil-bridge 0.1.0\n"
    assert bridge_main(["serve"]) == 2
    assert capsys.readouterr().err == "foil-bridge: unexpected argument\n"
    monkeypatch.setattr("foil.api.VERSION", 2)
    assert bridge_main([]) == 1
    assert capsys.readouterr().err == "foil-bridge: needs foil.api 1\n"
