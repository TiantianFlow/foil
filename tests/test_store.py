"""Atomic JSON, private modes, and symlink refusal."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import threading
from pathlib import Path

import pytest

from foil.errors import FoilError
from foil.store import SCHEMA_VERSION, exclusive_lock, read_json, write_json


def test_write_is_private_and_leaves_no_partial_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "record.json"
    write_json(path, {"schema_version": SCHEMA_VERSION, "ok": True})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert list(path.parent.glob("*.tmp")) == []
    assert read_json(path)["ok"] is True


def test_failed_replace_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(src: Path, dst: Path) -> None:
        del src, dst
        raise OSError("disk")

    monkeypatch.setattr("foil.store.os.replace", boom)
    path = tmp_path / "record.json"
    with pytest.raises(OSError, match="disk"):
        write_json(path, {"schema_version": SCHEMA_VERSION})
    assert not path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_and_read_refuse_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "record.json"
    link.symlink_to(target)

    with pytest.raises(FoilError, match="refusing symlink"):
        write_json(link, {"schema_version": SCHEMA_VERSION})
    assert target.read_text(encoding="utf-8") == "{}"
    with pytest.raises(FoilError, match="refusing symlink"):
        read_json(link)


def test_unsupported_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    write_json(path, {"schema_version": SCHEMA_VERSION})
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FoilError, match="unsupported schema"):
        read_json(path)


def test_secret_shaped_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    with pytest.raises(FoilError, match="credential-shaped text refused"):
        write_json(path, {"schema_version": SCHEMA_VERSION, "token": "sk-secret"})
    assert not path.exists()


def test_exclusive_lock_blocks_other_waiters(tmp_path: Path) -> None:
    lock = tmp_path / "locks" / "store.lock"
    started = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with exclusive_lock(lock):
            started.set()
            assert release.wait(2)

    thread = threading.Thread(target=hold)
    thread.start()
    assert started.wait(2)
    descriptor = os.open(lock, os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)
        release.set()
        thread.join(2)
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
