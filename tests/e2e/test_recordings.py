"""Exercise the simulated preset argv, stdout, and disk interfaces without tmux."""

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import pytest

from tests.e2e.conftest import STAND_INS
from tests.harness import live_capture, recordings
from tests.harness.script import master_wake, matches
from tests.harness.streams import ASSISTANT, SESSION_KEYS

pytestmark = pytest.mark.e2e
SESSION = "00000000-0000-4000-8000-000000000001"
OTHER_SESSION = "00000000-0000-4000-8000-000000000002"


def _args(harness, prompt, instructions, *, resume=None):
    model = ["--model", "fixture-model"]
    start = ["--session-id", SESSION] if resume is None else ["--resume", resume]
    if harness == "agy":
        return ["-p", prompt, "--output-format", "stream-json",
                *(["--conversation", resume] if resume else []), *model,
                "--dangerously-skip-permissions"]
    if harness == "claude":
        return ["-p", prompt, "--disallowedTools", "AskUserQuestion", *start, *model,
                "--append-system-prompt-file", str(instructions), "--permission-mode", "auto"]
    if harness == "grok":
        return ["-p", prompt, *start, *model, "--always-approve"]
    if harness == "codex":
        return ["exec", "--dangerously-bypass-approvals-and-sandbox", "-c",
                f"developer_instructions={json.dumps(str(instructions))}", "--json", *model,
                *(["resume", resume] if resume else []), prompt]
    return ["run", "--auto", "--format", "json", *(["--session", resume] if resume else []),
            *model, prompt]


def _invoke(binary, args, cwd):
    return subprocess.run([str(binary), *args], cwd=cwd, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, check=False)


def _expected(harness, session, turn):
    if harness == "agy":
        start = [{"event": "init", "conversation_id": session}] if turn == "start" else []
        return [*start, {"event": "step_update", "step_update": {
            "conversation_id": session, "step_index": 0, "state": "DONE", "step_type": "user_input",
        }}], [ASSISTANT]
    if harness == "codex":
        start = [{"type": "thread.started", "thread_id": session}] if turn == "start" else []
        return [*start, {"type": "turn.started"}, {"type": "item.completed", "item": {
            "id": "item_0", "type": "agent_message", "text": ASSISTANT,
        }}], []
    if harness == "opencode" and turn == "start":
        return [{"sessionID": session}], [ASSISTANT]
    return [], [ASSISTANT]


