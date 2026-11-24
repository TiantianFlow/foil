"""Transcript readers and ``foil-bridge transcript``. Synthetic files only."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

import pytest
from foil_bridge.cli import main as bridge_main
from foil_bridge.readers import read_session
from foil_bridge.transcript import _turn_times, mask

from foil.errors import FoilError
from foil.master import directory, empty_record, save_record
from foil.project import foil_root
from foil.runner import empty_record as turn_record
from foil.runner import record_path, remember_session
from foil.store import fleet_dir, load_registry, save_registry, write_json
from tests.test_spawn import _repo

GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "golden" / "transcript"
CWD = "/work/demo"
CLAUDE = "11111111-1111-4111-8111-111111111111"
CODEX = "22222222-2222-4222-8222-222222222222"
GROK = "44444444-4444-4444-8444-444444444444"
FAKE = "55555555-5555-4555-8555-555555555555"
OPEN = "ses_fixture1"
_HARNESS_ENV = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME", "XDG_DATA_HOME")


def _events(name: str) -> list[dict]:
    return json.loads((GOLDEN / f"{name}.events.json").read_text(encoding="utf-8"))


def _addressable(repo: Path) -> str:
    """A store key ``checkout_for_key`` accepts.

    ``project_key`` keeps the last 48 characters of a path, so a long
    temporary path can produce a key that starts with ``-``. Core refuses
    that shape. Copy the record under a name it accepts.
    """

    folder = foil_root(repo)
    if folder.name[:1].isalnum():
        return folder.name
    alias = folder.parent / "bridge-demo"
    record = alias / "project.json"
    if not record.is_file():
        alias.mkdir()
        record.write_text((folder / "project.json").read_text(encoding="utf-8"), encoding="utf-8")
    return alias.name


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _HARNESS_ENV:
        monkeypatch.delenv(name, raising=False)


def test_each_format_matches_its_golden_file(tmp_path: Path) -> None:
    encoded = "".join(char if char.isalnum() else "-" for char in CWD)
    claude = tmp_path / ".claude" / "projects" / encoded / f"{CLAUDE}.jsonl"
    claude.parent.mkdir(parents=True)
    claude.write_text((GOLDEN / "claude.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    assert read_session("claude", CLAUDE, CWD, tmp_path / ".claude")[0] == _events("claude")

    codex = (
        tmp_path
        / ".codex"
        / "sessions"
        / "2026"
        / "01"
        / "02"
        / f"rollout-2026-01-02T03-04-05-{CODEX}.jsonl"
    )
    codex.parent.mkdir(parents=True)
    codex.write_text((GOLDEN / "codex.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    assert read_session("codex", CODEX, CWD, tmp_path / ".codex", started="2026-01-02T03:04:05Z")[
        0
    ] == _events("codex")

    grok = tmp_path / ".grok" / "sessions" / quote(CWD, safe="") / GROK / "updates.jsonl"
    grok.parent.mkdir(parents=True)
    grok.write_text((GOLDEN / "grok.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    assert read_session("grok", GROK, CWD, tmp_path / ".grok")[0] == _events("grok")

    fake = tmp_path / ".foil-fake" / "sessions" / f"{FAKE}.jsonl"
    fake.parent.mkdir(parents=True)
    fake.write_text((GOLDEN / "fake.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    assert read_session("fake", FAKE, CWD, tmp_path / ".foil-fake")[0] == _events("fake")

    _plant_opencode(tmp_path / "opencode")
    assert read_session("opencode", OPEN, CWD, tmp_path / "opencode")[0] == _events("opencode")


def test_opencode_sqlite_matches_the_json_store(tmp_path: Path) -> None:
    spec = json.loads((GOLDEN / "opencode.json").read_text(encoding="utf-8"))
    database = tmp_path / "opencode.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        create table session (id text primary key);
        create table message (
            id text primary key, session_id text, time_created integer, data text
        );
        create table part (
            id text primary key, message_id text, session_id text, time_created integer, data text
        );
        """
    )
    connection.execute("insert into session (id) values (?)", (OPEN,))
    for index, message in enumerate(spec["messages"]):
        connection.execute(
            "insert into message (id, session_id, time_created, data) values (?, ?, ?, ?)",
            (message["id"], OPEN, message["time"], json.dumps(message)),
        )
        del index
    for index, part in enumerate(spec["parts"]):
        body = {"type": part["type"]}
        if part["type"] == "text":
            body["text"] = part["text"]
        else:
            body["tool"] = part["tool"]
            body["state"] = {"input": part["input"], "output": part["output"]}
        connection.execute(
            "insert into part (id, message_id, session_id, time_created, data) "
            "values (?, ?, ?, ?, ?)",
            (part["id"], part["message"], OPEN, index, json.dumps(body)),
        )
    connection.commit()
    connection.close()
    assert read_session("opencode", OPEN, CWD, tmp_path)[0] == _events("opencode")


