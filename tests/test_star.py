"""The init report's star line: when it shows, and that it never breaks init."""

from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from foil import __version__, lifecycle
from foil.cli import main
from foil.project import store_root

MINOR = ".".join(__version__.split(".")[:2])
LINE = "For the human: if Foil helps, a GitHub star helps others find it:"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    subprocess.run(["git", "init", "--quiet", str(path)], check=True, capture_output=True)
    monkeypatch.chdir(path)
    return path


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _record(shown: list[str], minor: str = MINOR, stop: bool = False) -> None:
    path = store_root() / "star.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "shown": shown, "minor": minor, "stop": stop}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _init(capsys: pytest.CaptureFixture[str]) -> str:
    assert main(["init"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    return captured.out


def _saved() -> dict:
    return json.loads((store_root() / "star.json").read_text(encoding="utf-8"))


def test_first_init_shows_the_line_once_and_records_it(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = _init(capsys).splitlines()
    star = [line for line in lines if line.startswith(LINE)]
    assert len(star) == 1
    assert "https://github.com/TiantianFlow/foil" in star[0]
    assert str(store_root() / "star.json") in star[0]
    assert lines.index(star[0]) == len(lines) - 2
    assert lines[-1].startswith("Read ")
    assert _saved() == {
        "schema_version": 1,
        "shown": [date.today().isoformat()],
        "minor": MINOR,
        "stop": False,
    }

    assert LINE not in _init(capsys)
    assert _saved()["shown"] == [date.today().isoformat()]


@pytest.mark.parametrize(
    ("shown", "minor", "due"),
    [
        ([_ago(2)], MINOR, False),
        ([_ago(3)], MINOR, True),
        ([_ago(20), _ago(13)], MINOR, False),
        ([_ago(20), _ago(14)], MINOR, True),
        ([_ago(90), _ago(80), _ago(40)], MINOR, False),
        ([_ago(90), _ago(80), _ago(40)], "0.0", True),
        ([_ago(90), _ago(80), _ago(29)], "0.0", False),
        ([_ago(90), _ago(80), _ago(70), _ago(31)], "0.0", True),
        ([_ago(-1)], MINOR, False),
    ],
)
def test_later_lines_follow_the_schedule(
    repo: Path,
    capsys: pytest.CaptureFixture[str],
    shown: list[str],
    minor: str,
    due: bool,
) -> None:
    _record(shown, minor)
    out = _init(capsys)
    assert (LINE in out) is due
    saved = _saved()
    if due:
        assert saved["shown"] == [*shown, date.today().isoformat()]
        assert saved["minor"] == MINOR
    else:
        assert saved["shown"] == shown
        assert saved["minor"] == minor


def test_stop_silences_every_later_line(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _record([_ago(400)], "0.0", stop=True)
    assert LINE not in _init(capsys)
    assert _saved()["stop"] is True


@pytest.mark.parametrize("text", ["not json", '{"schema_version": 1}', '{"schema_version": 9}'])
def test_an_unreadable_record_shows_nothing_and_is_left_alone(
    repo: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    path = store_root() / "star.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    assert LINE not in _init(capsys)
    assert path.read_text(encoding="utf-8") == text


def test_a_failed_save_still_finishes_init(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(lifecycle, "write_json", refuse)
    assert LINE in _init(capsys)
    assert not (store_root() / "star.json").exists()