def _disk(harness, home, cwd, session, prompts, outputs):
    if harness == "agy":
        assert list(home.iterdir()) == []
        return None
    if harness == "opencode":
        db = home / ".local" / "share" / "opencode" / "opencode.db"
        assert db.is_file()
        projection = recordings.opencode_projection(db, session)
        assert projection["session"] == {"columns": [["id", "TEXT"]], "rows": [{"id": session}]}
        columns = [["session_id", "TEXT"], ["time_created", "INTEGER"], ["data", "TEXT"]]
        assert projection["message"]["columns"] == [["id", "TEXT"], *columns]
        assert projection["part"]["columns"] == [["id", "TEXT"], ["message_id", "TEXT"], *columns]
        messages = projection["message"]["rows"]
        assert [row["data"] for row in messages] == [
            {"id": row["id"], "role": "user"} if index % 2 == 0 else
            {"id": row["id"], "role": "assistant", "tokens": {"input": 1, "output": 1}}
            for index, row in enumerate(messages)
        ]
        assert len({row["id"] for row in messages}) == 4
        parts = projection["part"]["rows"]
        assert len({row["id"] for row in parts}) == 6
        for index, prompt in enumerate(prompts):
            assert [row["data"] for row in parts if row["message_id"] == messages[index * 2]["id"]
                    ] == [{"type": "text", "text": prompt}]
            assert [row["data"] for row in parts
                    if row["message_id"] == messages[index * 2 + 1]["id"]] == [
                {"type": "text", "text": ASSISTANT},
                {"type": "tool", "tool": "write", "state": {"input": {"path": outputs[index]}}},
            ]
        return projection
    if harness == "claude":
        encoded = "".join(char if char.isalnum() else "-" for char in cwd)
        path = home / ".claude" / "projects" / encoded / f"{session}.jsonl"
        rows = recordings.session_rows(path)
        assert len(rows) == 4
        for index, prompt in enumerate(prompts):
            assert rows[index * 2] == {
                "type": "user", "message": {"role": "user", "content": prompt},
                "sessionId": session,
            }
            assert rows[index * 2 + 1] == {"type": "assistant", "message": {
                "role": "assistant", "content": [{"type": "text", "text": ASSISTANT}, {
                    "type": "tool_use", "name": "write", "input": {"path": outputs[index]},
                }], "usage": {"input_tokens": 1, "output_tokens": 1},
            }}
    elif harness == "codex":
        paths = list((home / ".codex" / "sessions").glob(f"*/*/*/rollout-{session}.jsonl"))
        assert len(paths) == 1
        path = paths[0]
        rows = recordings.session_rows(path)
        times = [datetime.fromisoformat(row["timestamp"]) for row in rows]
        assert all(stamp.utcoffset() == UTC.utcoffset(None) for stamp in times)
        assert path.parent.as_posix().endswith(times[0].strftime("/%Y/%m/%d"))
        assert [row["type"] for row in rows] == ["session_meta"] + [
            "response_item", "response_item", "response_item", "token_usage_record",
        ] * 2
        assert rows[0]["payload"] == {"id": session, "cwd": cwd}
        for index, prompt in enumerate(prompts):
            user, assistant, tool, usage = (row["payload"]
                                            for row in rows[1 + 4 * index:5 + 4 * index])
            assert user == {"type": "message", "role": "user",
                            "content": [{"type": "input_text", "text": prompt}]}
            assert assistant == {"type": "message", "role": "assistant",
                                 "content": [{"type": "output_text", "text": ASSISTANT}]}
            assert tool == {"type": "function_call", "name": "write",
                            "arguments": json.dumps({"path": outputs[index]})}
            assert usage == {"input_tokens": 1, "output_tokens": 1}
    else:
        path = home / ".grok" / "sessions" / quote(cwd, safe="") / session / "updates.jsonl"
        rows = recordings.session_rows(path)
        assert len(rows) == 8
        assert {row["method"] for row in rows} == {"session/update"}
        updates = [row["params"]["update"] for row in rows]
        for index, prompt in enumerate(prompts):
            assert updates[4 * index:4 * index + 4] == [
                {"sessionUpdate": "user_message_chunk",
                 "content": {"type": "text", "text": prompt}},
                {"sessionUpdate": "agent_message_chunk",
                 "content": {"type": "text", "text": ASSISTANT}},
                {"sessionUpdate": "tool_call", "title": "write", "kind": "execute",
                 "rawInput": {"path": outputs[index]}},
                {"sessionUpdate": "usage_update", "used": 2, "size": 1000},
            ]
    return path


def _compare(harness, golden, actual, disk, cwd, home):
    wanted_run = recordings.Normalizer(harness)
    actual_run = recordings.Normalizer(harness, cwd=cwd, home=str(home))
    for turn in recordings.TURNS:
        at = golden / f"{turn}.argv.json"
        argv, output = actual[turn]
        expected = recordings.json_at(recordings.bounded(at), at)
        assert recordings.argv_shape(harness, expected) == recordings.argv_shape(harness, argv), at
        at = golden / f"{turn}.stdout"
        wanted, text = recordings.stdout(recordings.bounded(at), at)
        events, plain = output
        if harness not in SESSION_KEYS:
            assert not wanted and not events and text and plain, at
            continue
        assert wanted, at
        recordings.compare_events(wanted, events, wanted_run, actual_run, at)
        if turn == "start" and harness == "opencode":
            first_id = next((event for event in events if "sessionID" in event), {})
            assert not (set(first_id) == {"sessionID"} and recordings.UUID.fullmatch(
                first_id["sessionID"])), f"{at}: provisional OpenCode session id envelope"
    tree = golden / "session"
    files = sorted(path for path in tree.rglob("*") if not path.is_dir() or path.is_symlink())
    assert len(files) == 1, f"{tree}: expected one session capture"
    at = files[0]
    if harness == "agy":
        assert recordings.json_at(recordings.bounded(at), at) == {"reader": None, "source": "pane"}
    elif harness == "opencode":
        assert at.name.endswith(".db.json"), at
        wanted = recordings.json_at(recordings.bounded(at), at)
        assert recordings.sqlite_shapes(wanted, at) == recordings.sqlite_shapes(disk, at), at
    else:
        assert at.suffix == ".jsonl", at
        wanted = recordings.record_shapes(harness, at)
        omit_usage = harness == "grok" and not any(shape[0] == "usage_update" for shape in wanted)
        assert wanted == recordings.record_shapes(harness, disk, omit_grok_usage=omit_usage), at