def test_codex_is_found_from_the_journal_day_when_the_name_differs(tmp_path: Path) -> None:
    folder = tmp_path / "sessions" / "2026" / "01" / "03"
    folder.mkdir(parents=True)
    path = folder / f"rollout-2026-01-03T00-00-00-{CODEX}.jsonl"
    path.write_text((GOLDEN / "codex.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    found = read_session(
        "codex", CODEX, CWD, tmp_path, started="", turn_times=["2026-01-03T00:00:00Z"]
    )
    assert found is not None
    assert found[0] == _events("codex")


def test_a_symlink_or_unknown_format_is_not_a_file(tmp_path: Path) -> None:
    target = tmp_path / "real.jsonl"
    target.write_text((GOLDEN / "fake.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    link = tmp_path / "sessions" / f"{FAKE}.jsonl"
    link.parent.mkdir()
    link.symlink_to(target)
    assert read_session("fake", FAKE, CWD, tmp_path) is None
    assert read_session("no-such-format", FAKE, CWD, tmp_path) is None


def test_a_file_over_the_cap_is_marked_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("foil_bridge.readers.LIMIT", 80)
    path = tmp_path / "sessions" / f"{FAKE}.jsonl"
    path.parent.mkdir()
    path.write_text((GOLDEN / "fake.jsonl").read_text(encoding="utf-8") * 20, encoding="utf-8")
    found = read_session("fake", FAKE, CWD, tmp_path)
    assert found is not None
    events, truncated = found
    assert truncated
    assert events[0]["text"] == "Say ready."


def test_mask_hides_credential_shapes_and_leaves_ordinary_words() -> None:
    assert mask("see sk-fixture and ghp_fixture") == "see [hidden] and [hidden]"
    assert mask("Bearer fixture-token") == "[hidden]"
    assert mask("Authorization: Bearer fixture-token") == "Authorization: [hidden]"
    assert mask("authorization=Bearer fixture-token") == "authorization=[hidden]"
    assert mask("password=hunter2") == "password=[hidden]"
    assert mask("FOO=bar PASSWORD=hunter2") == "FOO=bar PASSWORD=[hidden]"
    assert "hunter2" not in mask('{"command":"FOO=bar PASSWORD=hunter2"}')
    assert mask('{"password": "hunter2"}') == '{"password": "[hidden]"}'
    assert mask('{"password":"hunter2","note":"ok"}') == '{"password":"[hidden]","note":"ok"}'
    assert mask("https://user:s3cret@example.test/repo") == "https://[hidden]@example.test/repo"
    assert mask("The status file is fine.") == "The status file is fine."
    assert "sk-fixture" not in mask('{"api_key": "sk-fixture"}')


def test_summary_is_the_default_and_levels_add_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    _clear_env(monkeypatch)
    home = tmp_path / "home"
    monkeypatch.setattr("foil_bridge.transcript.user_home", lambda: home)
    _seat(repo, FAKE, "fake")
    lines = [
        {"role": "user", "kind": "message", "text": f"note {index}", "tool": "", "usage": None}
        for index in range(21)
    ]
    lines.append(
        {
            "role": "assistant",
            "kind": "message",
            "text": "sk-fixture " + ("x" * 220),
            "tool": "",
            "usage": None,
        }
    )
    lines.append({"role": "tool", "kind": "tool", "text": "true", "tool": "shell", "usage": None})
    lines.append(
        {
            "role": "assistant",
            "kind": "usage",
            "text": "",
            "tool": "",
            "usage": {"input": 1, "output": 2},
        }
    )
    path = home / ".foil-fake" / "sessions" / f"{FAKE}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")

    assert bridge_main(["transcript", "lead", "--project", _addressable(repo)]) == 0
    human = capsys.readouterr().out
    assert human.startswith("lead\tfake\tfile\n")
    assert "user: note 0\n" not in human
    assert "user: note 1\n" not in human
    assert "user: note 2\n" in human
    assert human.count("user: note ") == 19
    assert "tool:" not in human
    assert "sk-fixture" not in human
    assert "[hidden]" in human
    assert "omitted 2\n" in human
    assert human.endswith("Secrets are masked as a best effort.\n")

    assert (
        bridge_main(
            ["transcript", "lead", "--project", _addressable(repo), "--level", "tools", "--json"]
        )
        == 0
    )
    tools = json.loads(capsys.readouterr().out)
    assert tools["level"] == "tools"
    assert tools["masked"] is True
    assert tools["tokens"] == {"input": 1, "output": 2}
    assert any(
        event["kind"] == "tool" and event["tool"] == "shell" and event["text"] == ""
        for event in tools["events"]
    )
    assert all(event["kind"] != "usage" for event in tools["events"])

    assert bridge_main(["transcript", "lead", "--level", "all", "--json"]) == 0
    everything = json.loads(capsys.readouterr().out)
    assert any(
        event["kind"] == "tool" and event["text"] == "true" for event in everything["events"]
    )
    assert any(
        event["kind"] == "usage" and event["usage"] == {"input": 1, "output": 2}
        for event in everything["events"]
    )


def test_a_missing_or_unknown_file_uses_the_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    _clear_env(monkeypatch)
    monkeypatch.setattr("foil_bridge.transcript.user_home", lambda: tmp_path / "home")
    _seat(repo, FAKE, "fake")
    monkeypatch.setattr(
        "foil_bridge.transcript.peek_text", lambda *args, **kwargs: "pane says hello\n"
    )
    assert bridge_main(["transcript", "lead", "--project", _addressable(repo), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "pane"
    assert payload["tokens"] == {"input": 0, "output": 0}
    assert payload["events"] == [
        {"role": "pane", "kind": "pane", "text": "pane says hello\n", "tool": "", "usage": None}
    ]

    _seat(repo, FAKE, "nope")
    assert bridge_main(["transcript", "lead", "--fleet", "main", "--json"]) == 0
    unknown = json.loads(capsys.readouterr().out)
    assert unknown["source"] == "pane"
    assert unknown["format"] == "unknown"


def test_when_the_pane_is_gone_the_command_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _clear_env(monkeypatch)
    monkeypatch.setattr("foil_bridge.transcript.user_home", lambda: tmp_path / "empty")

    def refuse(*args, **kwargs):
        raise FoilError("foil: seat 'lead' is killed")

    monkeypatch.setattr("foil_bridge.transcript.peek_text", refuse)
    _seat(repo, FAKE, "fake")
    assert bridge_main(["transcript", "lead"]) == 1
    assert capsys.readouterr().err == "foil-bridge: no transcript for 'lead'\n"


def test_usage_and_unknown_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    assert bridge_main(["transcript"]) == 2
    assert "usage: transcript SEAT" in capsys.readouterr().err
    assert bridge_main(["transcript", "lead", "--project"]) == 2
    assert capsys.readouterr().err == "foil-bridge: unexpected argument\n"
    assert bridge_main(["transcript", "lead", "--project", "-bridge-demo"]) == 1
    assert capsys.readouterr().err == "foil-bridge: unknown project '-bridge-demo'\n"
    assert bridge_main(["transcript", "lead", "--level", "loud"]) == 2
    assert capsys.readouterr().err == "foil-bridge: --level is summary, messages, tools, or all\n"
    assert bridge_main(["transcript", "nope"]) == 1
    assert capsys.readouterr().err == "foil-bridge: unknown seat 'nope'\n"
    monkeypatch.setattr("foil.api.VERSION", 2)
    assert bridge_main(["transcript", "lead"]) == 1
    assert capsys.readouterr().err == "foil-bridge: needs foil.api 1\n"


def test_the_master_reads_its_own_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear_env(monkeypatch)
    home = tmp_path / "home"
    monkeypatch.setattr("foil_bridge.transcript.user_home", lambda: home)
    root = directory()
    (root / "run").mkdir(parents=True)
    (root / "run" / "plan.json").write_text(
        json.dumps({"harness": "fake", "model": "", "cwd": CWD}), encoding="utf-8"
    )
    record = empty_record()
    record["sessions"] = [
        {"id": FAKE, "harness": "fake", "model": "", "started": "2026-01-02T03:04:05Z"}
    ]
    save_record(root, record)
    path = home / ".foil-fake" / "sessions" / f"{FAKE}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text((GOLDEN / "fake.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    assert bridge_main(["transcript", "master", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["seat"] == "master"
    assert payload["project"] == ""
    assert payload["format"] == "fake"
    assert payload["source"] == "file"
    assert payload["events"][0]["text"] == "Say ready."
    assert payload["events"][1]["text"] == "Ready."
    assert len(payload["events"]) == 2


def test_a_linked_directory_or_part_id_cannot_leave_the_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    leaked = outside / f"{FAKE}.jsonl"
    leaked.write_text((GOLDEN / "fake.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    root = tmp_path / "fake-home"
    root.mkdir()
    (root / "sessions").symlink_to(outside, target_is_directory=True)
    assert read_session("fake", FAKE, CWD, root) is None

    store = tmp_path / "opencode"
    message_dir = store / "storage" / "message" / OPEN
    message_dir.mkdir(parents=True)
    (message_dir / "m.json").write_text(
        json.dumps({"id": "../../../outside", "role": "user", "text": "inside"}),
        encoding="utf-8",
    )
    (outside / "p.json").write_text(
        json.dumps({"type": "text", "text": "OUTSIDE-PART"}),
        encoding="utf-8",
    )
    found = read_session("opencode", OPEN, CWD, store)
    assert found is not None
    assert all("OUTSIDE" not in event["text"] for event in found[0])
    assert any(event["text"] == "inside" for event in found[0])


def test_a_symlinked_journal_is_not_read(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    line = {"seat": "lead", "event": "turn_start", "time": "2026-01-03T00:00:00Z"}
    (outside / "journal.log").write_text(json.dumps(line) + "\n", encoding="utf-8")
    fleet = tmp_path / "fleet"
    fleet.mkdir()
    (fleet / "run").symlink_to(outside, target_is_directory=True)
    assert _turn_times(fleet, "lead") == []


def _plant_opencode(root: Path) -> None:
    spec = json.loads((GOLDEN / "opencode.json").read_text(encoding="utf-8"))
    for message in spec["messages"]:
        path = root / "storage" / "message" / spec["session"] / f"{message['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(message), encoding="utf-8")
    for part in spec["parts"]:
        path = root / "storage" / "part" / part["message"] / f"{part['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        body: dict = {"id": part["id"], "messageID": part["message"], "type": part["type"]}
        if part["type"] == "text":
            body["text"] = part["text"]
        else:
            body["tool"] = part["tool"]
            body["state"] = {"input": part["input"], "output": part["output"]}
        path.write_text(json.dumps(body), encoding="utf-8")


def _seat(repo: Path, session: str, harness: str) -> None:
    registry = load_registry(repo)
    registry["seats"]["lead"] = {
        "name": "lead",
        "template": "lead",
        "harness": harness,
        "model": "",
        "window_id": "",
        "state": "idle",
        "worktree": "",
        "branch": "",
        "session_id": session,
    }
    save_registry(repo, registry)
    fleet = fleet_dir(repo)
    record = turn_record()
    remember_session(
        record, session_id=session, harness=harness, model="", started="2026-01-02T03:04:05Z"
    )
    write_json(record_path(fleet / "board", "lead"), record)
    plan = fleet / "run" / "plans" / "lead.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(json.dumps({"cwd": CWD, "harness": harness}), encoding="utf-8")
