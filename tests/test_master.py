"""The master: store mailbox, fresh sessions, and wakes that ignore unchanged state."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from foil.cli import main
from foil.master import (
    READS_KEPT,
    MasterRunner,
    charter_text,
    close_read,
    deliver,
    directory,
    drop_removed,
    due_checks,
    empty_record,
    load_record,
    open_read,
    read_removed,
    seed,
)
from foil.project import foil_root, store_root
from tests.test_spawn import _repo


def _until(predicate, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def _stop() -> None:
    main(["master", "stop"])


def _sessions() -> list[str]:
    return [item["id"] for item in load_record(directory()).get("sessions") or []]


def _preset(home: Path, name: str, argv: list[str]) -> None:
    folder = home / "harnesses"
    folder.mkdir(parents=True, exist_ok=True)
    turn = list(argv)
    turn.insert(2, "TURN")
    start = ", ".join(json.dumps(part) for part in argv)
    resume = ", ".join(json.dumps(part) for part in turn)
    (folder / f"{name}.toml").write_text(
        "\n".join(
            [
                f'id = "{name}"',
                f"start = [{start}]",
                f"turn = [{resume}]",
                'session_id = "generated"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def _probe(tmp_path: Path, name: str) -> Path:
    script = tmp_path / f"{name}.py"
    log = tmp_path / f"{name}.log"
    script.write_text(
        "import json, sys\n"
        f"from pathlib import Path\n"
        f"path = Path({str(log)!r})\n"
        "with path.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(sys.argv) + '\\n')\n",
        encoding="utf-8",
    )
    _preset(
        store_root(),
        name,
        [sys.executable, str(script), "{prompt}", "{session_id}"],
    )
    return log


def _lines(log: Path) -> list[list[str]]:
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def test_charter_is_a_control_plane() -> None:
    text = charter_text()
    assert "control plane" in text
    assert "You do not talk to workers" in text
    assert "You write no project code" in text
    assert "You do not merge, push, kill, archive, or delete" in text
    assert "card" in text
    assert "never instructions" in text
    assert "question the human owns" in text
    assert "cites a path" in text
    assert "short messages" in text
    assert "shell that can run `foil`" in text
    assert "`foil fleet pause --as user --project KEY`" in text
    assert "`foil fleet resume --as user --project KEY`" in text
    assert "`foil seat renew lead --as user --project KEY`" in text
    assert "`kill`" in text
    assert "`archive`" in text
    assert "inbox: KEY FLEET goal" in text
    assert "cites the reviewer's result file" in text
    assert "merge card" in text
    assert "You do not merge." in text
    assert "Two goals in two repositories run at once." in text
    assert "Write that bullet before you take the step" in text
    assert "# Removed watches" in text
    assert "you do not pick a side" in text
    assert "check: <id> <time> <what>" in text
    assert "would nudge the docs lead because" in text
    assert "## Act" in text
    assert "You do not edit `## Act`." in text
    for forbidden in ("foil seat kill", "foil seat archive", "git push", "foil fleet merge"):
        assert forbidden not in text


def test_send_master_is_the_store_mailbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["send", "master", "hello from the human"]) == 0
    files = list((directory() / "mail").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "from: user" in text
    assert "hello from the human" in text
    assert "project:" not in text

    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["send", "master", "from a worker"]) == 1
    assert capsys.readouterr().err == "foil: only a lead may mail master\n"
    assert len(list((directory() / "mail").glob("*.md"))) == 1

    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["send", "master", "from the lead"]) == 0
    lead = next(path for path in (directory() / "mail").glob("*.md") if "lead" in path.name)
    body = lead.read_text(encoding="utf-8")
    assert "from: lead" in body
    assert f"project: {foil_root(repo).name}" in body
    assert "fleet: main" in body

    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["master", "start"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"


def test_seed_includes_the_charter_and_cuts_a_long_log(tmp_path: Path) -> None:
    root = directory()
    (root / "mail").mkdir(parents=True)
    (root / "log.md").write_text("x" * (9 * 1024), encoding="utf-8")
    mailed = deliver("a wake", None)
    text = seed(root, [mailed], ["proj\x1ffleet\x1fquestion:one"])
    assert "# Charter" in text
    assert "control plane" in text
    assert "# Brief" in text
    assert "# Mandate" in text
    assert "a wake" in text
    assert "... truncated" in text
    assert "inbox: proj fleet question:one" in text
    assert "mail: " in text
    assert "# Removed watches" in text
    assert "(none)" in text


def test_start_stop_and_renew(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    try:
        assert main(["master", "start"]) == 0
        assert "foil: master started (claude)" in capsys.readouterr().out
        _until(lambda: load_record(directory())["baselined"])
        assert main(["master", "start"]) == 1
        assert capsys.readouterr().err == "foil: master is running\n"
        _probe(tmp_path, "probe")
        assert main(["master", "renew", "--harness", "probe"]) == 0
        assert capsys.readouterr().out == "foil: master harness probe\n"
        plan = json.loads((directory() / "run" / "plan.json").read_text(encoding="utf-8"))
        assert plan["kind"] == "master"
        assert "TURN" not in plan["start"]
        assert plan["harness"] == "probe"
        assert main(["master", "stop"]) == 0
        assert capsys.readouterr().out == "foil: master stopped\n"
        assert main(["master", "stop"]) == 1
        assert capsys.readouterr().err == "foil: master is not running\n"
    finally:
        _stop()


def test_wakes_on_mail_and_a_new_inbox_id_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    capsys.readouterr()
    log = _probe(tmp_path, "probe")
    try:
        assert main(["master", "start", "--harness", "probe"]) == 0
        _until(lambda: load_record(directory())["baselined"])
        assert _sessions() == []
        assert _lines(log) == []
        time.sleep(2.2)
        assert _sessions() == []

        assert main(["send", "master", "human mail"]) == 0
        _until(lambda: len(_lines(log)) == 1 and len(_sessions()) == 1)
        time.sleep(2.2)
        assert len(_sessions()) == 1
        first = _lines(log)
        prompt = first[0][1]
        assert "human mail" in prompt
        assert "control plane" in prompt
        assert "TURN" not in first[0]
        assert first[0][-1] == _sessions()[0]

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
        assert main(["send", "master", "second human"]) == 0
        _until(lambda: len(_lines(log)) == 2 and len(_sessions()) == 2)
        assert _sessions()[0] != _sessions()[1]
        assert "TURN" not in _lines(log)[1]

        board = foil_root(repo) / "fleets" / "main" / "board"
        board.mkdir(parents=True, exist_ok=True)
        (board / "status.md").write_text(
            "---\ncontract: status/v1\nstate: blocked\nquestions:\n"
            "  - id: one\n    ask: First?\n    owner: human\n---\n"
            "## Mandate\nDone when: it.\n",
            encoding="utf-8",
        )
        _until(lambda: len(_lines(log)) == 3 and len(_sessions()) == 3)
        time.sleep(2.2)
        assert len(_sessions()) == 3
        assert "question:one" in _lines(log)[2][1]
        (board / "status.md").write_text(
            "---\ncontract: status/v1\nstate: blocked\nquestions:\n"
            "  - id: one\n    ask: First?\n    owner: human\n---\n"
            "## Mandate\nDone when: it.\n",
            encoding="utf-8",
        )
        time.sleep(2.2)
        assert len(_sessions()) == 3
        (board / "status.md").write_text(
            "---\ncontract: status/v1\nstate: blocked\nquestions:\n"
            "  - id: two\n    ask: Second?\n    owner: human\n---\n"
            "## Mandate\nDone when: it.\n",
            encoding="utf-8",
        )
        _until(lambda: len(_lines(log)) == 4 and len(_sessions()) == 4)
        assert _sessions()[2] != _sessions()[3]
        assert "question:two" in _lines(log)[3][1]
    finally:
        _stop()


def test_a_failed_turn_pauses_after_the_waits(tmp_path: Path) -> None:
    root = directory()
    deliver("try again", None)
    script = tmp_path / "fail.py"
    script.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    plan = {
        "kind": "master",
        "start": [sys.executable, str(script), "{prompt}", "{session_id}"],
        "session_key": "",
        "cwd": str(root.resolve()),
        "env": {"FOIL_MASTER": "1"},
        "env_forward": [],
        "harness": "probe",
        "model": "",
        "turn_timeout": 30,
        "retry": [0, 0, 0],
        "root": str(root.resolve()),
    }
    path = root / "run" / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan), encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-P", "-m", "foil.runner", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        _until(lambda: load_record(root)["paused"])
        assert len(_sessions()) == 4
        assert len(set(_sessions())) == 4
        (root / "run" / "resume").write_bytes(b"")
        _until(lambda: len(_sessions()) > 4)
    finally:
        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)


def _status(board: Path, question: str, ask: str) -> None:
    board.mkdir(parents=True, exist_ok=True)
    (board / "status.md").write_text(
        "---\ncontract: status/v1\nstate: blocked\nquestions:\n"
        f"  - id: {question}\n    ask: {ask}\n    owner: human\n---\n"
        "## Mandate\nDone when: it.\n",
        encoding="utf-8",
    )


def test_a_failed_inbox_read_does_not_drop_seen_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    board = foil_root(repo) / "fleets" / "main" / "board"
    _status(board, "one", "First?")
    root = directory()
    script = tmp_path / "ok.py"
    log = tmp_path / "ok.log"
    script.write_text(
        "import json, sys\n"
        f"from pathlib import Path\n"
        f"Path({str(log)!r}).write_text(json.dumps(sys.argv), encoding='utf-8')\n",
        encoding="utf-8",
    )
    plan = {
        "kind": "master",
        "start": [sys.executable, str(script), "{prompt}", "{session_id}"],
        "session_key": "",
        "cwd": str(root.resolve()),
        "env": {"FOIL_MASTER": "1"},
        "env_forward": [],
        "harness": "probe",
        "model": "",
        "turn_timeout": 30,
        "retry": [0, 0, 0],
        "root": str(root.resolve()),
    }
    path = root / "run" / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan), encoding="utf-8")
    import foil.master as master

    real = master.identities
    state = {"fail": False, "saw_failure": False, "changed": False}

    def wrapped():
        if state["fail"]:
            state["fail"] = False
            state["saw_failure"] = True
            return None
        return real()

    def do_sleep(seconds: float) -> None:
        if seconds < 1:
            return
        record = load_record(root)
        if record["baselined"] and not state["saw_failure"]:
            assert any(item.endswith("\x1fquestion:one") for item in record["seen_inbox"])
            state["fail"] = True
            return
        if state["saw_failure"] and not state["changed"] and not _sessions():
            assert any(item.endswith("\x1fquestion:one") for item in record["seen_inbox"])
            _status(board, "two", "Second?")
            state["changed"] = True
            return
        if state["changed"] and _sessions():
            raise SystemExit(0)

    monkeypatch.setattr(master, "identities", wrapped)
    monkeypatch.setattr(master.time, "sleep", do_sleep)
    with pytest.raises(SystemExit):
        MasterRunner(path).loop()
    assert len(_sessions()) == 1
    prompt = json.loads(log.read_text(encoding="utf-8"))[1]
    assert "question:two" in prompt
    assert "question:one" not in prompt.split("# Wake", 1)[1]


def test_reads_keep_the_last_32() -> None:
    record = empty_record()
    for index in range(READS_KEPT + 8):
        open_read(record, [f"{index}.md"])
        close_read(record, [])
    assert len(record["reads"]) == READS_KEPT
    assert record["reads"][0]["names"] == ["8.md"]
    assert record["reads"][-1]["names"] == [f"{READS_KEPT + 7}.md"]
    assert all(item["open"] is False for item in record["reads"])


def test_a_turn_records_the_mail_it_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    root = directory()
    mail = root / "mail"
    script = tmp_path / "reply.py"
    log = tmp_path / "wrote.txt"
    script.write_text(
        "\n".join(
            [
                "import sys",
                "from pathlib import Path",
                f"folder = Path({str(mail)!r})",
                f"log = Path({str(log)!r})",
                "users = list(folder.glob('*-user-*.md')) if folder.is_dir() else []",
                "if not users:",
                "    sys.exit(0)",
                "names = []",
                "for index, body in enumerate(('Ship it.', 'Also this.')):",
                "    name = f'20261123T15010{index}Z-master-{index:08x}.md'",
                "    text = (",
                "        '---\\ncontract: mail/v1\\nfrom: master\\nto: master\\n'",
                "        'time: 2026-11-23T15:01:00Z\\n---\\n\\n' + body + '\\n'",
                "    )",
                "    (folder / name).write_text(text, encoding='utf-8')",
                "    names.append(name)",
                "log.write_text('\\n'.join(names), encoding='utf-8')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    plan = {
        "kind": "master",
        "start": [sys.executable, str(script), "{prompt}", "{session_id}"],
        "session_key": "",
        "cwd": str(root.resolve()),
        "env": {"FOIL_MASTER": "1"},
        "env_forward": [],
        "harness": "probe",
        "model": "",
        "turn_timeout": 30,
        "retry": [0, 0, 0],
        "root": str(root.resolve()),
    }
    path = root / "run" / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan), encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-P", "-m", "foil.runner", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        _until(lambda: load_record(root)["baselined"])
        mail.mkdir(parents=True, exist_ok=True)
        planted = "20261123T140000Z-master-cccc3333.md"
        (mail / planted).write_text(
            "---\ncontract: mail/v1\nfrom: master\nto: master\n"
            "time: 2026-11-23T14:00:00Z\n---\n\nfrom the previous turn\n",
            encoding="utf-8",
        )
        written = deliver("hello from the human", None)

        def ready() -> bool:
            record = load_record(root)
            return any(
                not item["open"] and written.name in item["names"] and item["wrote"]
                for item in record["reads"]
            )

        _until(ready)
        record = load_record(root)
        turn = next(
            item
            for item in record["reads"]
            if written.name in item["names"] and not item["open"]
        )
        assert turn["wrote"] == log.read_text(encoding="utf-8").splitlines()
        assert planted not in turn["wrote"]
        assert written.name in record["delivered"]
        assert set(turn["wrote"]) <= set(record["delivered"])
        time.sleep(2.2)
        later = load_record(root)
        names = {name for item in later["reads"] for name in item["names"]}
        assert set(turn["wrote"]).isdisjoint(names)
    finally:
        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)


def test_a_failed_attempt_keeps_its_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    root = directory()
    mail = root / "mail"
    script = tmp_path / "retry.py"
    log = tmp_path / "wrote.txt"
    attempts = tmp_path / "attempts"
    script.write_text(
        "\n".join(
            [
                "import sys",
                "from pathlib import Path",
                f"folder = Path({str(mail)!r})",
                f"log = Path({str(log)!r})",
                f"attempts = Path({str(attempts)!r})",
                "count = int(attempts.read_text(encoding='utf-8')) if attempts.is_file() else 0",
                "attempts.write_text(str(count + 1), encoding='utf-8')",
                "if count:",
                "    sys.exit(0)",
                "name = '20261123T150100Z-master-aaaa1111.md'",
                "folder.mkdir(parents=True, exist_ok=True)",
                "text = (",
                "    '---\\ncontract: mail/v1\\nfrom: master\\nto: master\\n'",
                "    'time: 2026-11-23T15:01:00Z\\n---\\n\\nShip it.\\n'",
                ")",
                "(folder / name).write_text(text, encoding='utf-8')",
                "log.write_text(name, encoding='utf-8')",
                "sys.exit(1)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    plan = {
        "kind": "master",
        "start": [sys.executable, str(script), "{prompt}", "{session_id}"],
        "session_key": "",
        "cwd": str(root.resolve()),
        "env": {"FOIL_MASTER": "1"},
        "env_forward": [],
        "harness": "probe",
        "model": "",
        "turn_timeout": 30,
        "retry": [0, 0, 0],
        "root": str(root.resolve()),
    }
    path = root / "run" / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan), encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-P", "-m", "foil.runner", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        _until(lambda: load_record(root)["baselined"])
        written = deliver("hello from the human", None)

        def ready() -> bool:
            record = load_record(root)
            turns = [
                item
                for item in record["reads"]
                if written.name in item["names"] and not item["open"]
            ]
            return len(turns) >= 2 and written.name in record["delivered"]

        _until(ready)
        record = load_record(root)
        turns = [item for item in record["reads"] if written.name in item["names"]]
        reply = log.read_text(encoding="utf-8").strip()
        assert [item["wrote"] for item in turns] == [[reply], []]
        assert all(reply not in item["names"] for item in turns)
        assert reply in record["delivered"]
        time.sleep(2.2)
        names = {name for item in load_record(root)["reads"] for name in item["names"]}
        assert reply not in names
    finally:
        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)


def test_unwatch_records_the_line_and_leaves_a_later_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root = directory()
    (root / "run").mkdir(parents=True)
    (root / "watches.md").write_text(
        "- ci 2026-11-23T12:00:00Z password=hunter2\n"
        "keep this line\n"
        "- ci 2026-11-23T13:00:00Z again\n"
        "- next 2026-11-23T14:00:00Z the other one\n",
        encoding="utf-8",
    )
    (root / "run" / "removed.json").write_text("not json\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["master", "unwatch", "ci"]) == 0
    assert capsys.readouterr().out == "foil: removed watch ci\n"
    assert (root / "watches.md").read_text(encoding="utf-8") == (
        "keep this line\n- next 2026-11-23T14:00:00Z the other one\n"
    )
    removed = json.loads((root / "run" / "removed.json").read_text(encoding="utf-8"))
    assert removed == [
        {"check": "2026-11-23T12:00:00Z", "id": "ci", "on": "password=hunter2"}
    ]
    assert main(["master", "unwatch", "next"]) == 0
    drop_removed(root, ["ci"])
    assert [item["id"] for item in read_removed(root)] == ["next"]
    assert main(["master", "unwatch", "missing"]) == 1
    assert capsys.readouterr().err == "foil: unknown watch 'missing'\n"
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["master", "unwatch", "ci"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"
    assert repo.is_dir()


def test_unwatch_does_not_follow_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    root = directory()
    root.mkdir(parents=True)
    target = tmp_path / "elsewhere.md"
    target.write_text("- ci 2026-11-23T12:00:00Z the review\n", encoding="utf-8")
    (root / "watches.md").symlink_to(target)
    assert main(["master", "unwatch", "ci"]) == 1
    assert capsys.readouterr().err == "foil: refusing symlink\n"
    assert "the review" in target.read_text(encoding="utf-8")


def test_a_recorded_removal_wakes_the_first_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    root = directory()
    root.mkdir(parents=True)
    (root / "watches.md").write_text(
        "- ci 2026-11-23T12:00:00Z the review\n", encoding="utf-8"
    )
    assert main(["master", "unwatch", "ci"]) == 0
    capsys.readouterr()
    log = _probe(tmp_path, "probe")
    try:
        assert main(["master", "start", "--harness", "probe"]) == 0
        _until(lambda: len(_lines(log)) == 1)
        assert "- ci 2026-11-23T12:00:00Z the review" in _lines(log)[0][1]
        _until(lambda: read_removed(directory()) == [])
    finally:
        _stop()


def test_removing_a_watch_is_in_the_next_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    root = directory()
    root.mkdir(parents=True)
    (root / "watches.md").write_text(
        "- ci 2099-01-01T00:00:00Z the review\n", encoding="utf-8"
    )
    log = _probe(tmp_path, "probe")
    try:
        assert main(["master", "start", "--harness", "probe"]) == 0
        _until(lambda: load_record(directory())["baselined"])
        assert _lines(log) == []
        capsys.readouterr()
        assert main(["master", "unwatch", "ci"]) == 0
        _until(lambda: len(_lines(log)) == 1)
        prompt = _lines(log)[0][1]
        assert "# Removed watches" in prompt
        assert "- ci 2099-01-01T00:00:00Z the review" in prompt
        _until(lambda: read_removed(directory()) == [])
        assert "ci" not in (directory() / "watches.md").read_text(encoding="utf-8")
    finally:
        _stop()


def test_the_log_shows_the_next_step_before_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foil_bridge.home import snapshot

    monkeypatch.chdir(tmp_path)
    root = directory()
    root.mkdir(parents=True)
    marker = tmp_path / "marker"
    script = tmp_path / "step.py"
    logged = tmp_path / "step.log"
    script.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        f"Path({str(logged)!r}).write_text(json.dumps(sys.argv), encoding='utf-8')\n"
        f"Path({str(root / 'log.md')!r}).write_text(\n"
        "    '## demo main\\n### Next\\n- record the choice\\n',\n"
        "    encoding='utf-8',\n"
        ")\n"
        "time.sleep(3)\n"
        f"Path({str(marker)!r}).write_text('done\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    _preset(store_root(), "step", [sys.executable, str(script), "{prompt}", "{session_id}"])
    try:
        assert main(["master", "start", "--harness", "step"]) == 0
        _until(lambda: load_record(directory())["baselined"])
        assert main(["send", "master", "go"]) == 0

        def shown() -> bool:
            rows = snapshot()["log"]["fleets"]
            return any("record the choice" in item for row in rows for item in row["next"])

        _until(shown)
        assert not marker.exists()
        _until(marker.exists)
    finally:
        _stop()


def test_a_future_watch_does_not_start_a_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    root = directory()
    root.mkdir(parents=True)
    (root / "watches.md").write_text(
        "- ci 2099-01-01T00:00:00Z the review\n", encoding="utf-8"
    )
    log = _probe(tmp_path, "probe")
    try:
        assert main(["master", "start", "--harness", "probe"]) == 0
        _until(lambda: load_record(directory())["baselined"])
        time.sleep(2.2)
        assert _lines(log) == []
        assert due_checks(directory()) == []
    finally:
        _stop()


def test_a_due_check_starts_one_turn_and_a_new_time_starts_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    root = directory()
    root.mkdir(parents=True)
    (root / "watches.md").write_text(
        "- ci 2020-01-01T00:00:00Z the review\n", encoding="utf-8"
    )
    log = _probe(tmp_path, "probe")
    try:
        assert main(["master", "start", "--harness", "probe"]) == 0
        _until(lambda: len(_lines(log)) == 1)
        assert "check: ci 2020-01-01T00:00:00Z the review" in _lines(log)[0][1]
        time.sleep(2.2)
        assert len(_lines(log)) == 1
        assert "ci\x1f2020-01-01T00:00:00Z" in load_record(directory())["seen_checks"]
        (directory() / "watches.md").write_text(
            "- ci 2020-01-02T00:00:00Z the review\n", encoding="utf-8"
        )
        _until(lambda: len(_lines(log)) == 2)
        assert "check: ci 2020-01-02T00:00:00Z the review" in _lines(log)[1][1]
    finally:
        _stop()


def test_observe_refuses_until_that_kind_acts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from foil.delivery import fleet_paused
    from foil.store import fleet_dir

    repo = _repo(tmp_path, monkeypatch)
    registry = foil_root(repo) / "fleets" / "main" / "run" / "registry.json"
    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload["seats"] = {
        "lead": {
            "name": "lead",
            "template": "lead",
            "harness": "fake",
            "window_id": "",
            "state": "dead",
            "worktree": "",
            "branch": "",
            "session_id": "",
        }
    }
    registry.write_text(json.dumps(payload), encoding="utf-8")
    root = directory()
    root.mkdir(parents=True)
    (root / "mandate.md").write_text("Be careful.\n\n## Notes\nstay\n", encoding="utf-8")
    fleet = fleet_dir(repo)
    assert main(["fleet", "pause"]) == 0
    assert main(["fleet", "resume"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("FOIL_MASTER", "1")
    assert main(["fleet", "pause"]) == 1
    assert capsys.readouterr().err == "foil: observe pause\n"
    assert not fleet_paused(fleet)
    assert main(["master", "mode", "pause", "act"]) == 0
    assert capsys.readouterr().out == "foil: pause act\n"
    mandate = (root / "mandate.md").read_text(encoding="utf-8")
    assert mandate.startswith("Be careful.\n")
    assert "## Notes\nstay\n" in mandate
    assert "- pause\n" in mandate
    assert main(["fleet", "pause"]) == 0
    assert fleet_paused(fleet)
    capsys.readouterr()
    assert main(["fleet", "resume"]) == 1
    assert capsys.readouterr().err == "foil: observe resume\n"
    assert fleet_paused(fleet)
    assert main(["master", "mode", "resume", "act"]) == 0
    capsys.readouterr()
    assert main(["fleet", "resume"]) == 0
    assert not fleet_paused(fleet)
    assert main(["send", "lead", "look at the docs"]) == 1
    assert capsys.readouterr().err == "foil: observe nudge\n"
    mail = fleet / "board" / "mail" / "lead"
    assert not mail.exists() or list(mail.glob("*.md")) == []
    assert main(["master", "mode", "nudge", "act"]) == 0
    capsys.readouterr()
    assert main(["send", "lead", "look at the docs"]) == 0
    assert list(mail.glob("*.md"))
    assert main(["seat", "renew", "lead"]) == 1
    assert capsys.readouterr().err == "foil: observe renew\n"
    assert main(["master", "mode", "renew", "act"]) == 0
    capsys.readouterr()
    assert main(["seat", "renew", "lead"]) == 1
    assert capsys.readouterr().err == "foil: write board/handoffs/lead.md first\n"
    mandate = (root / "mandate.md").read_text(encoding="utf-8")
    assert "Be careful." in mandate and "## Notes\nstay" in mandate


def test_mode_refuses_an_unknown_value_a_symlink_and_a_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    assert main(["master", "mode", "nudge", "maybe"]) == 1
    assert capsys.readouterr().err == "foil: unknown mode 'maybe'\n"
    root = directory()
    root.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "words.md"
    target.write_text("Be careful.\n", encoding="utf-8")
    mandate = root / "mandate.md"
    if mandate.exists() or mandate.is_symlink():
        mandate.unlink()
    mandate.symlink_to(target)
    assert main(["master", "mode", "nudge", "act"]) == 1
    assert capsys.readouterr().err == "foil: refusing symlink\n"
    assert target.read_text(encoding="utf-8") == "Be careful.\n"
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["master", "mode", "nudge", "act"]) == 1
    assert capsys.readouterr().err == "foil: not allowed\n"
    assert repo.is_dir()