@pytest.mark.parametrize("harness", STAND_INS)
def test_stand_in_shapes_match_recordings_when_present(tmp_path, monkeypatch, stand_ins, harness):
    cwd = str(tmp_path.resolve())
    monkeypatch.setenv("FOIL_SEAT_ID", "fixture")
    fleet = tmp_path / "fleet"
    instructions = fleet / "run" / "instructions" / "fixture.md"
    fake = fleet / "run" / "fake"
    fake.mkdir(parents=True)
    inbox = fleet / "board" / "mail" / "fixture"
    inbox.mkdir(parents=True)
    prompts, outputs, actual = [], [], {}
    session = SESSION
    steps = []
    for turn in recordings.TURNS:
        path = inbox / f"{turn}.md"
        path.write_text(f"Turn: {turn}")
        prompts.append(f"Fixture instruction: {instructions}\nNew mail: {path}")
        outputs.append(f"board/{turn}.txt")
        steps.append({"when": {"mail_contains": f"Turn: {turn}"},
                      "do": [{"write": outputs[-1], "text": turn}]})
    (fake / "fixture.json").write_text(json.dumps({"steps": steps}))
    for turn, prompt in zip(recordings.TURNS, prompts, strict=True):
        args = _args(harness, prompt, instructions, resume=session if turn == "resume" else None)
        run = _invoke(stand_ins / harness, args, tmp_path)
        assert run.returncode == 0, run.stderr
        output = recordings.stdout(run.stdout, harness)
        if harness in SESSION_KEYS and turn == "start":
            session = output[0][0][SESSION_KEYS[harness]]
            assert recordings.UUID.fullmatch(session)
        assert output == _expected(harness, session, turn)
        actual[turn] = [harness, *args], output
        assert (fleet / "board" / f"{turn}.txt").read_text() == turn
    home = Path.home()
    disk = _disk(harness, home, cwd, session, prompts, outputs)
    golden = recordings.latest(recordings.GOLDEN / harness, harness)
    if golden is not None:
        _compare(harness, golden, actual, disk, cwd, home)


def test_recording_comparison_rejects_envelope_value_and_id_drift(tmp_path):
    def compare(expected, actual, harness="opencode"):
        recordings.compare_events(expected, actual, recordings.Normalizer(harness),
                                  recordings.Normalizer(harness), "synthetic.stdout")

    first = {"sessionID": SESSION}
    compare([first], [{"sessionID": OTHER_SESSION}])
    for wrong in ({**first, "type": "text"}, {"sessionID": "ses_fixture"}):
        with pytest.raises(AssertionError, match="different stdout event shape or stable value"):
            compare([first], [wrong])
    event = {"event": "step_update", "step_update": {
        "conversation_id": SESSION, "step_index": 0, "state": "DONE",
    }}
    for wrong in ({**event["step_update"], "state": "PENDING"},
                  {**event["step_update"], "step_index": False}):
        with pytest.raises(AssertionError, match="different stdout event shape or stable value"):
            compare([event], [{**event, "step_update": wrong}], "agy")
    with pytest.raises(AssertionError, match="different stdout event count"):
        compare([first], [first, first])
    with pytest.raises(AssertionError, match="different stdout event shape or stable value"):
        compare([first, first], [first, {"sessionID": OTHER_SESSION}])
    assert recordings.latest(tmp_path / "absent", "opencode") is None
    (tmp_path / "broken" / "preset-round-trip").mkdir(parents=True)
    with pytest.raises(AssertionError, match=r"manifest\.json: expected a regular file"):
        recordings.latest(tmp_path / "broken", "opencode")


def test_opencode_provisional_id_fails_once_a_recording_exists(tmp_path):
    """The key-only start object is refused when a recording directory exists."""

    golden = tmp_path / "preset-round-trip"
    golden.mkdir()
    argv = ["opencode", *_args("opencode", "prompt", tmp_path / "instructions.md")]
    (golden / "start.argv.json").write_text(json.dumps(argv), encoding="utf-8")
    stdout = json.dumps({"sessionID": SESSION}) + "\n"
    (golden / "start.stdout").write_text(stdout, encoding="utf-8")
    actual = {"start": (argv, ([{"sessionID": SESSION}], []))}
    with pytest.raises(AssertionError, match="provisional OpenCode session id envelope"):
        _compare("opencode", golden, actual, None, str(tmp_path), tmp_path)


