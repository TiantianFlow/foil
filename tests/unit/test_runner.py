"""The runner: plan validation, prompts, its record, and exec-time environment."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from foil.board import items_for
from foil.runner import (
    INLINE,
    INLINE_TOTAL,
    LISTED,
    RESTARTED,
    RunnerError,
    load_plan,
    load_record,
    record_path,
    wake_prompt,
)


def _plan(tmp_path: Path, **changes: object) -> dict:
    board = tmp_path / "fleet" / "board"
    (board / "mail" / "seat-1").mkdir(parents=True, exist_ok=True)
    instruction = tmp_path / "fleet" / "run" / "instructions" / "seat-1.md"
    instruction.parent.mkdir(parents=True, exist_ok=True)
    instruction.write_text("Do the task.\n", encoding="utf-8")
    plan = {
        "start": [sys.executable, str(tmp_path / "probe.py"), "start", "{prompt}"],
        "turn": [sys.executable, str(tmp_path / "probe.py"), "turn", "{prompt}"],
        "session_key": "",
        "session_id": "s-1",
        "cwd": str(tmp_path),
        "env": {"FOIL_SEAT_ID": "seat-1"},
        "env_forward": [],
        "instruction": str(instruction),
        "board": str(board),
        "seat": "seat-1",
        "reader": "lead",
        "worker": False,
        "turn_timeout": 60,
        "retry": [0.1, 0.1, 0.1],
        "first": "start",
        "restarted": False,
    }
    plan.update(changes)
    return plan


def _write(tmp_path: Path, plan: dict) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def _run(tmp_path: Path, plan: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-P", "-m", "foil.runner", str(_write(tmp_path, plan))],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _until(predicate, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_a_plan_with_exactly_the_runner_fields_loads(tmp_path: Path) -> None:
    loaded = load_plan(_write(tmp_path, _plan(tmp_path, env_forward=["FOIL_FORWARD_ME"])))
    assert loaded["env_forward"] == ["FOIL_FORWARD_ME"]


@pytest.mark.parametrize(
    "change",
    [
        {"secrets": {"token": "sk-nope"}},
        {"env_forward": ["NAME=WITH-VALUE"]},
        {"start": ["claude", "-p"]},
        {"first": "resume"},
        {"turn_timeout": 0},
        {"retry": [-1]},
        {"seat": "../lead"},
        {"cwd": "relative"},
    ],
)
def test_a_plan_with_a_bad_field_is_refused(tmp_path: Path, change: dict) -> None:
    with pytest.raises(RunnerError):
        load_plan(_write(tmp_path, _plan(tmp_path, **change)))


def test_a_symlinked_plan_is_refused(tmp_path: Path) -> None:
    real = _write(tmp_path, _plan(tmp_path))
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(RunnerError, match="symlink"):
        load_plan(link)


def test_the_wake_prompt_inlines_short_mail_and_names_the_rest(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    board = Path(plan["board"])
    short = board / "mail" / "seat-1" / "a.md"
    long = board / "mail" / "seat-1" / "b.md"
    short.write_text("---\nfrom: lead\n---\n" + "a" * (INLINE - 1) + "\n", encoding="utf-8")
    long.write_text("---\nfrom: lead\n---\n" + "b" * INLINE + "\n", encoding="utf-8")
    items = [
        {"time": "t1", "from": "lead", "path": str(short), "relative": "mail/seat-1/a.md"},
        {"time": "t2", "from": "lead", "path": str(long), "relative": "mail/seat-1/b.md"},
    ]
    prompt = wake_prompt(plan, items, restarted=True)
    lines = prompt.splitlines()
    assert lines[0] == RESTARTED
    assert lines[1] == "New for seat-1 (2):"
    assert lines[2:4] == [f"t1 lead {short}", f"t2 lead {long}"]
    assert f"--- {short}" in prompt and f"--- {long}" not in prompt
    assert lines[-1].startswith(f"Re-read {plan['instruction']} before you act. Read each item")
    assert wake_prompt(plan, [], restarted=False) == (
        f"Nothing new for seat-1.\nRe-read {plan['instruction']} before you act.\n"
    )


def test_a_damaged_record_loads_as_defaults(tmp_path: Path) -> None:
    board = tmp_path / "board"
    path = record_path(board, "seat-1")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"delivered": "x", "failures": "2", "last_exit": True}))
    record = load_record(board, "seat-1")
    assert record["delivered"] == [] and record["failures"] == 0 and record["last_exit"] is None


def test_forwarded_values_are_read_at_exec_time_and_stdin_is_empty(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    (tmp_path / "probe.py").write_text(
        "import json, os, sys\n"
        f"open({str(out)!r}, 'w').write(json.dumps([os.environ.get('FOIL_FORWARD_ME', 'absent'),"
        " os.environ.get('FOIL_FORWARD_MISSING', 'absent'), sys.stdin.read(), sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    plan = _plan(tmp_path, env_forward=["FOIL_FORWARD_ME", "FOIL_FORWARD_MISSING"])
    os.environ["FOIL_FORWARD_ME"] = "forwarded"
    try:
        runner = _run(tmp_path, plan)
    finally:
        del os.environ["FOIL_FORWARD_ME"]
    try:
        _until(out.is_file)
        _until(lambda: out.read_text() != "")
        forwarded, missing, stdin, argv = json.loads(out.read_text())
        assert (forwarded, missing, stdin) == ("forwarded", "absent", "")
        assert argv == ["start", "Do the task.\n"]
    finally:
        os.killpg(runner.pid, signal.SIGKILL)
        runner.wait()


def test_a_turn_killed_by_a_signal_records_128_plus_n(tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text(
        "import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n", encoding="utf-8"
    )
    runner = _run(tmp_path, _plan(tmp_path, retry=[30]))
    try:
        board = Path(_plan(tmp_path)["board"])
        _until(lambda: load_record(board, "seat-1")["last_exit"] is not None)
        record = load_record(board, "seat-1")
        assert record["last_exit"] == 128 + signal.SIGTERM and record["failures"] == 1
    finally:
        os.killpg(runner.pid, signal.SIGKILL)
        runner.wait()


def test_stopping_the_runner_ends_the_turn_and_its_children(tmp_path: Path) -> None:
    child = tmp_path / "child.pid"
    (tmp_path / "probe.py").write_text(
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
        f"open({str(child)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(600)\n",
        encoding="utf-8",
    )
    runner = _run(tmp_path, _plan(tmp_path))
    _until(lambda: child.is_file() and child.read_text() != "")
    pid = int(child.read_text())
    runner.send_signal(signal.SIGTERM)
    runner.wait(timeout=20)

    def gone() -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        status = Path(f"/proc/{pid}/status")
        return status.is_file() and "State:\tZ" in status.read_text()

    _until(gone)


def test_the_wake_prompt_lists_at_most_50_items_and_inlines_at_most_16_kib(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    mailbox = Path(plan["board"]) / "mail" / "seat-1"
    items = []
    for number in range(LISTED + 10):
        path = mailbox / f"{number:03}.md"
        path.write_text("---\nfrom: lead\n---\n" + "x" * 500 + "\n", encoding="utf-8")
        relative = f"mail/seat-1/{path.name}"
        items.append(
            {"time": f"t{number}", "from": "lead", "path": str(path), "relative": relative}
        )
    prompt = wake_prompt(plan, items, restarted=False)
    assert prompt.count(" lead /") == LISTED
    assert "...and 10 more: run foil mail list" in prompt
    assert prompt.count("--- ") == INLINE_TOTAL // 501
    assert len(prompt.encode()) < 64 * 1024


def test_a_failed_start_runs_start_again_on_a_fresh_generated_id(tmp_path: Path) -> None:
    log = tmp_path / "starts.log"
    (tmp_path / "probe.py").write_text(
        "import sys\n"
        f"log = open({str(log)!r}, 'a')\n"
        "log.write(sys.argv[2] + '\\n'); log.close()\n"
        f"sys.exit(1 if len(open({str(log)!r}).read().split()) == 1 else 0)\n",
        encoding="utf-8",
    )
    plan = _plan(tmp_path)
    probe = str(tmp_path / "probe.py")
    plan["start"] = [sys.executable, probe, "start", "{session_id}", "{prompt}"]
    runner = _run(tmp_path, plan)
    board = Path(plan["board"])
    try:
        _until(lambda: load_record(board, "seat-1")["started"])
        first, second = log.read_text().split()
        assert first == "s-1" and second != first
        assert load_record(board, "seat-1")["session_id"] == second
    finally:
        os.killpg(runner.pid, signal.SIGKILL)
        runner.wait()


def test_a_program_that_cannot_run_is_a_failed_turn_and_pauses(tmp_path: Path) -> None:
    plan = _plan(tmp_path, start=[str(tmp_path / "missing"), "{prompt}"])
    runner = _run(tmp_path, plan)
    board = Path(plan["board"])
    try:
        _until(lambda: load_record(board, "seat-1")["paused"])
        record = load_record(board, "seat-1")
        assert record["last_exit"] == 127 and record["failures"] == 4
        assert runner.poll() is None
        (notice,) = (board / "mail" / "lead").glob("*-foil-*.md")
        assert "seat-1 paused after 4 failed turns (exit 127)" in notice.read_text()
    finally:
        os.killpg(runner.pid, signal.SIGKILL)
        runner.wait()


def test_a_room_file_that_claims_to_be_from_foil_wakes_nobody(tmp_path: Path) -> None:
    board = Path(_plan(tmp_path)["board"])
    room = board / "room" / ("a" * 16)
    room.mkdir(parents=True)
    for sender in ("foil", "lead"):
        (room / f"{'a' * 16}.md").write_text(
            f"---\ncontract: room/v2\nfrom: {sender}\ntime: 2026-11-10T12:00:00Z\n"
            "wakes: [seat-1]\n---\nHello.\n",
            encoding="utf-8",
        )
        found = [item["from"] for item in items_for(board, "seat-1", set())]
        assert found == ([] if sender == "foil" else ["lead"])


def test_an_output_session_id_is_saved_before_the_turn_ends(tmp_path: Path) -> None:
    """An output preset's id is durable even if the runner dies mid-turn."""

    (tmp_path / "probe.py").write_text(
        "import json, sys, time\n"
        "print(json.dumps({'thread_id': 'learned-1'}), flush=True)\n"
        "time.sleep(600)\n",
        encoding="utf-8",
    )
    plan = _plan(
        tmp_path,
        start=[sys.executable, str(tmp_path / "probe.py"), "{prompt}"],
        turn=[sys.executable, str(tmp_path / "probe.py"), "{session_id}", "{prompt}"],
        session_key="thread_id",
        session_id="",
        retry=[30],
    )
    board = Path(plan["board"])
    runner = _run(tmp_path, plan)
    try:
        _until(lambda: load_record(board, "seat-1")["session_id"] == "learned-1")
        assert load_record(board, "seat-1")["started"] is True
        os.killpg(runner.pid, signal.SIGKILL)
        runner.wait(timeout=20)
        record = load_record(board, "seat-1")
        assert record["session_id"] == "learned-1" and record["started"] is True
    finally:
        if runner.poll() is None:
            os.killpg(runner.pid, signal.SIGKILL)
            runner.wait()


