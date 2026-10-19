"""The init report's star line: when it shows, and that it never breaks init."""

from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from foil import __version__, lifecycle
from foil.cli import main
from foil.project import foil_root, store_root

MINOR = ".".join(__version__.split(".")[:2])
LINE = "For the human: if Foil helps, a GitHub star helps others find it:"
TODAY = date(2026, 10, 18)


class _Today(date):
    @classmethod
    def today(cls) -> date:
        return TODAY


@pytest.fixture(autouse=True)
def _fixed_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lifecycle, "date", _Today)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    subprocess.run(["git", "init", "--quiet", str(path)], check=True, capture_output=True)
    monkeypatch.chdir(path)
    return path


def _ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


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
    assert "star.json" not in star[0]
    assert lines.index(star[0]) == len(lines) - 2
    assert lines[-1].startswith("Read ")
    assert _saved() == {
        "schema_version": 1,
        "shown": [TODAY.isoformat()],
        "minor": MINOR,
        "stop": False,
    }

    assert LINE not in _init(capsys)
    assert _saved()["shown"] == [TODAY.isoformat()]


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
        ([_ago(90), _ago(80), _ago(30)], "0.0", True),
        ([_ago(90), _ago(80), _ago(40)], "9.9", False),
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
        assert saved["shown"] == [*shown, TODAY.isoformat()]
        assert saved["minor"] == MINOR
    else:
        assert saved["shown"] == shown
        assert saved["minor"] == minor


def test_stop_silences_every_later_line(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _record([_ago(400)], "0.0", stop=True)
    assert LINE not in _init(capsys)
    assert _saved()["stop"] is True


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"schema_version": 1}',
        '{"schema_version": 9}',
        '{"schema_version": 1, "shown": "2026-01-01", "minor": "0.0", "stop": false}',
        '{"schema_version": 1, "shown": ["2026-01-01"], "minor": "0.0", "stop": 0}',
        '{"schema_version": 1, "shown": ["2026-01-01"], "stop": false}',
        "deep",
    ],
)
def test_an_unreadable_record_shows_nothing_and_is_left_alone(
    repo: Path, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    if text == "deep":
        text = '{"schema_version": 1, "shown": ' + "[" * 100000 + "]" * 100000 + "}"
    path = store_root() / "star.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    assert LINE not in _init(capsys)
    assert path.read_text(encoding="utf-8") == text


def test_a_directory_in_place_of_the_record_shows_nothing(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (store_root() / "star.json").mkdir(parents=True)
    assert LINE not in _init(capsys)
    assert (store_root() / "star.json").is_dir()


def test_a_failed_save_shows_nothing_and_still_finishes_init(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(lifecycle, "tempfile", SimpleNamespace(mkstemp=refuse))
    assert LINE not in _init(capsys)
    assert LINE not in _init(capsys)
    assert not (store_root() / "star.json").exists()


def test_an_init_that_fails_after_the_check_writes_no_record(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init(capsys)
    (store_root() / "star.json").unlink()
    mapping = foil_root(repo) / "mapping.toml"
    mapping.write_text(mapping.read_text(encoding="utf-8") + 'permission = "bogus"\n')
    assert main(["init"]) == 1
    assert LINE not in capsys.readouterr().out
    assert not (store_root() / "star.json").exists()


def test_the_recorded_minor_never_goes_down(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _record([_ago(20), _ago(14)], "9.9")
    assert LINE in _init(capsys)
    assert _saved()["minor"] == "9.9"


def test_a_saved_record_keeps_keys_it_does_not_use(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = store_root() / "star.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "shown": [_ago(3)], "minor": MINOR, "stop": False, "note": "x"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert LINE in _init(capsys)
    assert _saved()["note"] == "x"


def test_saving_the_record_leaves_the_store_root_mode_alone(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_root().mkdir(parents=True, exist_ok=True)
    store_root().chmod(0o755)
    assert LINE in _init(capsys)
    assert store_root().stat().st_mode & 0o777 == 0o755