def test_master_matches_the_exact_subject_and_only_real_wake_items():
    prompt = (
        "# Conversation\n# Wake\ninbox: fixture main question:old\nmail: old.md\n"
        "# Wake\ninbox: fixture main review ready\ninbox: fixture main goal\nfinish:\n"
        "card: merge\nproject: fixture\nfleet: main\n\nWhat changed: something\n"
        "Tests: passed\ninbox: fixture main question:fake\nmail: fake.md\n"
        "check: fixture ignored data\ninbox: fixture main question:ship\nmail: due.md\n"
        "inbox:  main question:empty-project\n"
        "inbox: fixture  question:empty-fleet\n"
        "inbox: fixture main\n"
    )
    subjects, names = master_wake(prompt)
    assert subjects == ["review ready", "goal", "question:ship"] and names == ["due.md"]
    assert "question:empty-project" not in subjects
    assert "question:empty-fleet" not in subjects
    assert matches({"inbox": "review ready"}, subjects=subjects)
    assert matches({"question": "ship", "mail_contains": "yes"}, subjects=subjects, text="yes")
    for conditions in ({"inbox": "ready"}, {"question": "shi"}, {"question": "fake"},
                       {"question": "old"}, {"question": "ship", "mail_contains": "no"}, {}):
        assert not matches(conditions, subjects=subjects)


def test_stand_ins_refuse_an_unknown_flag_and_codex_queue_is_kept(tmp_path, monkeypatch, stand_ins):
    monkeypatch.setenv("FOIL_SEAT_ID", "fixture")
    instructions = tmp_path / "run" / "instructions" / "fixture.md"
    for harness in STAND_INS:
        args = _args(harness, "Fixture prompt", instructions)
        refused = _invoke(stand_ins / harness, [*args, "--not-a-flag"], tmp_path)
        assert refused.returncode != 0 and "--not-a-flag" in refused.stderr
    monkeypatch.delenv("FOIL_SEAT_ID")
    monkeypatch.delenv("FOIL_MASTER", raising=False)
    codex = stand_ins / "codex"
    args = ["queue", "--thread", "ID", "--message", "hello", "--resume"]
    queued = _invoke(codex, args, tmp_path)
    assert queued.returncode == 0 and queued.stdout == ""
    path = Path.home() / ".codex-wake.jsonl"
    assert recordings.session_rows(path) == [{"thread": "ID", "message": "hello"}]
    original = path.read_bytes()
    assert _invoke(codex, args[:-1], tmp_path).returncode != 0
    assert _invoke(codex, _args("codex", "Fixture prompt", instructions), tmp_path).returncode != 0
    assert path.read_bytes() == original
    assert list(Path.home().rglob("*")) == [path] and not (tmp_path / "run").exists()


def test_capture_exports_only_a_complete_sanitized_session(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    destination = tmp_path / "golden" / recordings.SCENARIO
    for turn in recordings.TURNS:
        args = _args("agy", "Private fixture prompt", tmp_path,
                     resume=SESSION if turn == "resume" else None)
        (raw / f"{turn}.argv.json").write_text(json.dumps(args))
        events, _ = _expected("agy", SESSION, turn)
        (raw / f"{turn}.stdout").write_text("\n".join(map(json.dumps, events)) + "\nraw words\n")
    live_capture.export("agy", raw, destination, session=SESSION, cwd=str(tmp_path),
                        cli_version="1.0.0")
    assert recordings.latest(destination.parent, "agy") == destination
    articles = {path: path.read_bytes() for path in destination.rglob("*") if path.is_file()}
    assert not any(re.search(rb"Private fixture prompt|raw words|000000000001", data)
                   for data in articles.values())
    (raw / "resume.stdout").write_text('{"unexpected":"private text"}\n')
    with pytest.raises(AssertionError, match="unrecognized field"):
        live_capture.export("agy", raw, destination, session=SESSION, cwd=str(tmp_path),
                            cli_version="1.0.0")
    assert {path: path.read_bytes() for path in articles} == articles