def test_a_timeout_before_the_session_starts_keeps_the_batch(tmp_path: Path) -> None:
    from foil.board import write_mail_at
    from foil.runner import empty_record, save_record

    plan = _plan(tmp_path, turn_timeout=1, retry=[30], first="start")
    board = Path(plan["board"])
    mailed = write_mail_at(board, "seat-1", "task", "lead")
    relative = mailed.relative_to(board).as_posix()
    (tmp_path / "probe.py").write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
    seeded = empty_record()
    seeded["session_id"] = "s-1"
    seeded["inflight"] = [relative]
    save_record(board, "seat-1", seeded)
    runner = _run(tmp_path, plan)
    try:
        _until(lambda: load_record(board, "seat-1")["last_exit"] == -1)
        record = load_record(board, "seat-1")
        assert record["inflight"] == [relative]
        assert record["delivered"] == []
        assert record["started"] is False
        assert record["session_id"] != "s-1"  # fresh generated id for the retry
        notice = list((board / "mail" / "lead").glob("*-foil-*.md"))
        assert notice and "turn_timeout" in notice[0].read_text()
    finally:
        os.killpg(runner.pid, signal.SIGKILL)
        runner.wait()


def test_a_large_board_file_is_not_fully_read_when_scanning(tmp_path: Path) -> None:
    from foil.board import items_for
    from foil.board_ops import FRONT_SCAN, body_for_inline, front_fields

    plan = _plan(tmp_path)
    board = Path(plan["board"])
    mail = board / "mail" / "seat-1" / "20260101T000000Z-lead-deadbeef.md"
    header = (
        "---\ncontract: mail/v1\nfrom: lead\nto: seat-1\ntime: 2026-01-01T00:00:00Z\n---\n\n"
    )
    mail.write_bytes(header.encode() + b"x" * (FRONT_SCAN * 2))
    assert front_fields(mail) is not None
    assert body_for_inline(mail, INLINE) is None
    found = items_for(board, "seat-1", set())
    assert len(found) == 1
    prompt = wake_prompt(plan, found, restarted=False)
    assert f"--- {mail}" not in prompt


def test_delivered_paths_are_compacted_before_the_record_limit(tmp_path: Path) -> None:
    """A full board cannot push the runner record past its 1 MiB cap (N7)."""
    from foil.runner import empty_record, prune_delivered
    from foil.store import MAX_BYTES

    board = tmp_path / "board"
    mailbox = board / "mail" / "seat-1"
    mailbox.mkdir(parents=True)
    record = empty_record()
    # Many short existing paths: absent-prune keeps them; size-prune must drop the oldest.
    # Long paths so a few thousand exceed the 1 MiB record cap.
    pad = "n" * 120
    for number in range(12000):
        name = f"{number:05}-{pad}.md"
        relative = f"mail/seat-1/{name}"
        (mailbox / name).write_text("x\n", encoding="utf-8")
        record["delivered"].append(relative)
    before = len(record["delivered"])
    prune_delivered(board, record)
    encoded = (
        __import__("json").dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode()
    assert len(encoded) <= MAX_BYTES - 64 * 1024
    assert 0 < len(record["delivered"]) < before
    assert not record["delivered"][0].startswith("mail/seat-1/00000-")
